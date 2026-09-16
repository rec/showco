from __future__ import annotations

import unittest

from showco.runtime import models, recording_progress


class RecordingProgressTests(unittest.TestCase):
    def test_flags_recording_that_stops_advancing(self) -> None:
        times = iter([0.0, 1.0, recording_progress.STALL_SECONDS + 2])
        monitor = recording_progress.ProgressMonitor(lambda: next(times))

        self.assertFalse(monitor.observe(recs(1.0)).ok)
        self.assertTrue(monitor.observe(recs(2.0)).ok)
        self.assertFalse(monitor.observe(recs(2.0)).ok)

    def test_pause_and_session_reset_require_new_progress(self) -> None:
        monitor = recording_progress.ProgressMonitor(lambda: 0.0)
        monitor.observe(recs(10))
        self.assertTrue(monitor.observe(recs(11)).ok)
        self.assertFalse(monitor.observe(recs(0)).ok)
        self.assertTrue(monitor.observe(recs(1)).ok)
        monitor.observe(recs(1).model_copy(update={'paused': True}))
        self.assertFalse(monitor.observe(recs(1)).ok)


def recs(recorded_seconds: float) -> models.RecsStatus:
    return models.RecsStatus(
        service=models.ServiceStatus(name='recs', state='connected'),
        recording=True,
        recorded_seconds=recorded_seconds,
    )
