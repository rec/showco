from __future__ import annotations

import base64
import io
import json
import unittest
from datetime import UTC, datetime
from pathlib import Path

from showco.x18_osc import (
    X18OscRecorder,
    decode_osc,
    log_path,
    osc_string,
    xremote_message,
)
from showco.x18_recorder_supervisor import X18RecorderSupervisor


class X18OscTests(unittest.TestCase):
    def test_xremote_message_is_read_only_subscription(self) -> None:
        self.assertEqual(
            decode_osc(xremote_message()),
            [{"path": "/xremote", "types": "", "args": []}],
        )

    def test_decodes_float_message(self) -> None:
        data = osc_string("/ch/01/mix/fader") + osc_string(",f") + b"?@\0\0"

        self.assertEqual(
            decode_osc(data),
            [{"path": "/ch/01/mix/fader", "types": "f", "args": [0.75]}],
        )

    def test_log_path_uses_x18_timestamp_name(self) -> None:
        timestamp = datetime(2026, 7, 26, 12, 34, 56, tzinfo=UTC)

        self.assertEqual(
            log_path(Path("/logs"), timestamp),
            Path("/logs/x18-20260726T123456Z.jsonl"),
        )

    def test_write_datagram_records_raw_payload_and_decoded_message(self) -> None:
        output = io.BytesIO()
        recorder = X18OscRecorder("10.43.0.18", log_dir=Path("/logs"))
        data = xremote_message()

        recorder.write_datagram(output, "out", data, target=("10.43.0.18", 10_024))

        record = json.loads(output.getvalue().decode())
        self.assertEqual(record["direction"], "out")
        self.assertEqual(record["target"], ["10.43.0.18", 10_024])
        self.assertEqual(base64.b64decode(record["data_b64"]), data)
        self.assertEqual(
            record["decoded"],
            [{"path": "/xremote", "types": "", "args": []}],
        )

    def test_supervisor_command_runs_recorder_subcommand(self) -> None:
        supervisor = X18RecorderSupervisor(
            "10.43.0.18",
            port=10_024,
            log_dir=Path("/logs"),
            python="/python",
        )

        self.assertEqual(
            supervisor.command(),
            [
                "/python",
                "-m",
                "showco",
                "x18-record",
                "--host",
                "10.43.0.18",
                "--port",
                "10024",
                "--log-dir",
                "/logs",
            ],
        )


if __name__ == "__main__":
    unittest.main()
