from __future__ import annotations

import subprocess
import threading
import time
import tomllib
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import sounddevice
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from reccy.runtime.files import atomic_output

from ..streamo.client import StreamoClient
from ..x18.music import MusicPlayer, RoomScene, X18MusicRouting
from . import models
from .mixer import MixerSpec
from .recs import RecsClient

DEFAULT_FADE_SECONDS = 2.0


class ClosingRecord(BaseModel, frozen=True):
    operation_id: str
    phase: str
    room_scene: RoomScene
    error: str | None = None


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
    subprocess.run(['sudo', 'systemctl', 'poweroff'], check=True)


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
        self.closing_state_path = closing_state_path
        self.closing_record: ClosingRecord | None = None
        self.closing_status: models.ClosingStatus | None = None
        self.closing_lock = threading.Lock()
        self.closing_stop = threading.Event()
        self.closing_thread: threading.Thread | None = None
        self.black_fade_done = False
        self._load_mode()
        self._load_closing()
        if self.closing_record is not None and self.closing_record.phase not in {
            'teardown-music-playing',
            'failed',
        }:
            self._start_observer()

    def status(self) -> models.MusicStatus:
        player = self.player.status()
        return models.MusicStatus(
            mode=self.mode,
            state=player.state,
            directory=player.directory,
            track=player.track,
            error=self.transition_error or player.error,
            closing=self.closing_status,
        )

    def setup(self) -> models.ActionResult:
        self._require_no_closing()
        restore = (
            [('room routing restored', self._restore_room)]
            if self.closing_record is not None
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
        self._clear_closing()
        return result

    def record(self) -> models.ActionResult:
        self._require_no_closing()
        restore = (
            [('room routing restored', self._restore_room)]
            if self.closing_record is not None
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
                ('streamO started', self._start_stream),
            ],
        )
        self._clear_closing()
        return result

    def teardown(self) -> models.ActionResult:
        if self.streamo is not None:
            return self._request_closing()
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

    def _request_closing(self) -> models.ActionResult:
        assert self.streamo is not None
        with self.closing_lock:
            if self.closing_record is not None:
                return models.ActionResult(
                    ok=False,
                    message='A closing operation already exists; review its status',
                )
            if self.mode != 'record':
                return models.ActionResult(
                    ok=False,
                    message='Enter Record mode before starting closing credits',
                )
            scene = self.routing.capture_room()
            record = ClosingRecord(
                operation_id=str(uuid.uuid4()),
                phase='credits-requesting',
                room_scene=scene,
            )
            self._save_closing(record)
            self.mode = 'closing'
            self.black_fade_done = False
        try:
            self.streamo.start_closing(record.operation_id)
        except (ConnectionError, OSError, TimeoutError, ValueError) as error:
            self.transition_error = (
                f'Closing request outcome is uncertain: {error}. '
                'Recording and room mix are unchanged; check the closing status.'
            )
            self._start_observer()
            return models.ActionResult(ok=False, message=self.transition_error)
        try:
            self._save_closing(record.model_copy(update={'phase': 'credits-running'}))
        except OSError as error:
            self.transition_error = (
                f'Closing started but local progress could not be saved: {error}'
            )
            self._start_observer()
            return models.ActionResult(ok=False, message=self.transition_error)
        self.transition_error = None
        self._start_observer()
        return models.ActionResult(
            ok=True, message='Closing credits started; recs continues recording'
        )

    def _start_observer(self) -> None:
        if self.closing_thread is not None and self.closing_thread.is_alive():
            return
        self.closing_stop.clear()
        self.closing_thread = threading.Thread(
            target=self._observe_closing, name='showco-closing-credits', daemon=True
        )
        self.closing_thread.start()

    def _observe_closing(self) -> None:
        while not self.closing_stop.wait(0.1):
            record = self.closing_record
            if record is None:
                return
            assert self.streamo is not None
            status = self.streamo.status()
            closing = status.closing
            if closing is None or closing.operation_id != record.operation_id:
                self.transition_error = (
                    'Closing status is unavailable or has a different operation ID; '
                    'recording and room music are unchanged'
                )
                continue
            self.closing_status = closing
            try:
                if closing.state == 'failed':
                    self._fail_closing(closing.error or 'streamO closing failed')
                    return
                if closing.state == 'running' and closing.black_started_at is not None:
                    if not self.black_fade_done:
                        self._save_closing(
                            record.model_copy(update={'phase': 'black-interval'})
                        )
                        remaining = max(
                            0.0,
                            min(
                                2.0, (closing.black_at or time.time()) + 2 - time.time()
                            ),
                        )
                        self.routing.fade_main(0.0, remaining)
                        self.black_fade_done = True
                if closing.state == 'completed':
                    if not self.black_fade_done:
                        self._fail_closing(
                            'streamO completed before showCo confirmed the room fade'
                        )
                        return
                    self._finish_closing()
                    return
            except (
                OSError,
                TimeoutError,
                ValueError,
                sounddevice.PortAudioError,
            ) as error:
                self._fail_closing(str(error))
                return

    def _finish_closing(self) -> models.ActionResult:
        record = self.closing_record
        if record is None:
            return models.ActionResult(ok=False, message='No closing operation exists')
        self._save_closing(record.model_copy(update={'phase': 'finalizing'}))
        self.routing.fade_main(0.0, 0)
        self._pause_recording()
        self._require(self.recs.action('stop_playback'))
        self.routing.isolate_instruments(record.room_scene)
        if self.teardown_audio:
            self.player.start(self.teardown_audio, 0)
            self.routing.enable()
            self.routing.fade_main(
                record.room_scene.master_fader, self.config.fade_seconds
            )
        else:
            self.player.stop(0)
            self.routing.disable()
        self._save_closing(
            record.model_copy(update={'phase': 'teardown-music-playing'})
        )
        self._save_mode('teardown')
        return models.ActionResult(
            ok=True,
            message='Broadcast stopped; recs paused; teardown music is playing'
            if self.teardown_audio
            else 'Broadcast stopped; recs paused; no teardown audio configured',
        )

    def _fail_closing(self, message: str) -> None:
        record = self.closing_record
        if record is None:
            return
        for name, action in [
            ('stop music player', lambda: self.player.stop(0)),
            ('mute music returns', self.routing.disable),
            *(
                [
                    (
                        'restore room mix',
                        lambda: self.routing.restore_room(record.room_scene),
                    )
                ]
                if record.phase in {'black-interval', 'finalizing'}
                else []
            ),
        ]:
            try:
                action()
            except (
                OSError,
                TimeoutError,
                ValueError,
                sounddevice.PortAudioError,
            ) as error:
                message += f'; {name} failed: {error}'
        self._save_closing(
            record.model_copy(update={'phase': 'failed', 'error': message})
        )
        self.mode = 'fault'
        self.transition_error = message

    def recover_closing(self, finish: bool) -> models.ActionResult:
        record = self.closing_record
        if record is None or record.phase == 'teardown-music-playing':
            return models.ActionResult(
                ok=False, message='No closing operation needs recovery'
            )
        if self.streamo is not None:
            closing = self.streamo.status().closing
            if (
                closing is not None
                and closing.operation_id == record.operation_id
                and closing.state == 'running'
            ):
                return models.ActionResult(
                    ok=False, message='Closing credits are still running'
                )
        self.closing_stop.set()
        if self.closing_thread is not None and self.closing_thread.is_alive():
            self.closing_thread.join(timeout=5)
            if self.closing_thread.is_alive():
                return models.ActionResult(
                    ok=False, message='Closing observer is still active'
                )
        if finish:
            try:
                return self._finish_closing()
            except (
                OSError,
                TimeoutError,
                ValueError,
                sounddevice.PortAudioError,
            ) as error:
                self._fail_closing(str(error))
                return models.ActionResult(ok=False, message=str(error))
        self.routing.restore_room(record.room_scene)
        self._clear_closing()
        self._save_mode('record')
        return models.ActionResult(
            ok=True,
            message='Closing abandoned; verify recs recording state before continuing',
        )

    def _require_no_closing(self) -> None:
        if self.closing_record is not None and self.closing_record.phase not in {
            'teardown-music-playing'
        }:
            raise ValueError('Resolve the closing operation before changing music mode')

    def _restore_room(self) -> None:
        if self.closing_record is not None:
            self.routing.restore_room(self.closing_record.room_scene)

    def _load_mode(self) -> None:
        if self.closing_state_path is None:
            return
        try:
            state = MusicState.model_validate_json(
                self.closing_state_path.with_name('music.json').read_text()
            )
        except FileNotFoundError:
            return
        except (OSError, ValidationError) as error:
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
        if self.closing_state_path is not None:
            path = self.closing_state_path.with_name('music.json')
            path.parent.mkdir(parents=True, exist_ok=True)
            with atomic_output(path) as temporary:
                temporary.write_text(state.model_dump_json())
        self.mode = state.mode
        self.transition_error = state.error

    def _load_closing(self) -> None:
        if self.closing_state_path is None:
            return
        try:
            record = ClosingRecord.model_validate_json(
                self.closing_state_path.read_text()
            )
        except FileNotFoundError:
            return
        except (OSError, ValidationError) as error:
            self.mode = 'fault'
            self.transition_error = f'Closing state cannot be read: {error}'
            return
        self.closing_record = record
        if record.phase == 'finalizing':
            record = record.model_copy(
                update={
                    'phase': 'failed',
                    'error': (
                        'showCo restarted during finalization; '
                        'inspect recs and mixer state'
                    ),
                }
            )
            self._save_closing(record)
        self.mode = (
            'teardown'
            if record.phase == 'teardown-music-playing'
            else 'fault'
            if record.phase == 'failed'
            else 'closing'
        )
        self.transition_error = record.error
        self.closing_status = models.ClosingStatus(
            operation_id=record.operation_id,
            state='failed' if record.phase == 'failed' else 'running',
            phase=record.phase,
            error=record.error,
        )

    def _save_closing(self, record: ClosingRecord) -> None:
        if self.closing_state_path is not None:
            self.closing_state_path.parent.mkdir(parents=True, exist_ok=True)
            with atomic_output(self.closing_state_path) as temporary:
                temporary.write_text(record.model_dump_json())
        self.closing_record = record

    def _clear_closing(self) -> None:
        self.closing_record = None
        self.closing_status = None
        self.black_fade_done = False
        if self.closing_state_path is not None:
            self.closing_state_path.unlink(missing_ok=True)

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
        self.mode = 'fault'
        self.transition_error = (
            f'{mode} failed at {step_name}: {error}. '
            f'Completed: {", ".join(completed) or "none"}.'
            + (
                f' Recovery errors: {"; ".join(recovery_errors)}.'
                if recovery_errors
                else ''
            )
        )
        return self.transition_error

    def stop(self) -> models.ActionResult:
        self.close(self.config.fade_seconds)
        self._save_mode('stopped')
        self.poweroff()
        return models.ActionResult(
            ok=True, message='Music stopped; Pi is shutting down'
        )

    def close(self, fade_seconds: float = 0.0) -> None:
        self.closing_stop.set()
        if (
            self.closing_thread is not None
            and threading.current_thread() is not self.closing_thread
        ):
            self.closing_thread.join(timeout=5)
        self.player.stop(fade_seconds)
        self.routing.disable()

    def _pause_recording(self) -> None:
        result = self.recs.pause_recording()
        if isinstance(result, models.ActionResult):
            self._require(result)

    def _start(self, paths: list[Path]) -> None:
        if paths:
            self.player.start(paths, self.config.fade_seconds)
        else:
            self.player.stop(0)

    def _start_stream(self) -> None:
        if self.streamo is not None:
            if self.streamo_restart is None:
                raise ValueError('streamO restart is not configured')
            self._require(self.streamo_restart())

    def _stop_stream(self) -> None:
        if self.streamo is not None:
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
        return MusicConfig.model_validate(tomllib.loads(path.read_text()))
    except FileNotFoundError:
        return MusicConfig()
