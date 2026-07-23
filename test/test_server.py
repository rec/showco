from __future__ import annotations

import unittest

from showco.models import (
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


if __name__ == "__main__":
    unittest.main()
