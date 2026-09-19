from __future__ import annotations

import unittest
from datetime import datetime, timezone
from io import BytesIO
from threading import BoundedSemaphore, Event, Lock, Thread
from unittest import mock

from showco.runtime import models, rehearsal
from showco.runtime.lyte import LyteClient
from showco.runtime.server import (
    MAX_WAVEFORM_CONNECTIONS,
    ShowcoApp,
    ShowcoHandler,
)


class ServerTests(unittest.TestCase):
    @mock.patch('showco.runtime.server.source_revision', return_value='revision')
    def test_status_includes_server_revision(self, source_revision: mock.Mock) -> None:
        app = ShowcoApp(
            rehearsal.RehearsalRecsClient(),
            rehearsal.RehearsalStreamoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
        )

        self.assertEqual(app.status().revision, 'revision')
        source_revision.assert_called_once_with()

    def test_status_excludes_errors_from_before_the_server_started(self) -> None:
        recs = mock.Mock()
        recs.status.return_value = models.RecsStatus(
            service=models.ServiceStatus(name='recs', state='connected'),
            errors=[
                models.ErrorRecord(
                    timestamp='2026-09-03T18:00:00Z', message='old error'
                ),
                models.ErrorRecord(
                    timestamp='2026-09-03T18:10:00Z', message='new error'
                ),
                models.ErrorRecord(timestamp='', message='startup error'),
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

        self.assertEqual([e.message for e in errors], ['new error', 'startup error'])

    def test_html_is_not_cacheable(self) -> None:
        handler = object.__new__(ShowcoHandler)
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        handler.wfile = BytesIO()

        handler._html('page')

        handler.send_header.assert_any_call('Cache-Control', 'no-store')

    def test_waveform_event_uses_server_sent_event_format(self) -> None:
        handler = object.__new__(ShowcoHandler)
        handler.wfile = BytesIO()

        handler._waveform_event('waveform', {'source': 'Mixer'})

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

        handler.send_error.assert_called_once_with(503, 'Too many waveform connections')

    def test_waveform_disconnect_releases_connection_slot(self) -> None:
        bridge = mock.Mock()
        bridge.snapshot.return_value = ([], [], 0)
        bridge.stopped = Event()
        bridge.wait_for_change.return_value = 1
        event = mock.Mock()
        event.model_dump.return_value = {}
        bridge.events_since.return_value = (False, [(1, 'waveform', event)])
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

    def test_missed_waveform_events_resend_the_current_snapshot(self) -> None:
        layout = mock.Mock()
        layout.model_dump.return_value = {'source': 'Mixer'}
        batch = mock.Mock()
        batch.model_dump.return_value = {'source': 'Mixer'}
        bridge = mock.Mock()
        bridge.stopped = Event()
        bridge.snapshot.side_effect = [([], [], 0), ([layout], [batch], 3)]

        def wait_for_change(changed: int, timeout: float) -> int:
            bridge.stopped.set()
            return 3

        bridge.wait_for_change.side_effect = wait_for_change
        bridge.events_since.return_value = (True, [])
        handler = object.__new__(ShowcoHandler)
        handler.app = mock.Mock(waveforms=bridge)
        handler.server = mock.Mock(
            waveform_slots=BoundedSemaphore(MAX_WAVEFORM_CONNECTIONS),
        )
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        handler._waveform_event = mock.Mock()

        handler._waveforms()

        self.assertEqual(
            handler._waveform_event.call_args_list,
            [
                mock.call('waveform_resync', {}),
                mock.call('waveform_layout', {'source': 'Mixer'}),
                mock.call('waveform', {'source': 'Mixer'}),
            ],
        )

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
        handler.headers = {'Content-Length': str(65_537)}

        with self.assertRaisesRegex(ValueError, 'exceeds'):
            handler._form()

    def test_invalid_utf8_action_request_returns_bad_request(self) -> None:
        handler = object.__new__(ShowcoHandler)
        handler.path = '/actions'
        handler.headers = {'Content-Length': '1'}
        handler.rfile = BytesIO(b'\xff')
        handler.send_error = mock.Mock()

        handler._do_post()

        handler.send_error.assert_called_once_with(
            400, 'action body is not valid UTF-8'
        )

    def test_malformed_action_request_returns_bad_request(self) -> None:
        handler = object.__new__(ShowcoHandler)
        handler.path = '/actions'
        handler.headers = {'Content-Length': '6'}
        handler.rfile = BytesIO(b'action')
        handler.send_error = mock.Mock()

        handler._do_post()

        handler.send_error.assert_called_once_with(400, 'action body is malformed')

    def test_invalid_content_length_returns_payload_too_large(self) -> None:
        handler = object.__new__(ShowcoHandler)
        handler.path = '/actions'
        handler.headers = {'Content-Length': 'unknown'}
        handler.rfile = BytesIO()
        handler.send_error = mock.Mock()

        handler._do_post()

        handler.send_error.assert_called_once_with(413, 'invalid Content-Length')

    def test_channel_calibration_passes_device_and_channels(self) -> None:
        recs = mock.Mock()
        recs.calibrate.return_value = models.ActionResult(ok=True, message='calibrated')
        app = ShowcoApp(
            recs,
            rehearsal.RehearsalStreamoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
        )

        result = app.run_action(
            {'action': 'recs-calibrate', 'device': 'Mic', 'channels': '1,2'}
        )

        self.assertTrue(result.ok)
        recs.calibrate.assert_called_once_with('Mic', [1, 2])

    def test_disabled_streamo_does_not_request_status(self) -> None:
        app = ShowcoApp(
            rehearsal.RehearsalRecsClient(),
            None,
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
        )

        status = app.status()

        self.assertEqual(status.streamo.service.state, 'disabled')

    def test_disabled_streamo_rejects_actions(self) -> None:
        app = ShowcoApp(
            rehearsal.RehearsalRecsClient(),
            None,
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
        )

        result = app.run_action({'action': 'streamo-mute'})

        self.assertFalse(result.ok)
        self.assertEqual(result.message, 'streamo is disabled')

    def test_playback_actions_send_typed_recs_parameters(self) -> None:
        recs = mock.Mock()
        recs.action.return_value = models.ActionResult(ok=True, message='jumped')
        app = ShowcoApp(
            recs,
            None,
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
        )

        result = app.run_action({'action': 'recs-playback-jump', 'seconds': '-10'})

        self.assertTrue(result.ok)
        recs.action.assert_called_once_with('jump_playback', seconds=-10.0)

    def test_cable_test_action_reports_each_pair(self) -> None:
        tester = mock.Mock()
        tester.run.return_value = mock.Mock(
            passed=False,
            message=mock.Mock(return_value='Cable test: 1/2 passed'),
        )
        app = ShowcoApp(
            mock.Mock(),
            None,
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
            cable_tester=tester,
        )

        result = app.run_action(
            {'action': 'cable-test', 'channels': '9-10', 'sends': '1-2'}
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.message, 'Cable test: 1/2 passed')
        tester.run.assert_called_once_with([9, 10], [1, 2])

    def test_lyte_light_test_uses_lyte_client(self) -> None:
        app = ShowcoApp(
            rehearsal.RehearsalRecsClient(),
            rehearsal.RehearsalStreamoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
        )
        lyte = mock.Mock(spec=LyteClient)
        expected = models.ActionResult(ok=True, message='lyte light test queued')
        lyte.test.return_value = expected
        app.lyte = lyte

        result = app.run_action({'action': 'lyte-test'})

        self.assertEqual(result, expected)
        lyte.test.assert_called_once_with()

    def test_lyte_reconnection_does_not_trigger_light_tests(self) -> None:
        lyte = mock.Mock(spec=LyteClient)
        offline = models.LyteStatus(
            service=models.ServiceStatus(name='lyte', state='offline')
        )
        online = models.LyteStatus(
            service=models.ServiceStatus(name='lyte', state='connected')
        )
        lyte.status.side_effect = [offline, online, online, offline, online]
        lyte.test.return_value = models.ActionResult(
            ok=True, message='lyte light test queued'
        )
        app = ShowcoApp(
            rehearsal.RehearsalRecsClient(),
            rehearsal.RehearsalStreamoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
            lyte=lyte,
        )

        for _ in range(5):
            app.status()

        lyte.test.assert_not_called()

    def test_stream_restart_action_uses_service_restart(self) -> None:
        restart = mock.Mock(
            return_value=models.ActionResult(
                ok=True,
                message='streamo restart requested',
            )
        )
        app = ShowcoApp(
            rehearsal.RehearsalRecsClient(),
            rehearsal.RehearsalStreamoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
            restart,
        )

        result = app.run_action({'action': 'streamo-restart'})

        self.assertTrue(result.ok)
        restart.assert_called_once_with()

    def test_track_name_action_uses_recs_client(self) -> None:
        recs = rehearsal.RehearsalRecsClient()
        app = ShowcoApp(
            recs,
            rehearsal.RehearsalStreamoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
        )

        result = app.run_action(
            {
                'action': 'recs-track-name',
                'device': 'X18/XR18',
                'channel': '1',
                'track_name': 'Lead Vocal',
            }
        )

        self.assertTrue(result.ok)
        self.assertEqual(recs.rehearsal_track_names, {'X18/XR18': {'Lead Vocal': 1}})

    def test_set_attr_action_uses_recs_client(self) -> None:
        recs = rehearsal.RehearsalRecsClient()
        app = ShowcoApp(
            recs,
            rehearsal.RehearsalStreamoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
        )

        result = app.run_action(
            {
                'action': 'recs-set-attr',
                'address': 'recording.record_everything',
                'value': 'true',
            }
        )

        self.assertTrue(result.ok)
        self.assertTrue(recs.rehearsal_attributes['recording.record_everything'])

    def test_set_stereo_action_uses_recs_client(self) -> None:
        recs = rehearsal.RehearsalRecsClient()
        app = ShowcoApp(
            recs,
            rehearsal.RehearsalStreamoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
        )

        result = app.run_action(
            {'action': 'recs-set-stereo', 'device': 'X18/XR18', 'channels': '1'}
        )

        self.assertTrue(result.ok)
        self.assertIn([1, 2], recs.rehearsal_tracks)

    def test_recs_action_uses_recs_client(self) -> None:
        recs = rehearsal.RehearsalRecsClient()
        app = ShowcoApp(
            recs,
            rehearsal.RehearsalStreamoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
        )

        result = app.run_action(
            {
                'action': 'recs-set-noise-floor',
                'source': 'Mic',
                'channel': '1',
                'noise_floor': '42.5',
            }
        )

        self.assertTrue(result.ok)
        self.assertIn('set_noise_floor', result.message)

    def test_recs_action_reports_invalid_noise_floor(self) -> None:
        app = ShowcoApp(
            rehearsal.RehearsalRecsClient(),
            rehearsal.RehearsalStreamoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
        )

        result = app.run_action(
            {
                'action': 'recs-set-noise-floor',
                'source': 'Mic',
                'channel': '1',
                'noise_floor': 'loud',
            }
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.message, 'noise_floor must be a number')

    def test_shutdown_action_defaults_to_cancel(self) -> None:
        app = ShowcoApp(
            rehearsal.RehearsalRecsClient(),
            rehearsal.RehearsalStreamoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
        )

        result = app.run_action({'action': 'recs-shutdown'})

        self.assertTrue(result.ok)
        self.assertEqual(result.message, 'recs shutdown canceled')

    def test_action_log_keeps_ten_most_recent_results(self) -> None:
        app = ShowcoApp(
            rehearsal.RehearsalRecsClient(),
            rehearsal.RehearsalStreamoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
        )

        for i in range(12):
            app.run_action({'action': f'unknown-{i}'})

        messages = [entry.result.message for entry in app.recent_actions()]
        self.assertEqual(len(messages), 10)
        self.assertEqual(messages[0], 'unknown action unknown-11')
        self.assertEqual(messages[-1], 'unknown action unknown-2')

    def test_action_transport_error_is_recorded_as_a_failure(self) -> None:
        recs = mock.Mock()
        recs.calibrate.side_effect = OSError('recs socket unavailable')
        app = ShowcoApp(
            recs,
            rehearsal.RehearsalStreamoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
        )

        result = app.run_action({'action': 'recs-calibrate'})

        self.assertFalse(result.ok)
        self.assertEqual(result.message, 'recs socket unavailable')
        entry = app.recent_actions()[0]
        self.assertEqual(entry.service, 'recs')
        self.assertEqual(entry.command, 'calibrate')
        self.assertEqual(entry.result, result)

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
            return models.ActionResult(ok=True, message='calibrated')

        recs = mock.Mock()
        recs.calibrate.side_effect = calibrate
        app = ShowcoApp(
            recs,
            rehearsal.RehearsalStreamoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
        )

        def run_second_action() -> None:
            app.run_action({'action': 'recs-calibrate'})
            second_complete.set()

        first = Thread(target=app.run_action, args=({'action': 'recs-calibrate'},))
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
        handler.client_address = ('127.0.0.1', 12345)

        with self.assertLogs('showco.runtime.server', level='ERROR') as logs:
            handler._log_action(
                'recs-calibrate',
                models.ActionResult(ok=False, message='I/O operation on closed file.'),
            )

        self.assertIn("action='recs-calibrate'", logs.output[0])
        self.assertIn('ok=False', logs.output[0])
        self.assertIn('I/O operation on closed file.', logs.output[0])


if __name__ == '__main__':
    unittest.main()

    def test_musician_actions_use_recs_client(self) -> None:
        recs = rehearsal.RehearsalRecsClient()
        app = ShowcoApp(
            recs,
            rehearsal.RehearsalStreamoClient(),
            rehearsal.RehearsalSystemMonitor(),
            rehearsal.RehearsalMixersMonitor(),
        )

        added = app.run_action(
            {
                'action': 'recs-musician-add',
                'name': 'mike',
                'other_names': 'Michael\n',
                'contacts': 'insta:mike',
            }
        )
        edited = app.run_action(
            {
                'action': 'recs-musician-edit',
                'name': 'mike',
                'public_keys': 'ssh-ed25519 AAA',
            }
        )

        self.assertTrue(added.ok)
        self.assertTrue(edited.ok)
        self.assertEqual(recs.musicians()['mike'].other_names, [])
        self.assertEqual(recs.musicians()['mike'].public_keys, ['ssh-ed25519 AAA'])
