from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from concurrent.futures import Future
from functools import partial
from pathlib import Path
from typing import Literal
from weakref import proxy

import sounddevice
from pydantic import BaseModel, ConfigDict, Field, field_validator
from reccy.configuration import settings
from reccy.runtime import logging

from ..streamo.client import StreamoClient
from ..x18.music import MusicPlayer, X18MusicRouting
from . import models
from .closing import ClosingController
from .mixer import MixerSpec
from .recs import RecsClient

DEFAULT_FADE_SECONDS = 2.0
SHUTDOWN_CLEANUP_SECONDS = 5.0
POWEROFF_SECONDS = 3.0
USB_ROUTING_CHECK_SECONDS = 2.0
LOGGER = logging.get_logger(__name__)


class MusicState(BaseModel, frozen=True):
    mode: Literal['stopped', 'setup', 'record', 'teardown', 'transitioning', 'fault']
    error: str | None = None


class MusicConfig(BaseModel, frozen=True):
    fade_seconds: float = DEFAULT_FADE_SECONDS
    source_channels: list[int] = Field(default_factory=lambda: [17, 18])

    model_config = ConfigDict(frozen=True)

    @field_validator('fade_seconds')
    @classmethod
    def validate_fade_seconds(cls, value: float) -> float:
        if value <= 0:
            raise ValueError('fade_seconds must be positive')
        return value

    @field_validator('source_channels')
    @classmethod
    def validate_source_channels(cls, value: list[int]) -> list[int]:
        if (
            len(value) != 2
            or value[0] not in range(1, 18, 2)
            or value[1] != value[0] + 1
        ):
            raise ValueError(
                'source_channels must be an adjacent odd/even pair in 1-18'
            )
        return value


def poweroff_pi() -> None:
    subprocess.run(
        ['sudo', '-n', 'systemctl', 'poweroff'], check=True, timeout=POWEROFF_SECONDS
    )


class MusicController:
    def __init__(
        self,
        recs: RecsClient,
        player: MusicPlayer,
        routing: X18MusicRouting,
        config: MusicConfig,
        poweroff: Callable[[], None] = poweroff_pi,
        streamo: StreamoClient | None = None,
        streamo_restart: Callable[[], models.ActionResult] | None = None,
        closing_state_path: Path | None = None,
        setup: list[Path] | None = None,
        teardown: list[Path] | None = None,
    ) -> None:
        self.recs = recs
        self.player = player
        self.routing = routing
        self.config = config
        self.setup_audio = setup or []
        self.teardown_audio = teardown or []
        self.poweroff = poweroff
        self.streamo = streamo
        self.streamo_restart = streamo_restart
        self.mode = 'stopped'
        self.transition_error: str | None = None
        self.state_path = (
            closing_state_path.with_name('music.json')
            if closing_state_path is not None
            else None
        )
        self.phase_lock = threading.Lock()
        self.cleanup_lock = threading.Lock()
        self.usb_routing_lock = threading.Lock()
        self.usb_routing_stopped = threading.Event()
        self.usb_routing_thread: threading.Thread | None = None
        self.x18_usb_routing: models.ReadinessCheck | None = None
        self._load_mode()
        self.closing = ClosingController(
            recs,
            player,
            routing,
            streamo,
            config.fade_seconds,
            self.teardown_audio,
            closing_state_path,
            # Keep the phase owner out of the closing controller's retained graph.
            partial(MusicController._save_mode, proxy(self)),
        )
        self.closing.observe_existing()

    def status(self) -> models.MusicStatus:
        player = self.player.status()
        with self.phase_lock:
            mode, error = self.mode, self.transition_error
        usb_routing = self.x18_usb_routing
        main_usb_error = (
            usb_routing.message if usb_routing and not usb_routing.ok else None
        )
        closing = self.closing.status()
        remote = closing.remote
        if remote is None and closing.record is not None:
            remote = models.ClosingStatus(
                operation_id=closing.record.operation_id,
                state='unknown',
                phase=closing.record.phase,
            )
        if mode != 'transitioning':
            if closing.observing:
                mode = 'closing'
            elif (closing.error and closing.record is None) or (
                closing.record is not None and closing.record.phase == 'failed'
            ):
                mode = 'fault'
            elif closing.record is not None and not error:
                mode = (
                    'teardown'
                    if closing.record.phase == 'teardown-music-playing'
                    else 'closing'
                )
        return models.MusicStatus(
            mode=mode,
            state=player.state,
            directory=player.directory,
            track=player.track,
            error='; '.join(e for e in [error, closing.error, main_usb_error] if e)
            or player.error,
            closing=remote,
            x18_usb_routing=usb_routing,
        )

    def start_usb_routing(self) -> None:
        self._configure_main_usb()
        self.usb_routing_thread = threading.Thread(
            target=self._maintain_usb_routing, name='showco-x18-usb', daemon=True
        )
        self.usb_routing_thread.start()

    def _maintain_usb_routing(self) -> None:
        while not self.usb_routing_stopped.wait(USB_ROUTING_CHECK_SECONDS):
            self._configure_main_usb()

    def _configure_main_usb(self, *, required: bool = False) -> None:
        with self.usb_routing_lock:
            try:
                self.routing.route_main_to_usb()
            except (OSError, ValueError) as error:
                message = f'Cannot route X18 Main L/R to USB 17–18: {error}'
                if (
                    self.x18_usb_routing is None
                    or message != self.x18_usb_routing.message
                ):
                    LOGGER.warning('%s', message)
                self.x18_usb_routing = models.ReadinessCheck(
                    name='X18 USB routing', ok=False, message=message
                )
                if required:
                    raise
            else:
                if self.x18_usb_routing is not None and not self.x18_usb_routing.ok:
                    LOGGER.info('X18 Main L/R routing to USB 17–18 verified')
                self.x18_usb_routing = models.ReadinessCheck(
                    name='X18 USB routing',
                    ok=True,
                    message='Main L/R post-fader on USB 17–18',
                )

    def start_stream(self) -> models.ActionResult:
        if self.streamo is None:
            return models.ActionResult(ok=True, message='streamO is disabled')
        if self.streamo_restart is None:
            raise ValueError('streamO restart is not configured')
        self._configure_main_usb(required=True)
        return self.streamo_restart()

    def setup(self) -> models.ActionResult:
        self.closing.require_idle()
        restore = (
            [('room routing restored', self._restore_room)]
            if self.closing.status().record is not None
            else []
        )
        result = self._transition(
            'setup',
            'Setup music is playing'
            if self.setup_audio
            else 'Setup ready; no incidental audio configured',
            [
                ('streamO stopped', self._stop_stream),
                ('recs paused', self._pause_recording),
                *restore,
                (
                    'setup music started',
                    lambda: self._start(self.setup_audio),
                ),
                (
                    'music returns enabled'
                    if self.setup_audio
                    else 'music returns muted',
                    self.routing.enable if self.setup_audio else self.routing.disable,
                ),
            ],
        )
        self.closing.clear()
        return result

    def record(self, *, restart: bool = False) -> models.ActionResult:
        if restart:
            if not self.closing.stop(timeout=5):
                return models.ActionResult(
                    ok=False, message='Closing observer is still active; retry Restart'
                )
        else:
            self.closing.require_idle()
        if self.status().mode == 'record' and self.closing.status().record is None:
            return models.ActionResult(
                ok=True,
                message='Already in Record mode; recording and stream are unchanged',
            )
        restore = (
            [('room routing restored', self._restore_room)]
            if self.closing.status().record is not None
            else []
        )
        result = self._transition(
            'record',
            'Music stopped; new recording started',
            [
                ('music stopped', lambda: self.player.stop(self.config.fade_seconds)),
                ('music returns muted', self.routing.disable),
                *restore,
                (
                    'new recs session started',
                    lambda: self._require(self.recs.action('new_session')),
                ),
                (
                    'recs recording resumed',
                    lambda: self._require(self.recs.action('resume_recording')),
                ),
                ('streamO started', lambda: self._require(self.start_stream())),
            ],
        )
        self.closing.clear()
        return result

    def teardown(self) -> models.ActionResult:
        if self.streamo is not None and self.streamo.status().stream_state != 'stopped':
            record = self.closing.status().record
            if (
                record is None or record.phase == 'teardown-music-playing'
            ) and self.status().mode in {
                'transitioning',
                'fault',
            }:
                return models.ActionResult(
                    ok=False,
                    message='Resolve the current music transition before closing',
                )
            return self.closing.request()
        self.closing.require_idle()
        if self.status().mode == 'teardown':
            return models.ActionResult(ok=True, message='The show has already ended')
        return self._transition(
            'teardown',
            'Recording stopped; teardown music is playing'
            if self.teardown_audio
            else 'Recording paused; no teardown audio configured',
            [
                ('streamO stopped', self._stop_stream),
                ('recs paused', self._pause_recording),
                (
                    'playback stopped',
                    lambda: self._require(self.recs.action('stop_playback')),
                ),
                (
                    'teardown music started',
                    lambda: self._start(self.teardown_audio),
                ),
                (
                    'music returns enabled'
                    if self.teardown_audio
                    else 'music returns muted',
                    self.routing.enable
                    if self.teardown_audio
                    else self.routing.disable,
                ),
            ],
        )

    def abandon_closing(self) -> models.ActionResult:
        if (rejected := self.closing.prepare_recovery()) is not None:
            return rejected
        return self.record(restart=True)

    def finish_closing(self) -> models.ActionResult:
        return self.closing.finish()

    def stop(self) -> models.ActionResult:
        try:
            self._close_with_deadline(self.config.fade_seconds, save_stopped=True)
        except (OSError, RuntimeError, ValueError, sounddevice.PortAudioError) as error:
            warning = f'Show cleanup incomplete: {error}'
            LOGGER.error('%s', warning)
        else:
            warning = None
        try:
            self.poweroff()
        except subprocess.TimeoutExpired:
            message = 'Pi shutdown request timed out; poweroff is unconfirmed'
            return models.ActionResult(
                ok=False, message=f'{message}. {warning}' if warning else message
            )
        except (OSError, subprocess.SubprocessError) as error:
            message = f'Pi shutdown request failed: {error}'
            return models.ActionResult(
                ok=False, message=f'{message}. {warning}' if warning else message
            )
        message = 'Pi shutdown requested'
        return models.ActionResult(
            ok=True, message=f'{message}. {warning}' if warning else message
        )

    def close(self, fade_seconds: float = 0.0) -> None:
        self.usb_routing_stopped.set()
        self._close_with_deadline(fade_seconds, save_stopped=False)

    def _close_with_deadline(self, fade_seconds: float, *, save_stopped: bool) -> None:
        if not self.cleanup_lock.acquire(blocking=False):
            raise TimeoutError('Music cleanup is still running')
        completed: Future[None] = Future()
        expired = threading.Event()

        def cleanup() -> None:
            failure: Exception | None = None
            try:
                if self.usb_routing_stopped.is_set() and self.usb_routing_thread:
                    self.usb_routing_thread.join()
                # The observer owns the player and mixer until finalization exits.
                self.closing.stop()
                if not expired.is_set():
                    try:
                        self.player.stop(fade_seconds)
                    finally:
                        if not expired.is_set():
                            self.routing.disable()
                    if save_stopped and not expired.is_set():
                        self._save_mode('stopped')
            except (
                OSError,
                RuntimeError,
                ValueError,
                sounddevice.PortAudioError,
            ) as error:
                failure = error
            finally:
                self.cleanup_lock.release()
            if failure is None:
                completed.set_result(None)
            else:
                completed.set_exception(failure)

        worker = threading.Thread(
            target=cleanup, name='showco-music-close', daemon=True
        )
        try:
            worker.start()
        except RuntimeError as error:
            self.cleanup_lock.release()
            raise OSError('Could not start shutdown cleanup') from error
        try:
            completed.result(timeout=SHUTDOWN_CLEANUP_SECONDS)
        except TimeoutError:
            if completed.done():
                return completed.result()
            expired.set()
            raise TimeoutError('Timed out waiting for music cleanup') from None

    def _restore_room(self) -> None:
        record = self.closing.status().record
        if record is not None:
            self.routing.restore_room(record.room_scene)

    def _load_mode(self) -> None:
        if self.state_path is None:
            return
        try:
            state = MusicState.model_validate(settings.read(self.state_path))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as error:
            self.mode = 'fault'
            self.transition_error = f'Show phase cannot be read: {error}'
            return
        self.mode = state.mode
        self.transition_error = state.error
        if self.mode == 'transitioning':
            self.mode = 'fault'
            self.transition_error = (
                'showCo restarted during a show transition; inspect recording, '
                'stream and mixer state before choosing a mode'
            )

    def _save_mode(self, mode: str, error: str | None = None) -> None:
        state = MusicState.model_validate({'mode': mode, 'error': error})
        if self.state_path is not None:
            path = self.state_path
            path.parent.mkdir(parents=True, exist_ok=True)
            settings.write_json_model(path, state)
        with self.phase_lock:
            self.mode = state.mode
            self.transition_error = state.error

    def _transition(
        self, mode: str, message: str, steps: list[tuple[str, Callable[[], None]]]
    ) -> models.ActionResult:
        completed: list[str] = []
        step_name = ''
        self._save_mode('transitioning')
        try:
            for step_name, action in steps:
                action()
                completed.append(step_name)
            step_name = 'show phase saved'
            self._save_mode(mode)
        except (
            OSError,
            ValueError,
            TimeoutError,
            subprocess.CalledProcessError,
            sounddevice.PortAudioError,
        ) as error:
            raise ValueError(
                self._recover_transition(mode, step_name, error, completed)
            ) from error
        except KeyboardInterrupt as error:
            raise KeyboardInterrupt(
                self._recover_transition(mode, step_name, error, completed)
            ) from error
        return models.ActionResult(ok=True, message=message)

    def _recover_transition(
        self, mode: str, step_name: str, error: BaseException, completed: list[str]
    ) -> str:
        recovery_errors: list[str] = []
        for name, action in [
            ('mute music returns', self.routing.disable),
            ('stop music player', lambda: self.player.stop(0)),
        ]:
            try:
                action()
                completed.append(name)
            except (
                OSError,
                ValueError,
                TimeoutError,
                sounddevice.PortAudioError,
            ) as recovery_error:
                recovery_errors.append(f'{name} failed: {recovery_error}')
        message = (
            f'{mode} failed at {step_name}: {error}. '
            f'Completed: {", ".join(completed) or "none"}.'
            + (
                f' Recovery errors: {"; ".join(recovery_errors)}.'
                if recovery_errors
                else ''
            )
        )
        with self.phase_lock:
            self.mode = 'fault'
            self.transition_error = message
        return message

    def _pause_recording(self) -> None:
        result = self.recs.pause_recording()
        if isinstance(result, models.ActionResult):
            self._require(result)

    def _start(self, paths: list[Path]) -> None:
        if paths:
            self.player.start(paths, self.config.fade_seconds)
        else:
            self.player.stop(0)

    def _stop_stream(self) -> None:
        if self.streamo is not None:
            if self.streamo.status().stream_state == 'stopped':
                return
            self._require(self.streamo.action('stop'))

    @staticmethod
    def _require(result: models.ActionResult) -> None:
        if not result.ok:
            raise ValueError(result.message)


def controller_from_specs(
    recs: RecsClient,
    specs: list[MixerSpec],
    streamo: StreamoClient | None = None,
    streamo_restart: Callable[[], models.ActionResult] | None = None,
    *,
    setup: list[Path] | None = None,
    teardown: list[Path] | None = None,
) -> MusicController | None:
    mixer = next((m for m in specs if m.name == 'X18'), None)
    if mixer is None or mixer.osc is None:
        return None
    config = load_config(Path.home() / '.config/showco/music.toml')
    return MusicController(
        recs,
        MusicPlayer(config.source_channels, mixer.audio_device_names),
        X18MusicRouting(mixer.osc.host, mixer.osc.port, config.source_channels),
        config,
        streamo=streamo,
        streamo_restart=streamo_restart,
        closing_state_path=Path.home() / '.local/state/showco/closing.json',
        setup=setup,
        teardown=teardown,
    )


def load_config(path: Path) -> MusicConfig:
    try:
        return MusicConfig.model_validate(settings.read(path))
    except FileNotFoundError:
        return MusicConfig()
