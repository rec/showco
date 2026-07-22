from __future__ import annotations

import unittest

from showco.models import RecsStatus, ServiceStatus, ShowStatus, TwitchoStatus
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


if __name__ == "__main__":
    unittest.main()
