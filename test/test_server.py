from __future__ import annotations

import unittest
from datetime import datetime, timezone
from io import BytesIO
from threading import BoundedSemaphore, Event, Lock, Thread
from unittest import mock

from showco import models, rehearsal
from showco.lyte import LyteClient
from showco.server import (
    ERROR_PAGE_LIMIT,
    MAX_WAVEFORM_CONNECTIONS,
    ShowcoApp,
    ShowcoHandler,
    actions_page,
    attributes_page,
    channels_page,
    errors_page,
    health_page,
)


class ServerTests(unittest.TestCase):
    @mock.patch("showco.server.source_revision", return_value="revision")
    def test_status_includes_server_revision(self, source_revision: mock.Mock) -> None:
        app = ShowcoApp(
            rehearsal.RehearsalRecsClient(),
            rehearsal.RehearsalTwitchoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
        )

        self.assertEqual(app.status().revision, "revision")
        source_revision.assert_called_once_with()

    def test_status_excludes_errors_from_before_the_server_started(self) -> None:
        recs = mock.Mock()
        recs.status.return_value = models.RecsStatus(
            service=models.ServiceStatus(name="recs", state="connected"),
            errors=[
                models.ErrorRecord(
                    timestamp="2026-09-03T18:00:00Z", message="old error"
                ),
                models.ErrorRecord(
                    timestamp="2026-09-03T18:10:00Z", message="new error"
                ),
                models.ErrorRecord(timestamp="", message="startup error"),
            ],
        )
        app = ShowcoApp(
            recs,
            None,
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
        )
        app.run_started_at = datetime(
            2026, 9, 3, 18, 5, tzinfo=timezone.utc
        ).timestamp()

        errors = app.status().recs.errors

        self.assertEqual([e.message for e in errors], ["new error", "startup error"])

    def test_html_is_not_cacheable(self) -> None:
        handler = object.__new__(ShowcoHandler)
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        handler.wfile = BytesIO()

        handler._html("page")

        handler.send_header.assert_any_call("Cache-Control", "no-store")

    def test_waveform_event_uses_server_sent_event_format(self) -> None:
        handler = object.__new__(ShowcoHandler)
        handler.wfile = BytesIO()

        handler._waveform_event("waveform", {"source": "Mixer"})

        self.assertEqual(
            handler.wfile.getvalue(),
            b'event: waveform\ndata: {"source":"Mixer"}\n\n',
        )

    def test_waveform_connection_limit_returns_service_unavailable(self) -> None:
        handler = object.__new__(ShowcoHandler)
        handler.app = mock.Mock(waveforms=object())
        handler.server = mock.Mock(
            waveform_slots=BoundedSemaphore(0),
        )
        handler.send_error = mock.Mock()

        handler._waveforms()

        handler.send_error.assert_called_once_with(503, "Too many waveform connections")

    def test_waveform_disconnect_releases_connection_slot(self) -> None:
        bridge = mock.Mock()
        bridge.snapshot.return_value = ([], [], 0)
        bridge.stopped = Event()
        bridge.wait_for_change.return_value = 1
        event = mock.Mock()
        event.model_dump.return_value = {}
        bridge.events_since.return_value = [(1, "waveform", event)]
        handler = object.__new__(ShowcoHandler)
        handler.app = mock.Mock(waveforms=bridge)
        handler.server = mock.Mock(
            waveform_slots=BoundedSemaphore(MAX_WAVEFORM_CONNECTIONS),
        )
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        handler._waveform_event = mock.Mock(side_effect=BrokenPipeError)

        handler._waveforms()

        self.assertTrue(handler.server.waveform_slots.acquire(blocking=False))

    def test_waveform_connections_do_not_use_normal_request_slots(self) -> None:
        handler = object.__new__(ShowcoHandler)
        handler.server = mock.Mock(
            request_slots=BoundedSemaphore(1),
            waveform_slots=BoundedSemaphore(MAX_WAVEFORM_CONNECTIONS),
        )
        handler.send_error = mock.Mock()
        for _ in range(MAX_WAVEFORM_CONNECTIONS):
            self.assertTrue(handler.server.waveform_slots.acquire(blocking=False))

        self.assertTrue(handler._acquire_request())
        handler.server.request_slots.release()

    def test_form_rejects_large_request(self) -> None:
        handler = object.__new__(ShowcoHandler)
        handler.headers = {"Content-Length": str(65_537)}

        with self.assertRaisesRegex(ValueError, "exceeds"):
            handler._form()

    def test_status_pages_have_five_page_navigation(self) -> None:
        html = channels_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name="recs", state="connected")
                ),
                twitcho=models.TwitchoStatus(
                    service=models.ServiceStatus(name="twitcho", state="offline")
                ),
            )
        )

        self.assertIn('href="/channels"', html)
        self.assertIn('href="/health"', html)
        self.assertIn('href="/attributes"', html)
        self.assertIn('href="/actions"', html)
        self.assertIn('href="/errors"', html)
        self.assertNotIn('href="/home"', html)

    def test_channels_page_has_live_status_elements(self) -> None:
        html = channels_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name="recs", state="connected")
                ),
                twitcho=models.TwitchoStatus(
                    service=models.ServiceStatus(name="twitcho", state="disabled")
                ),
            )
        )

        self.assertIn('id="channels"', html)
        self.assertIn('new EventSource("/waveforms")', html)
        self.assertIn("<script>  function serviceDetail(service)", html)
        self.assertIn("  const WAVEFORM_SECONDS = 8", html)
        self.assertIn(
            ".levels {\n  grid-template-columns: repeat(3, minmax(0, 1fr));", html
        )
        self.assertIn('fetch("/status"', html)

    def test_health_page_shows_pi_temperature(self) -> None:
        html = health_page(
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

    def test_health_page_shows_lyte_output_error(self) -> None:
        html = health_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name="recs", state="connected")
                ),
                twitcho=models.TwitchoStatus(
                    service=models.ServiceStatus(name="twitcho", state="disabled")
                ),
                lyte=models.LyteStatus(
                    service=models.ServiceStatus(
                        name="lyte",
                        state="error",
                        last_error="controller unreachable",
                    ),
                    daemon_state="streaming",
                    output_state="failed",
                ),
            )
        )

        self.assertIn('id="lyte-health"', html)
        self.assertIn("lyte: error: controller unreachable", html)

    def test_health_page_shows_bitrate_and_mixer_latency(self) -> None:
        html = health_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name="recs", state="connected")
                ),
                twitcho=models.TwitchoStatus(
                    service=models.ServiceStatus(name="twitcho", state="connected"),
                    output_bitrate_kbps=312.5,
                ),
                mixers=[
                    models.MixerStatus(name="X18", state="connected", latency_ms=4.25)
                ],
            )
        )

        self.assertIn("Twitch bitrate", html)
        self.assertIn("312 kbps", html)
        self.assertIn("X18: connected: 4.2 ms", html)
        self.assertIn("4.2 ms", html)

    def test_health_page_shows_named_mixer_input_progress(self) -> None:
        html = health_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name="recs", state="connected")
                ),
                twitcho=models.TwitchoStatus(
                    service=models.ServiceStatus(name="twitcho", state="connected")
                ),
                mixers=[
                    models.MixerStatus(
                        name="Flow 8",
                        state="waiting",
                        audio_ready=False,
                        midi_ready=False,
                    )
                ],
            )
        )

        self.assertIn("Flow 8: waiting for USB audio and MIDI", html)
        self.assertIn("function mixerDetail", html)

    def test_errors_page_shows_recs_errors_without_controls(self) -> None:
        html = errors_page(
            [
                models.ErrorRecord(
                    timestamp="2026-08-13T12:34:56.789Z",
                    message="disk almost full",
                )
            ]
        )

        self.assertIn("disk almost full", html)
        self.assertIn(f'data-limit="{ERROR_PAGE_LIMIT}"', html)
        self.assertNotIn("Show all errors", html)
        self.assertNotIn('type="checkbox" role="switch"', html)

    def test_errors_page_shows_empty_recs_errors(self) -> None:
        html = errors_page([])

        self.assertIn("No errors", html)

    def test_errors_page_limits_previous_errors(self) -> None:
        html = errors_page(
            [
                models.ErrorRecord(
                    timestamp=f"2026-08-13T12:34:{i:02}Z", message=str(i)
                )
                for i in range(ERROR_PAGE_LIMIT + 1)
            ]
        )

        self.assertNotIn(">0</span>", html)
        self.assertIn(f">{ERROR_PAGE_LIMIT}</span>", html)

    def test_channels_page_has_track_name_editor_for_recs_channels(self) -> None:
        html = channels_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name="recs", state="connected"),
                    channels=[
                        models.ChannelLevel(
                            name="1",
                            state="healthy",
                            device="Mic",
                            channels=[1],
                            on=True,
                        ),
                        models.ChannelLevel(
                            name="2", state="healthy", device="Mic", channels=[2]
                        ),
                    ],
                ),
                twitcho=models.TwitchoStatus(
                    service=models.ServiceStatus(name="twitcho", state="connected")
                ),
            )
        )

        self.assertIn('name="track_name" value="1"', html)
        self.assertIn('data-saved-track-name="1"', html)
        self.assertIn('class="channel-state indicator-red"', html)
        self.assertIn('aria-label="recording"', html)
        self.assertIn(
            '<canvas class="waveform" aria-label="Live waveform"></canvas>', html
        )
        self.assertIn("<b>1</b>", html)
        self.assertNotIn("Channel 1", html)
        self.assertIn(">•</span>", html)
        self.assertEqual(html.count('id="save-track-names"'), 1)
        self.assertEqual(html.count('id="revert-track-names"'), 1)
        self.assertEqual(html.count(">Save</button>"), 1)
        self.assertEqual(html.count(">Revert</button>"), 1)
        self.assertNotIn(">healthy</span>", html)
        self.assertIn(
            '<label class="stereo"><input type="checkbox">Stereo</label>', html
        )

    def test_channels_page_disables_mono_stereo_control_without_right_channel(
        self,
    ) -> None:
        html = channels_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name="recs", state="connected"),
                    channels=[
                        models.ChannelLevel(
                            name="2", state="healthy", device="Mic", channels=[2]
                        )
                    ],
                ),
                twitcho=models.TwitchoStatus(
                    service=models.ServiceStatus(name="twitcho", state="connected")
                ),
            )
        )

        self.assertIn(
            '<label class="stereo"><input type="checkbox" disabled>Stereo</label>',
            html,
        )

    def test_channels_page_checks_stereo_control(self) -> None:
        html = channels_page(
            models.ShowStatus(
                recs=models.RecsStatus(
                    service=models.ServiceStatus(name="recs", state="connected"),
                    channels=[
                        models.ChannelLevel(
                            name="1-2",
                            state="healthy",
                            device="Mic",
                            channels=[1, 2],
                        )
                    ],
                ),
                twitcho=models.TwitchoStatus(
                    service=models.ServiceStatus(name="twitcho", state="connected")
                ),
            )
        )

        self.assertIn(
            '<label class="stereo"><input type="checkbox" checked>Stereo</label>',
            html,
        )

    def test_channels_page_shows_not_recording_channel_light(self) -> None:
        html = channels_page(
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

        self.assertIn('class="channel-state indicator-green"', html)
        self.assertIn('aria-label="not recording"', html)

    def test_attributes_page_has_mutable_recs_attributes(self) -> None:
        html = attributes_page(
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
            rehearsal.RehearsalMixersMonitor(),
        )

        status = app.status()

        self.assertEqual(status.twitcho.service.state, "disabled")

    def test_disabled_twitcho_rejects_actions(self) -> None:
        app = ShowcoApp(
            rehearsal.RehearsalRecsClient(),
            None,
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
        )

        result = app.run_action({"action": "twitcho-mute"})

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "twitcho is disabled")

    def test_actions_page_has_recs_protocol_controls(self) -> None:
        html = actions_page([])

        self.assertIn('value="recs-disk-status"', html)
        self.assertIn('value="recs-list-devices"', html)
        self.assertIn('value="recs-pause-recording"', html)
        self.assertIn('value="recs-resume-recording"', html)
        self.assertNotIn('value="recs-stop-recording"', html)
        self.assertNotIn('value="recs-start-recording"', html)
        self.assertIn('value="recs-marker"', html)
        self.assertIn('value="recs-set-noise-floor"', html)
        self.assertIn('value="recs-shutdown"', html)
        self.assertIn('<option value="cancel" selected>Cancel</option>', html)

    def test_actions_page_has_lyte_light_test(self) -> None:
        html = actions_page([])

        self.assertIn('value="lyte-test"', html)
        self.assertIn(">Test lights</button>", html)
        self.assertIn('button.setAttribute("aria-busy", "true")', html)
        self.assertIn('button:active, button[aria-busy="true"]', html)

    def test_lyte_light_test_uses_lyte_client(self) -> None:
        app = ShowcoApp(
            rehearsal.RehearsalRecsClient(),
            rehearsal.RehearsalTwitchoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
        )
        lyte = mock.Mock(spec=LyteClient)
        expected = models.ActionResult(ok=True, message="lyte light test queued")
        lyte.test.return_value = expected
        app.lyte = lyte

        result = app.run_action({"action": "lyte-test"})

        self.assertEqual(result, expected)
        lyte.test.assert_called_once_with()

    def test_twitch_restart_action_uses_service_restart(self) -> None:
        restart = mock.Mock(
            return_value=models.ActionResult(
                ok=True,
                message="twitcho restart requested",
            )
        )
        app = ShowcoApp(
            rehearsal.RehearsalRecsClient(),
            rehearsal.RehearsalTwitchoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
            restart,
        )

        result = app.run_action({"action": "twitcho-restart"})

        self.assertTrue(result.ok)
        restart.assert_called_once_with()

    def test_track_name_action_uses_recs_client(self) -> None:
        recs = rehearsal.RehearsalRecsClient()
        app = ShowcoApp(
            recs,
            rehearsal.RehearsalTwitchoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
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
            rehearsal.RehearsalMixersMonitor(),
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

    def test_set_stereo_action_uses_recs_client(self) -> None:
        recs = rehearsal.RehearsalRecsClient()
        app = ShowcoApp(
            recs,
            rehearsal.RehearsalTwitchoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
        )

        result = app.run_action(
            {"action": "recs-set-stereo", "device": "X18/XR18", "channels": "1"}
        )

        self.assertTrue(result.ok)
        self.assertIn([1, 2], recs.rehearsal_tracks)

    def test_recs_action_uses_recs_client(self) -> None:
        recs = rehearsal.RehearsalRecsClient()
        app = ShowcoApp(
            recs,
            rehearsal.RehearsalTwitchoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
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
            rehearsal.RehearsalMixersMonitor(),
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
            rehearsal.RehearsalMixersMonitor(),
        )

        result = app.run_action({"action": "recs-shutdown"})

        self.assertTrue(result.ok)
        self.assertEqual(result.message, "recs shutdown canceled")

    def test_action_log_keeps_ten_most_recent_results(self) -> None:
        app = ShowcoApp(
            rehearsal.RehearsalRecsClient(),
            rehearsal.RehearsalTwitchoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
        )

        for i in range(12):
            app.run_action({"action": f"unknown-{i}"})

        messages = [r.message for r in app.recent_actions()]
        self.assertEqual(len(messages), 10)
        self.assertEqual(messages[0], "unknown action unknown-11")
        self.assertEqual(messages[-1], "unknown action unknown-2")

    def test_actions_do_not_overlap(self) -> None:
        calls = 0
        calls_lock = Lock()
        entered = Event()
        release = Event()
        second_complete = Event()

        def calibrate() -> models.ActionResult:
            nonlocal calls
            with calls_lock:
                calls += 1
            entered.set()
            release.wait(1)
            return models.ActionResult(ok=True, message="calibrated")

        recs = mock.Mock()
        recs.calibrate.side_effect = calibrate
        app = ShowcoApp(
            recs,
            rehearsal.RehearsalTwitchoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
        )

        def run_second_action() -> None:
            app.run_action({"action": "recs-calibrate"})
            second_complete.set()

        first = Thread(target=app.run_action, args=({"action": "recs-calibrate"},))
        second = Thread(target=run_second_action)

        first.start()
        self.assertTrue(entered.wait(1))
        second.start()
        try:
            self.assertFalse(second_complete.wait(0.05))
            self.assertEqual(calls, 1)
        finally:
            release.set()
        first.join(1)
        second.join(1)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(calls, 2)

    def test_failed_action_result_logs_error(self) -> None:
        handler = ShowcoHandler.__new__(ShowcoHandler)
        handler.client_address = ("127.0.0.1", 12345)

        with self.assertLogs("showco.server", level="ERROR") as logs:
            handler._log_action(
                "recs-calibrate",
                models.ActionResult(ok=False, message="I/O operation on closed file."),
            )

        self.assertIn("action='recs-calibrate'", logs.output[0])
        self.assertIn("ok=False", logs.output[0])
        self.assertIn("I/O operation on closed file.", logs.output[0])


if __name__ == "__main__":
    unittest.main()
