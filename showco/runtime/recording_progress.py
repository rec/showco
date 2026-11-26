from __future__ import annotations

import time
from collections.abc import Callable

from pydantic import BaseModel

from . import models

STALL_SECONDS = 15.0
INPUT_STALL_SECONDS = 5.0
CAPTURE_STALE_SECONDS = 10.0


class ProgressMonitor:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.recorded_seconds: float | None = None
        self.advanced_at: float | None = None

    def observe(self, recs: models.RecsStatus) -> models.RecordingProgress:
        if recs.service.state != 'connected' or not recs.snapshot_available:
            self.recorded_seconds = None
            self.advanced_at = None
            return models.RecordingProgress(
                ok=False, message='recording progress unavailable'
            )
        if recs.paused:
            self.recorded_seconds = None
            self.advanced_at = None
            return models.RecordingProgress(ok=False, message='not recording')
        if recs.recorded_seconds is None:
            self.recorded_seconds = None
            self.advanced_at = None
            return models.RecordingProgress(
                ok=False, message='recording progress unknown'
            )
        now = self.clock()
        if (
            self.recorded_seconds is None
            or recs.recorded_seconds < self.recorded_seconds
        ):
            self.advanced_at = None
        elif recs.recorded_seconds > self.recorded_seconds:
            self.advanced_at = now
        self.recorded_seconds = recs.recorded_seconds
        if self.advanced_at is None:
            return models.RecordingProgress(
                ok=False, message='waiting for recorded audio to advance'
            )
        if self.advanced_at is not None and now - self.advanced_at <= STALL_SECONDS:
            return models.RecordingProgress(ok=True, message='recorded audio advancing')
        return models.RecordingProgress(
            ok=False, message='recorded audio has not advanced'
        )


class InputSample(BaseModel):
    seen_at: float
    recorded_seconds: float | None = None
    last_write_at: float | None = None
    write_requested_at: float | None = None


class InputMonitor:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.session: tuple[str | None, str | None] | None = None
        self.tracks: dict[tuple[str, tuple[int, ...]], InputSample] = {}

    def observe(self, recs: models.RecsStatus) -> list[models.InputRecording]:
        current = recs.service.state == 'connected' and recs.snapshot_available
        session = (recs.instance_token, recs.session_directory)
        if self.session != session or not current or recs.paused:
            self.session = session
            self.tracks.clear()
        if not current:
            return []

        now = self.clock()
        observed = {}
        inputs = []
        for channel in recs.channels:
            key = channel.source_key or channel.device, tuple(channel.channels)
            name = f'{channel.device} · {channel.name}'
            if channel.channels:
                name += f' ({", ".join(str(n) for n in channel.channels)})'
            sample = self.tracks.get(key)
            if sample is None or (
                channel.recorded_seconds is not None
                and sample.recorded_seconds is not None
                and channel.recorded_seconds < sample.recorded_seconds
            ):
                sample = InputSample(seen_at=now)
            if recs.paused:
                inputs.append(models.InputRecording(name=name, message='Paused'))
                continue
            offline = (
                channel.capture_running is None
                and channel.device in recs.offline_sources
            )
            inputs.append(input_recording(name, channel, sample, now, offline=offline))
            observed[key] = sample
        self.tracks = observed
        inputs.extend(
            models.InputRecording(
                name=s, message='Configured input unavailable', fault=True
            )
            for s in dict.fromkeys(recs.missing_audio_inputs)
            if not recs.paused
        )
        return inputs


def input_recording(
    name: str,
    channel: models.ChannelLevel,
    sample: InputSample,
    now: float,
    *,
    offline: bool,
) -> models.InputRecording:
    if offline or channel.capture_running is False:
        sample.last_write_at = None
        sample.write_requested_at = None
        sample.recorded_seconds = None
        sample.seen_at = now
        return models.InputRecording(
            name=name, message='Capture unavailable', fault=True
        )
    if channel.writing_enabled is False:
        sample.last_write_at = None
        sample.write_requested_at = None
        sample.recorded_seconds = None
        return models.InputRecording(name=name, message='Writes disabled', fault=True)

    recorded = channel.recorded_seconds
    if (
        recorded is not None
        and sample.recorded_seconds is not None
        and recorded > sample.recorded_seconds
    ):
        sample.last_write_at = now
    sample.recorded_seconds = recorded
    age = channel.capture_age_seconds
    if age is not None and age > CAPTURE_STALE_SECONDS:
        sample.last_write_at = None
        sample.write_requested_at = None
        return models.InputRecording(name=name, message='No fresh samples', fault=True)
    if channel.capture_running is True and age is None:
        fault = now - sample.seen_at >= CAPTURE_STALE_SECONDS
        return models.InputRecording(
            name=name,
            message='No fresh samples' if fault else 'Waiting for samples',
            fault=fault,
        )
    if age is None or channel.write_requested is None or recorded is None:
        return models.InputRecording(
            name=name, message='Per-input evidence unavailable'
        )

    if not channel.write_requested:
        sample.write_requested_at = None
        saved = recorded > 0
        return models.InputRecording(
            name=name,
            message='Quiet · silence filtered'
            if saved
            else 'Quiet · no audio saved yet',
            ok=saved,
        )
    if sample.write_requested_at is None:
        sample.write_requested_at = now
    if (
        sample.last_write_at is not None
        and now - sample.last_write_at < INPUT_STALL_SECONDS
    ):
        return models.InputRecording(name=name, message='Writing audio', ok=True)
    if now - sample.write_requested_at >= INPUT_STALL_SECONDS:
        return models.InputRecording(
            name=name, message='Signal present · writes stalled', fault=True
        )
    return models.InputRecording(
        name=name, message='Signal present · waiting for writes'
    )
