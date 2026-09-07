from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from showco.recs_snapshot import RecsSnapshotClient, osc_status


class RecsSnapshotTests(unittest.TestCase):
    def test_reads_recording_disk_status(self) -> None:
        client = RecsSnapshotClient(
            control_endpoint=Path("/tmp/recs.sock"), cache_seconds=0
        )
        with mock.patch("showco.recs_snapshot.rpc.Client") as rpc_client:
            rpc_client.return_value.call.return_value = {
                "disk": {
                    "path": "/recordings",
                    "used_bytes": 25,
                    "free_bytes": 75,
                    "total_bytes": 100,
                    "estimated_seconds_remaining": 3600.0,
                    "alert_threshold": "10 GiB",
                    "alert_active": True,
                    "paused_for_disk_space": False,
                }
            }

            status = client.status()

        self.assertEqual(status.disk.path, "/recordings")
        self.assertEqual(status.disk.free_bytes, 75)
        self.assertTrue(status.disk.alert_active)
        self.assertIsNone(status.disk_error)

    def test_invalid_disk_preserves_previous_disk_and_other_snapshot_data(self) -> None:
        client = RecsSnapshotClient(
            control_endpoint=Path("/tmp/recs.sock"), cache_seconds=0
        )
        with mock.patch("showco.recs_snapshot.rpc.Client") as rpc_client:
            rpc_client.return_value.call.side_effect = [
                {
                    "disk": {
                        "path": "/recordings",
                        "used_bytes": 25,
                        "free_bytes": 75,
                        "total_bytes": 100,
                    }
                },
                {
                    "disk": {"path": "/recordings"},
                    "midi": [{"name": "FLOW 8", "state": "recording"}],
                },
            ]

            initial = client.status()
            invalid = client.status()

        self.assertEqual(invalid.disk, initial.disk)
        self.assertEqual(invalid.disk_error, "recs disk status is invalid")
        self.assertEqual(invalid.midi[0].state, "recording")

    def test_reads_named_osc_statuses(self) -> None:
        statuses = osc_status(
            {
                "osc": [
                    {
                        "name": "X18",
                        "state": "running",
                        "path": "X18.jsonl",
                        "size": 12,
                    }
                ]
            }
        )

        self.assertEqual(statuses[0].name, "X18")
        self.assertEqual(statuses[0].state, "running")
        self.assertEqual(statuses[0].log_path, "X18.jsonl")
        self.assertEqual(statuses[0].log_size, 12)

    def test_invalid_snapshot_preserves_data_and_later_recovers(self) -> None:
        client = RecsSnapshotClient(
            control_endpoint=Path("/tmp/recs.sock"), cache_seconds=0
        )
        with mock.patch("showco.recs_snapshot.rpc.Client") as rpc_client:
            rpc_client.return_value.call.side_effect = [
                {"midi": [{"name": "FLOW 8", "state": "recording"}]},
                "invalid",
                {"midi": [{"name": "FLOW 8", "state": "waiting"}]},
            ]

            initial = client.status()
            invalid = client.status()
            recovered = client.status()

        self.assertEqual(initial.midi[0].state, "recording")
        self.assertEqual(invalid.midi, initial.midi)
        self.assertEqual(invalid.error, "recs status snapshot is not an object")
        self.assertEqual(recovered.midi[0].state, "waiting")
        self.assertIsNone(recovered.error)


if __name__ == "__main__":
    unittest.main()
