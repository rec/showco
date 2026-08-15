from __future__ import annotations

import unittest
from io import BytesIO
from unittest import mock

from showco import models, rehearsal
from showco.server import ShowcoApp, ShowcoHandler, actions_page, home_page


class ServerTests(unittest.TestCase):
    @mock.patch("showco.server.source_revision", return_value="revision")
    def test_status_includes_server_revision(self, source_revision: mock.Mock) -> None:
        app = ShowcoApp(
            rehearsal.RehearsalRecsClient(),
            rehearsal.RehearsalTwitchoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixerMonitor(),
        )

        self.assertEqual(app.status().revision, "revision")
        source_revision.assert_called_once_with()

    def test_html_is_not_cacheable(self) -> None:
        handler = object.__new__(ShowcoHandler)
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        handler.wfile = BytesIO()

        handler._html("page")

        handler.send_header.assert_any_call("Cache-Control", "no-store")

    def test_home_page_has_two_screen_navigation(self) -> None:
        html = home_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name="recs", state="connected")
                ),
                twitcho=models.TwitchoStatus(
                    service=models.ServiceStatus(name="twitcho", state="offline")
                ),
            )
        )

        self.assertIn('href="/home"', html)
        self.assertIn('href="/actions"', html)
        self.assertNotIn('href="/levels"', html)

    def test_home_page_has_live_status_elements(self) -> None:
        html = home_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name="recs", state="connected")
                ),
                twitcho=models.TwitchoStatus(
                    service=models.ServiceStatus(name="twitcho", state="disabled")
                ),
            )
        )

        self.assertIn('id="recording-card"', html)
        self.assertIn('id="channels"', html)
        self.assertIn('id="generated-at"', html)
        self.assertIn('fetch("/status"', html)

    def test_home_page_shows_pi_temperature(self) -> None:
        html = home_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name="recs", state="connected")
                ),
                twitcho=models.TwitchoStatus(
                    service=models.ServiceStatus(name="twitcho", state="connected")
                ),
                system=models.SystemStatus(temperature_c=52.75),
            )
        )

        self.assertIn("Pi temperature", html)
        self.assertIn("52.8 °C", html)

    def test_home_page_shows_bitrate_and_mixer_latency(self) -> None:
        html = home_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name="recs", state="connected")
                ),
                twitcho=models.TwitchoStatus(
                    service=models.ServiceStatus(name="twitcho", state="connected"),
                    output_bitrate_kbps=312.5,
                ),
                mixer=models.MixerStatus(latency_ms=4.25),
            )
        )

        self.assertIn("Twitch bitrate", html)
        self.assertIn("312 kbps", html)
        self.assertIn("Mixer latency", html)
        self.assertIn("4.2 ms", html)

    def test_home_page_shows_recs_errors(self) -> None:
        html = home_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(
                        name="recs",
                        state="connected",
                        updated_at=1_785_000_000,
                    ),
                    errors=[
                        models.ErrorRecord(
                            timestamp="2026-08-13T12:34:56.789Z",
                            message="disk almost full",
                        )
                    ],
                ),
                twitcho=models.TwitchoStatus(
                    service=models.ServiceStatus(name="twitcho", state="connected")
                ),
            )
        )

        self.assertIn("Recs errors", html)
        self.assertIn("disk almost full", html)
        self.assertIn(
            '<input id="show-all-errors" type="checkbox" role="switch">', html
        )
        self.assertIn("Show all errors", html)

    def test_home_page_hides_errors_from_before_this_run(self) -> None:
        html = home_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name="recs", state="connected"),
                    errors=[
                        models.ErrorRecord(
                            timestamp="2026-08-13T12:34:56.789Z",
                            message="old disk error",
                        )
                    ],
                ),
                twitcho=models.TwitchoStatus(
                    service=models.ServiceStatus(name="twitcho", state="connected")
                ),
                run_started_at=1_800_000_000,
            )
        )

        self.assertNotIn("old disk error", html)

    def test_home_page_has_track_name_editor_for_recs_channels(self) -> None:
        html = home_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name="recs", state="connected"),
                    channels=[
                        models.ChannelLevel(name="1", state="healthy", device="Mic"),
                    ],
                ),
                twitcho=models.TwitchoStatus(
                    service=models.ServiceStatus(name="twitcho", state="connected")
                ),
            )
        )

        self.assertIn('name="track_name" value="1"', html)
        self.assertIn('data-saved-track-name="1"', html)
        self.assertIn('class="channel-state indicator-green"', html)
        self.assertIn("Channel 1", html)
        self.assertIn(">•</span>", html)
        self.assertEqual(html.count('id="save-track-names"'), 1)
        self.assertEqual(html.count('id="revert-track-names"'), 1)
        self.assertEqual(html.count(">Save</button>"), 1)
        self.assertEqual(html.count(">Revert</button>"), 1)
        self.assertNotIn(">healthy</span>", html)

    def test_home_page_has_mutable_recs_attributes(self) -> None:
        html = home_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name="recs", state="connected")
                ),
                twitcho=models.TwitchoStatus(
                    service=models.ServiceStatus(name="twitcho", state="connected")
                ),
            ),
            [
                models.MutableAttribute(
                    address="recording.noise_floor",
                    value=70.0,
                ),
                models.MutableAttribute(
                    address="recording.record_everything",
                    value=False,
                ),
            ],
        )

        self.assertIn("Recs attributes", html)
        self.assertIn('id="mutable-attributes"', html)
        self.assertIn('data-address="recording.noise_floor"', html)
        self.assertIn('type="number" data-value-type="number" value="70.0"', html)
        self.assertIn('type="checkbox" data-value-type="boolean"', html)
        self.assertIn("saveMutableAttribute", html)

    def test_actions_page_has_twitch_restart_button(self) -> None:
        html = actions_page([])

        self.assertIn("Restart Twitch", html)
        self.assertIn('value="twitcho-restart"', html)

    def test_actions_page_hides_twitch_controls_when_disabled(self) -> None:
        html = actions_page([], twitcho_enabled=False)

        self.assertNotIn("Restart Twitch", html)
        self.assertNotIn('value="twitcho-mute"', html)

    def test_disabled_twitcho_does_not_request_status(self) -> None:
        app = ShowcoApp(
            rehearsal.RehearsalRecsClient(),
            None,
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixerMonitor(),
        )

        status = app.status()

        self.assertEqual(status.twitcho.service.state, "disabled")

    def test_disabled_twitcho_rejects_actions(self) -> None:
        app = ShowcoApp(
            rehearsal.RehearsalRecsClient(),
            None,
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixerMonitor(),
        )

        result = app.run_action({"action": "twitcho-mute"})

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "twitcho is disabled")

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
        supervisor = rehearsal.RehearsalTwitchoSupervisor()
        app = ShowcoApp(
            rehearsal.RehearsalRecsClient(),
            rehearsal.RehearsalTwitchoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixerMonitor(),
            supervisor,
        )

        result = app.run_action({"action": "twitcho-restart"})

        self.assertTrue(result.ok)
        self.assertEqual(supervisor.restart_count, 1)

    def test_track_name_action_uses_recs_client(self) -> None:
        recs = rehearsal.RehearsalRecsClient()
        app = ShowcoApp(
            recs,
            rehearsal.RehearsalTwitchoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixerMonitor(),
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

    def test_set_attr_action_uses_recs_client(self) -> None:
        recs = rehearsal.RehearsalRecsClient()
        app = ShowcoApp(
            recs,
            rehearsal.RehearsalTwitchoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixerMonitor(),
        )

        result = app.run_action(
            {
                "action": "recs-set-attr",
                "address": "recording.record_everything",
                "value": "true",
            }
        )

        self.assertTrue(result.ok)
        self.assertTrue(recs.rehearsal_attributes["recording.record_everything"])

    def test_recs_action_uses_recs_client(self) -> None:
        recs = rehearsal.RehearsalRecsClient()
        app = ShowcoApp(
            recs,
            rehearsal.RehearsalTwitchoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixerMonitor(),
        )

        result = app.run_action(
            {
                "action": "recs-set-noise-floor",
                "source": "Mic",
                "channel": "1",
                "noise_floor": "42.5",
            }
        )

        self.assertTrue(result.ok)
        self.assertIn("set_noise_floor", result.message)

    def test_recs_action_reports_invalid_noise_floor(self) -> None:
        app = ShowcoApp(
            rehearsal.RehearsalRecsClient(),
            rehearsal.RehearsalTwitchoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixerMonitor(),
        )

        result = app.run_action(
            {
                "action": "recs-set-noise-floor",
                "source": "Mic",
                "channel": "1",
                "noise_floor": "loud",
            }
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "noise_floor must be a number")

    def test_shutdown_action_defaults_to_cancel(self) -> None:
        app = ShowcoApp(
            rehearsal.RehearsalRecsClient(),
            rehearsal.RehearsalTwitchoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixerMonitor(),
        )

        result = app.run_action({"action": "recs-shutdown"})

        self.assertTrue(result.ok)
        self.assertEqual(result.message, "recs shutdown canceled")

    def test_action_log_keeps_ten_most_recent_results(self) -> None:
        app = ShowcoApp(
            rehearsal.RehearsalRecsClient(),
            rehearsal.RehearsalTwitchoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixerMonitor(),
        )

        for i in range(12):
            app.run_action({"action": f"unknown-{i}"})

        messages = [r.message for r in app.recent_actions()]
        self.assertEqual(len(messages), 10)
        self.assertEqual(messages[0], "unknown action unknown-11")
        self.assertEqual(messages[-1], "unknown action unknown-2")


if __name__ == "__main__":
    unittest.main()
