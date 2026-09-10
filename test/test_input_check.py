from __future__ import annotations

import unittest

from showco.runtime import input_check, models


class InputCheckTests(unittest.TestCase):
    def test_checks_recording_channels_only(self) -> None:
        checks = input_check.checks(
            [
                models.ChannelLevel(name="1", state="silent", device="Mic", on=True),
                models.ChannelLevel(name="2", state="present", device="Mic", on=True),
                models.ChannelLevel(name="3", state="silent", device="Mic"),
            ]
        )

        self.assertEqual(
            checks,
            [
                models.InputCheck(name="Mic 1", ok=False, message="silent"),
                models.InputCheck(name="Mic 2", ok=True, message="signal present"),
            ],
        )
