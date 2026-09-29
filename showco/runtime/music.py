from __future__ import annotations

import subprocess
import tomllib
from collections.abc import Callable
from pathlib import Path

import sounddevice
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..streamo.client import StreamoClient
from ..x18.music import MusicPlayer, X18MusicRouting
from . import models
from .mixer import MixerSpec
from .recs import RecsClient

DEFAULT_FADE_SECONDS = 2.0


class MusicConfig(BaseModel, frozen=True):
    setup_directory: Path = Field(default_factory=lambda: Path.home() / 'Music/setup')
    teardown_directory: Path = Field(
        default_factory=lambda: Path.home() / 'Music/teardown'
    )
    shuffle: bool = False
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
    ) -> None:
        self.recs = recs
        self.player = player
        self.routing = routing
        self.config = config
        self.poweroff = poweroff
        self.streamo = streamo
        self.streamo_restart = streamo_restart
        self.mode = 'stopped'
        self.transition_error: str | None = None

    def status(self) -> models.MusicStatus:
        player = self.player.status()
        return models.MusicStatus(
            mode=self.mode,
            state=player.state,
            directory=player.directory,
            track=player.track,
            error=self.transition_error or player.error,
        )

    def setup(self) -> models.ActionResult:
        return self._transition(
            'setup',
            'Setup music is playing',
            [
                ('streamO stopped', self._stop_stream),
                ('recs paused', self._pause_recording),
                (
                    'setup music started',
                    lambda: self._start(self.config.setup_directory),
                ),
                ('music returns enabled', self.routing.enable),
            ],
        )

    def record(self) -> models.ActionResult:
        return self._transition(
            'record',
            'Music stopped; new recording started',
            [
                ('music stopped', lambda: self.player.stop(self.config.fade_seconds)),
                ('music returns muted', self.routing.disable),
                (
                    'new recs session started',
                    lambda: self._require(self.recs.action('new_session')),
                ),
                ('streamO started', self._start_stream),
            ],
        )

    def teardown(self) -> models.ActionResult:
        return self._transition(
            'teardown',
            'Recording stopped; teardown music is playing',
            [
                ('streamO stopped', self._stop_stream),
                ('recs paused', self._pause_recording),
                (
                    'playback stopped',
                    lambda: self._require(self.recs.action('stop_playback')),
                ),
                (
                    'teardown music started',
                    lambda: self._start(self.config.teardown_directory),
                ),
                ('music returns enabled', self.routing.enable),
            ],
        )

    def _transition(
        self, mode: str, message: str, steps: list[tuple[str, Callable[[], None]]]
    ) -> models.ActionResult:
        completed: list[str] = []
        step_name = ''
        try:
            for step_name, action in steps:
                action()
                completed.append(step_name)
        except (
            OSError,
            ValueError,
            TimeoutError,
            subprocess.CalledProcessError,
            sounddevice.PortAudioError,
        ) as error:
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
            raise ValueError(self.transition_error) from error
        self.mode = mode
        self.transition_error = None
        return models.ActionResult(ok=True, message=message)

    def stop(self) -> models.ActionResult:
        self.close(self.config.fade_seconds)
        self.mode = 'stopped'
        self.poweroff()
        return models.ActionResult(
            ok=True, message='Music stopped; Pi is shutting down'
        )

    def close(self, fade_seconds: float = 0.0) -> None:
        self.player.stop(fade_seconds)
        self.routing.disable()

    def _pause_recording(self) -> None:
        result = self.recs.pause_recording()
        if isinstance(result, models.ActionResult):
            self._require(result)

    def _start(self, directory: Path) -> None:
        self.player.start(directory, self.config.shuffle, self.config.fade_seconds)

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
    )


def load_config(path: Path) -> MusicConfig:
    try:
        return MusicConfig.model_validate(tomllib.loads(path.read_text()))
    except FileNotFoundError:
        return MusicConfig()
