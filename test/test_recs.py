from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from showco.recs import RecsClient, channel_levels, level_state


class RecsTests(unittest.TestCase):
    def test_reads_recs_status_file(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            path.write_text(
                json.dumps(
                    {
                        "client_count": 2,
                        "recording": True,
                        "updated_at": time.time(),
                        "rows": [
                            {"time": 4.0, "recorded": 3.0, "file_count": 1},
                            {"channel": "1", "signal": 0.5},
                        ],
                    }
                )
            )

            status = RecsClient(status_path=path).status()

        self.assertEqual(status.service.state, "connected")
        self.assertTrue(status.recording)
        self.assertEqual(status.elapsed_seconds, 4.0)
        self.assertEqual(status.file_count, 1)
        self.assertEqual(status.client_count, 2)
        self.assertEqual(status.channels[0].state, "healthy")

    def test_reports_missing_recs_status_as_offline(self) -> None:
        status = RecsClient(status_path=Path("/does/not/exist")).status()

        self.assertEqual(status.service.state, "offline")

    def test_level_state_uses_four_display_states(self) -> None:
        self.assertEqual(level_state(None), "silent")
        self.assertEqual(level_state(0.0), "silent")
        self.assertEqual(level_state(0.1), "present")
        self.assertEqual(level_state(0.5), "healthy")
        self.assertEqual(level_state(0.95), "clipping")

    def test_channel_levels_ignore_non_channel_rows(self) -> None:
        self.assertEqual(
            channel_levels([{"device": "Mic"}, {"channel": "1", "signal": 0.1}])[
                0
            ].name,
            "1",
        )


if __name__ == "__main__":
    unittest.main()
