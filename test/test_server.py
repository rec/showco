from __future__ import annotations

import unittest

from showco.models import (
    ChannelLevel,
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
                recs=RecsStatus(service=ServiceStatus(name="recs", state="connected")),
                twitcho=TwitchoStatus(
                    service=ServiceStatus(name="twitcho", state="offline")
                ),
            )
        )

        self.assertIn('href="/home"', html)
        self.assertIn('href="/actions"', html)
        self.assertNotIn('href="/levels"', html)

    def test_home_page_shows_pi_temperature(self) -> None:
        html = home_page(
            ShowStatus(
                recs=RecsStatus(service=ServiceStatus(name="recs", state="connected")),
                twitcho=TwitchoStatus(
                    service=ServiceStatus(name="twitcho", state="connected")
                ),
                system=SystemStatus(temperature_c=52.75),
            )
        )

        self.assertIn("Pi temperature", html)
        self.assertIn("52.8 °C", html)

    def test_home_page_shows_bitrate_and_mixer_latency(self) -> None:
        html = home_page(
            ShowStatus(
                recs=RecsStatus(service=ServiceStatus(name="recs", state="connected")),
                twitcho=TwitchoStatus(
                    service=ServiceStatus(name="twitcho", state="connected"),
                    output_bitrate_kbps=312.5,
                ),
                mixer=MixerStatus(latency_ms=4.25),
            )
        )

        self.assertIn("Twitch bitrate", html)
        self.assertIn("312 kbps", html)
        self.assertIn("Mixer latency", html)
        self.assertIn("4.2 ms", html)

    def test_home_page_shows_recs_errors(self) -> None:
        html = home_page(
            ShowStatus(
                recs=RecsStatus(
                    service=ServiceStatus(name="recs", state="connected"),
                    errors=["disk almost full"],
                ),
                twitcho=TwitchoStatus(
                    service=ServiceStatus(name="twitcho", state="connected")
                ),
            )
        )

        self.assertIn("Recs errors", html)
        self.assertIn("disk almost full", html)

    def test_home_page_has_track_name_editor_for_recs_channels(self) -> None:
        html = home_page(
            ShowStatus(
                recs=RecsStatus(
                    service=ServiceStatus(name="recs", state="connected"),
                    channels=[
                        ChannelLevel(name="1", state="healthy", device="Mic"),
                    ],
                ),
                twitcho=TwitchoStatus(
                    service=ServiceStatus(name="twitcho", state="connected")
                ),
            )
        )

        self.assertIn('value="recs-track-name"', html)
        self.assertIn('method="post" action="/actions"', html)
        self.assertIn('name="device" value="Mic"', html)
        self.assertIn('name="channel" value="1"', html)
        self.assertIn('name="track_name" value="1"', html)

    def test_actions_page_has_twitch_restart_button(self) -> None:
        html = actions_page([])

        self.assertIn("Restart Twitch", html)
        self.assertIn('value="twitcho-restart"', html)

    def test_actions_page_has_recs_protocol_controls(self) -> None:
        html = actions_page([])

        self.assertIn('value="recs-disk-status"', html)
        self.assertIn('value="recs-list-devices"', html)
        self.assertIn('value="recs-pause-recording"', html)
        self.assertIn('value="recs-marker"', html)
        self.assertIn('value="recs-set-noise-floor"', html)
        self.assertIn('value="recs-shutdown"', html)
        self.assertIn('<option value="cancel" selected>Cancel</option>', html)

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

    def test_track_name_action_uses_recs_client(self) -> None:
        recs = RehearsalRecsClient()
        app = ShowcoApp(
            recs,
            RehearsalTwitchoClient(),
            RehearsalSystemMonitor(),
            RehearsalMixerMonitor(),
        )

        result = app.run_action(
            {
                "action": "recs-track-name",
                "device": "X18/XR18",
                "channel": "1",
                "track_name": "Lead Vocal",
            }
        )

        self.assertTrue(result.ok)
        self.assertEqual(recs.rehearsal_track_names, {"X18/XR18": {"Lead Vocal": 1}})

    def test_recs_action_uses_recs_client(self) -> None:
        recs = RehearsalRecsClient()
        app = ShowcoApp(
            recs,
            RehearsalTwitchoClient(),
            RehearsalSystemMonitor(),
            RehearsalMixerMonitor(),
        )

        result = app.run_action(
            {
                "action": "recs-set-noise-floor",
                "source": "Mic",
                "noise_floor": "42.5",
            }
        )

        self.assertTrue(result.ok)
        self.assertIn("set_noise_floor", result.message)

    def test_recs_action_reports_invalid_noise_floor(self) -> None:
        app = ShowcoApp(
            RehearsalRecsClient(),
            RehearsalTwitchoClient(),
            RehearsalSystemMonitor(),
            RehearsalMixerMonitor(),
        )

        result = app.run_action(
            {
                "action": "recs-set-noise-floor",
                "source": "Mic",
                "noise_floor": "loud",
            }
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "noise_floor must be a number")

    def test_shutdown_action_defaults_to_cancel(self) -> None:
        app = ShowcoApp(
            RehearsalRecsClient(),
            RehearsalTwitchoClient(),
            RehearsalSystemMonitor(),
            RehearsalMixerMonitor(),
        )

        result = app.run_action({"action": "recs-shutdown"})

        self.assertTrue(result.ok)
        self.assertEqual(result.message, "recs shutdown canceled")

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
