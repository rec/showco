from __future__ import annotations

import math
import time

from recs.musicians import Musician

from ..streamo.client import StreamoClient
from . import models
from .lyte import LyteClient
from .mixer import MixersMonitor
from .recs import RecsClient, stereo_tracks
from .system import SystemMonitor


class RehearsalRecsClient(RecsClient):
    def __init__(self) -> None:
        self.started_at = time.time()
        self.calibration_count = 0
        self.rehearsal_tracks = [[channel] for channel in range(1, 19)]
        self.rehearsal_track_names: dict[str, dict[str, int]] = {}
        self.rehearsal_musicians: dict[str, Musician] = {}
        self.rehearsal_attributes: dict[str, object] = {
            'recording.longest_file_time': 0.0,
            'recording.record_everything': False,
        }

    def status(self) -> models.RecsStatus:
        elapsed = time.time() - self.started_at
        return models.RecsStatus(
            service=models.ServiceStatus(
                name='recs',
                state='connected',
            ),
            recording=True,
            elapsed_seconds=elapsed,
            recorded_seconds=max(0.0, elapsed - 0.2),
            file_size=elapsed * 9_000_000,
            file_count=18,
            channels=rehearsal_channels(elapsed, self.rehearsal_tracks),
            errors=[],
            disk=models.RecordingDiskStatus(
                path='/media/showco/recordings',
                used_bytes=48 * 1024**3,
                free_bytes=208 * 1024**3,
                total_bytes=256 * 1024**3,
                estimated_seconds_remaining=18 * 3600,
            ),
        )

    def calibrate(
        self, device: str = '', channels: list[int] | None = None
    ) -> models.ActionResult:
        self.calibration_count += 1
        return models.ActionResult(
            ok=True,
            message=f'rehearsal recs calibration {self.calibration_count}',
        )

    def musicians(self) -> dict[str, Musician]:
        return dict(self.rehearsal_musicians)

    def add_musician(
        self,
        nickname: str,
        names: list[str],
        copyright_name: str | None,
        public_keys: list[str],
        links: list[str],
    ) -> models.ActionResult:
        if nickname in self.rehearsal_musicians:
            return models.ActionResult(
                ok=False, message=f'Musician already exists: {nickname}'
            )
        return self._save_rehearsal_musician(
            nickname, names, copyright_name, public_keys, links
        )

    def edit_musician(
        self,
        nickname: str,
        names: list[str],
        public_keys: list[str] | None,
        links: list[str],
    ) -> models.ActionResult:
        musician = self.rehearsal_musicians.get(nickname)
        if musician is None:
            return models.ActionResult(
                ok=False, message=f'Unknown musician: {nickname}'
            )
        return self._save_rehearsal_musician(
            nickname,
            names,
            musician.copyright_name,
            musician.public_keys if public_keys is None else public_keys,
            links,
        )

    def _save_rehearsal_musician(
        self,
        nickname: str,
        names: list[str],
        copyright_name: str | None,
        public_keys: list[str],
        links: list[str],
    ) -> models.ActionResult:
        musician = Musician(
            nickname=nickname,
            names=names,
            copyright_name=copyright_name,
            public_keys=public_keys,
            links=links,
        )
        self.rehearsal_musicians[nickname] = musician
        return models.ActionResult(
            ok=True, message=f'rehearsal recs saved musician {nickname}'
        )

    def set_track_name(
        self, device: str, channel: str, track_name: str
    ) -> models.ActionResult:
        device = device.strip()
        channel = channel.strip()
        track_name = track_name.strip()
        if not device or not channel:
            return models.ActionResult(
                ok=False, message='rehearsal recs track name missing'
            )
        channel_number = int(channel.partition('-')[0])
        names = self.rehearsal_track_names.setdefault(device, {})
        for name, value in list(names.items()):
            if value == channel_number:
                del names[name]
        if track_name:
            names[track_name] = channel_number
        return models.ActionResult(
            ok=True, message=f'rehearsal recs track name {track_name}'
        )

    def set_stereo(self, device: str, channels: list[int]) -> models.ActionResult:
        tracks = stereo_tracks(self.status().channels, device, channels)
        if isinstance(tracks, models.ActionResult):
            return tracks
        self.rehearsal_tracks = tracks
        return models.ActionResult(ok=True, message='rehearsal recs stereo updated')

    def mutable_attributes(self) -> list[models.MutableAttribute]:
        return [
            models.MutableAttribute(address=a, value=v)
            for a, v in self.rehearsal_attributes.items()
        ]

    def set_attr(self, address: str, value: object) -> models.ActionResult:
        if address not in self.rehearsal_attributes:
            return models.ActionResult(
                ok=False,
                message=f'rehearsal recs unknown attribute {address}',
            )
        self.rehearsal_attributes[address] = value
        return models.ActionResult(ok=True, message=f'rehearsal recs set {address}')

    def action(self, command: str, **fields: object) -> models.ActionResult:
        if fields:
            return models.ActionResult(
                ok=True, message=f'rehearsal recs {command} {fields} succeeded'
            )
        return models.ActionResult(
            ok=True, message=f'rehearsal recs {command} succeeded'
        )

    def shutdown(self) -> models.ActionResult:
        return models.ActionResult(ok=True, message='rehearsal recs shutdown requested')


class RehearsalLyteClient(LyteClient):
    def __init__(self) -> None:
        super().__init__(enabled=True)
        self.animation = 'idle'

    def status(self) -> models.LyteStatus:
        return models.LyteStatus(
            service=models.ServiceStatus(name='lyte', state='connected'),
            running=True,
            animations=['idle', 'circle', 'square'],
            active_animation=self.animation,
        )

    def select_animation(self, name: str) -> models.ActionResult:
        if name not in self.status().animations:
            return models.ActionResult(
                ok=False, message=f'Unknown rehearsal look: {name}'
            )
        self.animation = name
        return models.ActionResult(ok=True, message=f'Rehearsal look selected: {name}')

    def test(self) -> models.ActionResult:
        return models.ActionResult(
            ok=True, message='Rehearsal light test; no physical output'
        )


class RehearsalStreamoClient(StreamoClient):
    def __init__(self) -> None:
        self.started_at = time.time()
        self.muted = False
        self.stopped = False
        self.actions: list[tuple[str, dict[str, object]]] = []

    def status(self) -> models.StreamoStatus:
        return models.StreamoStatus(
            service=models.ServiceStatus(name='streamo', state='connected'),
            stream_state='stopped' if self.stopped else 'streaming',
            muted=self.muted,
            ffmpeg_alive=not self.stopped,
            audio_seconds=0.0 if self.stopped else time.time() - self.started_at,
            clipping=False,
            output_bitrate_kbps=310.0 if not self.stopped else 0.0,
        )

    def action(self, command: str, **fields: object) -> models.ActionResult:
        self.actions.append((command, fields))
        if command == 'mute':
            self.muted = True
        elif command == 'unmute':
            self.muted = False
        elif command == 'stop':
            self.stopped = True
        return models.ActionResult(
            ok=True, message=f'rehearsal streamo {command} succeeded'
        )


def restart_streamo() -> models.ActionResult:
    return models.ActionResult(ok=True, message='rehearsal streamo restart requested')


class RehearsalSystemMonitor(SystemMonitor):
    def status(self) -> models.SystemStatus:
        return models.SystemStatus(
            temperature_c=48.5,
            cpu_percent=18,
            memory_used_bytes=2 * 1024**3,
            memory_total_bytes=8 * 1024**3,
        )


class RehearsalMixersMonitor(MixersMonitor):
    def __init__(self) -> None:
        super().__init__([])

    def status(
        self, audio_devices: set[str], midi: dict[str, str]
    ) -> list[models.MixerStatus]:
        return [
            models.MixerStatus(name='X18', state='connected', latency_ms=4.2),
            models.MixerStatus(name='Flow 8', state='waiting'),
        ]


def rehearsal_channels(
    elapsed: float, tracks: list[list[int]]
) -> list[models.ChannelLevel]:
    channels = []
    for track in tracks:
        index = track[0] - 1
        signal = channel_signal(index, elapsed)
        channels.append(
            models.ChannelLevel(
                name='-'.join(str(channel) for channel in track),
                state=channel_state(signal),
                device='X18/XR18',
                channels=track,
                signal=signal,
                on=True,
            )
        )
    return channels


def channel_signal(index: int, elapsed: float) -> float:
    if index == 17:
        return 0.95 if int(elapsed) % 9 == 0 else 0.62
    if index % 6 == 0:
        return 0.0
    return 0.18 + 0.58 * ((math.sin(elapsed + index) + 1) / 2)


def channel_state(signal: float) -> str:
    if signal < 0.001:
        return 'silent'
    if signal < 1 / 3:
        return 'present'
    if signal < 0.9:
        return 'healthy'
    return 'clipping'
