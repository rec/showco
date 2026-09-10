from __future__ import annotations

import unittest

from showco.runtime import incidents, models


class IncidentTimelineTests(unittest.TestCase):
    def test_records_changed_state_after_initial_observation(self) -> None:
        timeline = incidents.IncidentTimeline()
        timeline.observe(status())

        observed = timeline.observe(status(paused=True))

        self.assertEqual(observed[0].message, "Recording: recording to paused")


def status(*, paused: bool = False) -> models.ShowStatus:
    return models.ShowStatus(
        recs=models.RecsStatus(
            service=models.ServiceStatus(name="recs", state="connected"),
            recording=True,
            paused=paused,
        ),
        twitcho=models.TwitchoStatus(
            service=models.ServiceStatus(name="twitcho", state="disabled")
        ),
    )
