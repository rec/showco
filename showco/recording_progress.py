from __future__ import annotations

import time
from collections.abc import Callable

from . import models

STALL_SECONDS = 15.0


class ProgressMonitor:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.recorded_seconds: float | None = None
        self.advanced_at: float | None = None

    def observe(self, recs: models.RecsStatus) -> models.RecordingProgress:
        if not recs.recording or recs.paused:
            return models.RecordingProgress(ok=False, message="not recording")
        if recs.recorded_seconds is None:
            return models.RecordingProgress(
                ok=False, message="recording progress unknown"
            )
        now = self.clock()
        if (
            self.recorded_seconds is None
            or recs.recorded_seconds > self.recorded_seconds
        ):
            self.advanced_at = now
        self.recorded_seconds = recs.recorded_seconds
        if self.advanced_at is not None and now - self.advanced_at <= STALL_SECONDS:
            return models.RecordingProgress(ok=True, message="recorded audio advancing")
        return models.RecordingProgress(
            ok=False, message="recorded audio has not advanced"
        )
