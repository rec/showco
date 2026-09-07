from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from showco.recs_snapshot import RecsSnapshotClient, osc_status


class RecsSnapshotTests(unittest.TestCase):
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
