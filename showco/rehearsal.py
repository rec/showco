from __future__ import annotations

import math
import time

from . import models
from .mixer import MixerMonitor
from .recs import RecsClient
from .system import SystemMonitor
from .twitcho.client import TwitchoClient


class RehearsalRecsClient(RecsClient):
    def __init__(self) -> None:
        self.started_at = time.time()
        self.calibration_count = 0
        self.rehearsal_track_names: dict[str, dict[str, int]] = {}
        self.rehearsal_attributes: dict[str, object] = {
            "recording.longest_file_time": 0.0,
            "recording.record_everything": False,
        }

    def status(self) -> models.RecsStatus:
        elapsed = time.time() - self.started_at
        return models.RecsStatus(
            service=models.ServiceStatus(
                name="recs",
                state="connected",
                updated_at=time.time(),
            ),
            recording=True,
            elapsed_seconds=elapsed,
            recorded_seconds=max(0.0, elapsed - 0.2),
            file_size=elapsed * 9_000_000,
            file_count=18,
            client_count=1,
            channels=rehearsal_channels(elapsed),
            errors=[],
        )

    def calibrate(self) -> models.ActionResult:
        self.calibration_count += 1
        return models.ActionResult(
            ok=True,
            message=f"rehearsal recs calibration {self.calibration_count}",
        )

    def set_track_name(
        self, device: str, channel: str, track_name: str
    ) -> models.ActionResult:
        device = device.strip()
        channel = channel.strip()
        track_name = track_name.strip()
        if not device or not channel:
            return models.ActionResult(
                ok=False, message="rehearsal recs track name missing"
            )
        channel_number = int(channel.partition("-")[0])
        names = self.rehearsal_track_names.setdefault(device, {})
        for name, value in list(names.items()):
            if value == channel_number:
                del names[name]
        if track_name:
            names[track_name] = channel_number
        return models.ActionResult(
            ok=True, message=f"rehearsal recs track name {track_name}"
        )

    def mutable_attributes(self) -> list[models.MutableAttribute]:
        return [
            models.MutableAttribute(address=a, value=v)
            for a, v in self.rehearsal_attributes.items()
        ]

    def set_attr(self, address: str, value: object) -> models.ActionResult:
        if address not in self.rehearsal_attributes:
            return models.ActionResult(
                ok=False,
                message=f"rehearsal recs unknown attribute {address}",
            )
        self.rehearsal_attributes[address] = value
        return models.ActionResult(ok=True, message=f"rehearsal recs set {address}")

    def action(self, command: str, **fields: object) -> models.ActionResult:
        if fields:
            return models.ActionResult(
                ok=True, message=f"rehearsal recs {command} {fields} succeeded"
            )
        return models.ActionResult(
            ok=True, message=f"rehearsal recs {command} succeeded"
        )

    def shutdown(self) -> models.ActionResult:
        return models.ActionResult(ok=True, message="rehearsal recs shutdown requested")


class RehearsalTwitchoClient(TwitchoClient):
    def __init__(self) -> None:
        self.started_at = time.time()
        self.muted = False
        self.stopped = False
        self.actions: list[tuple[str, dict[str, object]]] = []

    def status(self) -> models.TwitchoStatus:
        return models.TwitchoStatus(
            service=models.ServiceStatus(name="twitcho", state="connected"),
            stream_state="stopped" if self.stopped else "streaming",
            muted=self.muted,
            ffmpeg_alive=not self.stopped,
            audio_seconds=0.0 if self.stopped else time.time() - self.started_at,
            clipping=False,
            output_bitrate_kbps=310.0 if not self.stopped else 0.0,
        )

    def action(self, command: str, **fields: object) -> models.ActionResult:
        self.actions.append((command, fields))
        if command == "mute":
            self.muted = True
        elif command == "unmute":
            self.muted = False
        elif command == "stop":
            self.stopped = True
        return models.ActionResult(
            ok=True, message=f"rehearsal twitcho {command} succeeded"
        )


def restart_twitcho() -> models.ActionResult:
    return models.ActionResult(ok=True, message="rehearsal twitcho restart requested")


class RehearsalSystemMonitor(SystemMonitor):
    def status(self) -> models.SystemStatus:
        return models.SystemStatus(temperature_c=48.5)


class RehearsalMixerMonitor(MixerMonitor):
    def status(self) -> models.MixerStatus:
        return models.MixerStatus(latency_ms=4.2)


def rehearsal_channels(elapsed: float) -> list[models.ChannelLevel]:
    channels = []
    for index in range(18):
        signal = channel_signal(index, elapsed)
        channels.append(
            models.ChannelLevel(
                name=str(index + 1),
                state=channel_state(signal),
                device="X18/XR18",
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
        return "silent"
    if signal < 1 / 3:
        return "present"
    if signal < 0.9:
        return "healthy"
    return "clipping"
