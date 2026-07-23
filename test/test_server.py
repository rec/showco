from __future__ import annotations

import unittest

from showco.models import (
    MixerStatus,
    RecsStatus,
    ServiceStatus,
    ShowStatus,
    SystemStatus,
    TwitchoStatus,
)
from showco.server import home_page


class ServerTests(unittest.TestCase):
    def test_home_page_has_two_screen_navigation(self) -> None:
        html = home_page(
            ShowStatus(
                recs=RecsStatus(service=ServiceStatus("recs", "connected")),
                twitcho=TwitchoStatus(service=ServiceStatus("twitcho", "offline")),
            )
        )

        self.assertIn('href="/home"', html)
        self.assertIn('href="/actions"', html)
        self.assertNotIn('href="/levels"', html)

    def test_home_page_shows_pi_temperature(self) -> None:
        html = home_page(
            ShowStatus(
                recs=RecsStatus(service=ServiceStatus("recs", "connected")),
                twitcho=TwitchoStatus(service=ServiceStatus("twitcho", "connected")),
                system=SystemStatus(temperature_c=52.75),
            )
        )

        self.assertIn("Pi temperature", html)
        self.assertIn("52.8 °C", html)

    def test_home_page_shows_bitrate_and_mixer_latency(self) -> None:
        html = home_page(
            ShowStatus(
                recs=RecsStatus(service=ServiceStatus("recs", "connected")),
                twitcho=TwitchoStatus(
                    service=ServiceStatus("twitcho", "connected"),
                    output_bitrate_kbps=312.5,
                ),
                mixer=MixerStatus(latency_ms=4.25),
            )
        )

        self.assertIn("Twitch bitrate", html)
        self.assertIn("312 kbps", html)
        self.assertIn("Mixer latency", html)
        self.assertIn("4.2 ms", html)


if __name__ == "__main__":
    unittest.main()
