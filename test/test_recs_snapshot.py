from __future__ import annotations

import unittest
from unittest import mock

from showco.recs_control import RecsControlClient
from showco.recs_snapshot import RecsSnapshotClient, osc_status


class RecsSnapshotTests(unittest.TestCase):
    def test_reads_complete_status_snapshot(self) -> None:
        control = mock.Mock(spec=RecsControlClient)
        control.call.return_value = snapshot()
        client = RecsSnapshotClient(control, cache_seconds=0)

        status = client.status()

        self.assertEqual(status.service_state, "connected")
        self.assertTrue(status.has_snapshot)
        self.assertTrue(status.paused)
        self.assertEqual(status.rows[0]["file_count"], 3)
        self.assertEqual(status.errors[0].message, "disk almost full")
        assert status.disk is not None
        self.assertEqual(status.disk.path, "/recordings")
        self.assertEqual(status.disk.free_bytes, 75)
        self.assertTrue(status.disk.alert_active)
        self.assertEqual(status.midi[0].state, "recording")
        self.assertEqual(status.osc[0].name, "X18")
        self.assertIsNone(status.error)
        control.call.assert_called_once_with("status_snapshot", timeout=0.25)

    def test_transport_failure_preserves_data_as_stale_and_later_recovers(self) -> None:
        control = mock.Mock(spec=RecsControlClient)
        recovered = snapshot()
        recovered["recording"] = {"paused": False}
        control.call.side_effect = [
            snapshot(),
            ConnectionError("gone"),
            recovered,
        ]
        client = RecsSnapshotClient(control, cache_seconds=0)

        initial = client.status()
        failed = client.status()
        final = client.status()

        self.assertEqual(failed.rows, initial.rows)
        self.assertEqual(failed.disk, initial.disk)
        self.assertEqual(failed.service_state, "stale")
        self.assertEqual(failed.error, "recs status_snapshot failed: gone")
        self.assertEqual(final.service_state, "connected")
        self.assertFalse(final.paused)
        self.assertIsNone(final.error)

    def test_initial_transport_failure_is_offline(self) -> None:
        control = mock.Mock(spec=RecsControlClient)
        control.call.side_effect = ConnectionError("gone")

        status = RecsSnapshotClient(control, cache_seconds=0).status()

        self.assertEqual(status.service_state, "offline")
        self.assertFalse(status.has_snapshot)

    def test_invalid_nested_data_preserves_previous_snapshot_as_error(self) -> None:
        control = mock.Mock(spec=RecsControlClient)
        invalid = snapshot()
        invalid["midi"] = [{"name": "FLOW 8"}]
        control.call.side_effect = [snapshot(), invalid]
        client = RecsSnapshotClient(control, cache_seconds=0)

        initial = client.status()
        failed = client.status()

        self.assertEqual(failed.rows, initial.rows)
        self.assertEqual(failed.service_state, "error")
        self.assertEqual(failed.error, "recs status snapshot has invalid MIDI status")

    def test_invalid_top_level_snapshot_is_an_error(self) -> None:
        control = mock.Mock(spec=RecsControlClient)
        control.call.return_value = []

        status = RecsSnapshotClient(control, cache_seconds=0).status()

        self.assertEqual(status.service_state, "error")
        self.assertEqual(status.error, "recs status snapshot is not an object")

    def test_reads_named_osc_statuses(self) -> None:
        statuses = osc_status(snapshot())

        self.assertEqual(statuses[0].name, "X18")
        self.assertEqual(statuses[0].state, "running")
        self.assertEqual(statuses[0].log_path, "X18.jsonl")
        self.assertEqual(statuses[0].log_size, 12)


def snapshot() -> dict[str, object]:
    return {
        "type": "status_snapshot_result",
        "rows": [{"time": 4.0, "recorded": 3.0, "file_count": 3}],
        "errors": [
            {
                "timestamp": "2026-09-07T12:00:00Z",
                "message": "disk almost full",
            }
        ],
        "recording": {"paused": True},
        "disk": {
            "path": "/recordings",
            "used_bytes": 25,
            "free_bytes": 75,
            "total_bytes": 100,
            "estimated_seconds_remaining": 3600.0,
            "alert_threshold": "10 GiB",
            "alert_active": True,
            "paused_for_disk_space": False,
        },
        "midi": [{"name": "FLOW 8", "state": "recording"}],
        "osc": [
            {
                "name": "X18",
                "state": "running",
                "path": "X18.jsonl",
                "size": 12,
            }
        ],
    }


if __name__ == "__main__":
    unittest.main()
