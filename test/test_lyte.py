from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from showco.lyte import LyteClient


class LyteClientTests(unittest.TestCase):
    def test_disabled_lyte_reports_disabled(self) -> None:
        status = LyteClient(enabled=False).status()

        self.assertEqual(status.service.state, "disabled")
        self.assertEqual(status.daemon_state, "disabled")

    def test_status_reports_output_error_and_progress(self) -> None:
        client = mock.Mock()
        client.call.return_value = {
            "state": "streaming",
            "output_state": "streaming",
            "host": "10.0.0.17",
            "device_mac": "00:11:22:33:44:55",
            "planned_led_count": 200,
            "actual_led_count": 250,
            "frame_send_count": 42,
            "last_frame_sent_at": "2026-09-03T20:00:00Z",
            "active_test": {"level": 50},
            "output_error": "controller unreachable",
        }
        with mock.patch("showco.lyte.rpc.Client", return_value=client):
            status = LyteClient(
                enabled=True, control_endpoint=Path("/tmp/lyte.sock")
            ).status()

        self.assertEqual(status.service.state, "error")
        self.assertEqual(status.service.last_error, "controller unreachable")
        self.assertEqual(status.actual_led_count, 250)
        self.assertEqual(status.frame_send_count, 42)
        self.assertTrue(status.active_test)
        client.call.assert_called_once_with("status")

    def test_test_returns_queued_result(self) -> None:
        client = mock.Mock()
        client.call.return_value = {"state": "queued"}
        with mock.patch("showco.lyte.rpc.Client", return_value=client):
            result = LyteClient(enabled=True).test()

        self.assertTrue(result.ok)
        self.assertEqual(result.message, "lyte light test queued")

    def test_invalid_test_reply_is_an_error(self) -> None:
        client = mock.Mock()
        client.call.return_value = {"state": "running"}
        with mock.patch("showco.lyte.rpc.Client", return_value=client):
            result = LyteClient(enabled=True).test()

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "lyte did not queue light test")


if __name__ == "__main__":
    unittest.main()
