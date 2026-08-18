from __future__ import annotations

import base64
import io
import json
import unittest
from datetime import UTC, datetime
from pathlib import Path

import tyro

from showco.x18 import osc
from showco.x18.recorder_supervisor import X18RecorderSupervisor


class X18OscTests(unittest.TestCase):
    def test_recorder_options_accept_existing_flags(self) -> None:
        options = tyro.cli(
            osc.X18RecorderOptions,
            args=["--host", "10.43.0.18", "--port", "10024", "--log-dir", "/logs"],
        )

        self.assertEqual(options.host, "10.43.0.18")
        self.assertEqual(options.port, 10_024)
        self.assertEqual(options.log_dir, Path("/logs"))

    def test_xremote_message_is_read_only_subscription(self) -> None:
        self.assertEqual(
            osc.decode_osc(osc.xremote_message()),
            [{"path": "/xremote", "types": "", "args": []}],
        )

    def test_decodes_float_message(self) -> None:
        data = osc.osc_string("/ch/01/mix/fader") + osc.osc_string(",f") + b"?@\0\0"

        self.assertEqual(
            osc.decode_osc(data),
            [{"path": "/ch/01/mix/fader", "types": "f", "args": [0.75]}],
        )

    def test_log_path_uses_x18_timestamp_name(self) -> None:
        timestamp = datetime(2026, 7, 26, 12, 34, 56, tzinfo=UTC)

        self.assertEqual(
            osc.log_path(Path("/logs"), timestamp),
            Path("/logs/x18-20260726T123456Z.jsonl"),
        )

    def test_write_datagram_records_raw_payload_and_decoded_message(self) -> None:
        output = io.BytesIO()
        recorder = osc.X18OscRecorder("10.43.0.18", log_dir=Path("/logs"))
        recorder.output = output
        data = osc.xremote_message()

        recorder.write_datagram("out", data, target=("10.43.0.18", 10_024))

        record = json.loads(output.getvalue().decode())
        self.assertEqual(record["direction"], "out")
        self.assertEqual(record["target"], ["10.43.0.18", 10_024])
        self.assertEqual(base64.b64decode(record["data_b64"]), data)
        self.assertEqual(
            record["decoded"],
            [{"path": "/xremote", "types": "", "args": []}],
        )

    def test_send_xremote_records_send_error(self) -> None:
        output = io.BytesIO()
        recorder = osc.X18OscRecorder("10.43.0.18", log_dir=Path("/logs"))
        recorder.output = output

        recorder.send_xremote(BrokenSocket())

        record = json.loads(output.getvalue().decode())
        self.assertEqual(record["direction"], "out")
        self.assertEqual(record["kind"], "error")
        self.assertEqual(record["target"], ["10.43.0.18", 10_024])
        self.assertEqual(record["error"], "network unreachable")

    def test_send_xremote_does_not_record_success(self) -> None:
        output = io.BytesIO()
        recorder = osc.X18OscRecorder("10.43.0.18", log_dir=Path("/logs"))
        recorder.output = output

        recorder.send_xremote(FakeSocket())

        self.assertIs(recorder.output, output)
        self.assertEqual(output.getvalue(), b"")

    def test_write_error_does_not_escape_when_log_is_unavailable(self) -> None:
        recorder = osc.X18OscRecorder("10.43.0.18", log_dir=Path("/logs"))
        recorder.output = FailingOutput()

        recorder.write_datagram("out", b"data")

        self.assertIsNotNone(recorder.last_write_error)

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
                "run",
                "x18-record",
                "--host",
                "10.43.0.18",
                "--port",
                "10024",
                "--log-dir",
                "/logs",
            ],
        )

    def test_supervisor_close_clears_process_reference(self) -> None:
        supervisor = X18RecorderSupervisor(
            "10.43.0.18",
            port=10_024,
            log_dir=Path("/logs"),
            python="/python",
        )
        process = FakeProcess()
        supervisor.process = process

        supervisor.close()

        self.assertIsNone(supervisor.process)
        self.assertTrue(process.terminated)


class BrokenSocket:
    def sendto(self, data: bytes, target: tuple[str, int]) -> None:
        raise OSError("network unreachable")


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, data: bytes, target: tuple[str, int]) -> None:
        self.sent.append((data, target))


class FailingOutput:
    def write(self, data: bytes) -> int:
        raise OSError("disk full")

    def flush(self) -> None:
        return

    def close(self) -> None:
        return


class FakeProcess:
    def __init__(self) -> None:
        self.terminated = False
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: int | None = None) -> int:
        self.returncode = 0
        return self.returncode


if __name__ == "__main__":
    unittest.main()
