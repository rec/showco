from __future__ import annotations

import unittest

from showco import models, recording_progress


class RecordingProgressTests(unittest.TestCase):
    def test_flags_recording_that_stops_advancing(self) -> None:
        times = iter([0.0, recording_progress.STALL_SECONDS + 1])
        monitor = recording_progress.ProgressMonitor(lambda: next(times))

        self.assertTrue(monitor.observe(recs(1.0)).ok)
        self.assertFalse(monitor.observe(recs(1.0)).ok)


def recs(recorded_seconds: float) -> models.RecsStatus:
    return models.RecsStatus(
        service=models.ServiceStatus(name="recs", state="connected"),
        recording=True,
        recorded_seconds=recorded_seconds,
    )
