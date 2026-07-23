from __future__ import annotations

import unittest

from showco.rehearsal import (
    RehearsalRecsClient,
    RehearsalSystemMonitor,
    RehearsalTwitchoClient,
)


class RehearsalTests(unittest.TestCase):
    def test_rehearsal_recs_returns_recording_status(self) -> None:
        recs = RehearsalRecsClient()

        status = recs.status()

        self.assertEqual(status.service.state, "connected")
        self.assertTrue(status.recording)
        self.assertEqual(len(status.channels), 18)
        self.assertIn("healthy", {c.state for c in status.channels})

    def test_rehearsal_twitcho_actions_change_status(self) -> None:
        twitcho = RehearsalTwitchoClient()

        mute = twitcho.action("mute")
        muted = twitcho.status()
        stop = twitcho.action("stop")
        stopped = twitcho.status()

        self.assertTrue(mute.ok)
        self.assertTrue(stop.ok)
        self.assertTrue(muted.muted)
        self.assertEqual(stopped.stream_state, "stopped")

    def test_rehearsal_system_reports_temperature(self) -> None:
        status = RehearsalSystemMonitor().status()

        self.assertEqual(status.temperature_c, 48.5)
        self.assertIsNone(status.temperature_error)


if __name__ == "__main__":
    unittest.main()
