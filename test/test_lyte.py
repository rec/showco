from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from showco.runtime.lyte import LyteClient


class LyteClientTests(unittest.TestCase):
    def test_disabled_lyte_reports_disabled(self) -> None:
        status = LyteClient(enabled=False).status()

        self.assertEqual(status.service.state, 'disabled')
        self.assertFalse(status.running)

    def test_status_reports_installation_progress_and_error(self) -> None:
        client = mock.Mock()
        client.call.return_value = {
            'running': True,
            'active_animation': 'tree_show',
            'bindings': {'light': 'dots + strings'},
            'strings': {
                'dots': {
                    'state': 'streaming',
                    'host': '10.0.0.17',
                    'mac': '00:11:22:33:44:55',
                    'led_count': 250,
                    'frame_count': 42,
                },
                'strings': {
                    'state': 'failed',
                    'failure_count': 1,
                    'last_error': 'controller unreachable',
                },
            },
            'midi_connected': True,
            'note': 64,
            'active_test': {'level': 50},
        }
        with mock.patch('showco.runtime.lyte.rpc.Client', return_value=client):
            status = LyteClient(
                enabled=True, control_endpoint=Path('/tmp/lyte.sock')
            ).status()

        self.assertEqual(status.service.state, 'error')
        self.assertEqual(status.service.last_error, 'strings: controller unreachable')
        self.assertEqual(status.active_animation, 'tree_show')
        self.assertEqual(status.strings['dots'].led_count, 250)
        self.assertEqual(status.strings['dots'].frame_count, 42)
        self.assertTrue(status.midi_connected)
        self.assertEqual(status.note, 64)
        self.assertTrue(status.active_test)
        client.call.assert_called_once_with('status')

    def test_test_returns_queued_result(self) -> None:
        client = mock.Mock()
        client.call.return_value = {'state': 'queued'}
        with mock.patch('showco.runtime.lyte.rpc.Client', return_value=client):
            result = LyteClient(enabled=True).test()

        self.assertTrue(result.ok)
        self.assertEqual(result.message, 'lyte light test queued')
        client.call.assert_called_once_with('test', level=30.0, duration=1.0)

    def test_invalid_test_reply_is_an_error(self) -> None:
        client = mock.Mock()
        client.call.return_value = {'state': 'running'}
        with mock.patch('showco.runtime.lyte.rpc.Client', return_value=client):
            result = LyteClient(enabled=True).test()

        self.assertFalse(result.ok)
        self.assertEqual(result.message, 'lyte did not queue light test')


if __name__ == '__main__':
    unittest.main()
