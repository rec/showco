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
from showco.rehearsal import (
    RehearsalMixerMonitor,
    RehearsalRecsClient,
    RehearsalSystemMonitor,
    RehearsalTwitchoClient,
    RehearsalTwitchoSupervisor,
)
from showco.server import ShowcoApp, actions_page, home_page


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

    def test_actions_page_has_twitch_restart_button(self) -> None:
        html = actions_page([])

        self.assertIn("Restart Twitch", html)
        self.assertIn('value="twitcho-restart"', html)

    def test_twitch_restart_action_uses_supervisor(self) -> None:
        supervisor = RehearsalTwitchoSupervisor()
        app = ShowcoApp(
            RehearsalRecsClient(),
            RehearsalTwitchoClient(),
            RehearsalSystemMonitor(),
            RehearsalMixerMonitor(),
            supervisor,
        )

        result = app.run_action({"action": "twitcho-restart"})

        self.assertTrue(result.ok)
        self.assertEqual(supervisor.restart_count, 1)

    def test_action_log_keeps_ten_most_recent_results(self) -> None:
        app = ShowcoApp(
            RehearsalRecsClient(),
            RehearsalTwitchoClient(),
            RehearsalSystemMonitor(),
            RehearsalMixerMonitor(),
        )

        for i in range(12):
            app.run_action({"action": f"unknown-{i}"})

        messages = [r.message for r in app.recent_actions()]
        self.assertEqual(len(messages), 10)
        self.assertEqual(messages[0], "unknown action unknown-11")
        self.assertEqual(messages[-1], "unknown action unknown-2")


if __name__ == "__main__":
    unittest.main()
