from __future__ import annotations

import json
import time
import unittest
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from reccy.models import Platform
from recs.daemon.models import DaemonMetadata

from showco import recs
from showco.recs import RecsClient, channel_levels, level_state, replace_track_name

CLIENT_CONNECTION = "showco.recs.ipc.client_connection"


class RecsTests(unittest.TestCase):
    def test_status_changes_command_checks_successive_updated_at_values(self) -> None:
        command = recs.status_changes_command()

        self.assertIn('status="$HOME/.local/state/recs/status.json"', command)
        self.assertIn("sleep 4", command)
        self.assertIn("for sample in $(seq 3)", command)
        self.assertIn("current=$(updated_at)", command)
        self.assertIn('previous="$current"', command)

    def test_status_failure_summary_shows_recent_error_messages(self) -> None:
        summary = recs.status_failure_summary(
            json.dumps(
                {
                    "updated_at": 123.0,
                    "errors": [
                        "first",
                        {"message": "second"},
                        "third",
                        "fourth",
                    ],
                }
            )
        )

        self.assertEqual(
            summary,
            "Recs status did not advance; updated_at=123.0\n"
            "Recent Recs errors:\n- second\n- third\n- fourth",
        )

    def test_reads_recs_status_file(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            path.write_text(
                json.dumps(
                    {
                        "client_count": 2,
                        "errors": [
                            {
                                "timestamp": "2026-08-13T12:34:56.789Z",
                                "message": "disk almost full",
                            }
                        ],
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
        self.assertEqual(status.errors[0].message, "disk almost full")

    def test_reports_missing_recs_status_as_offline(self) -> None:
        status = RecsClient(status_path=Path("/does/not/exist")).status()

        self.assertEqual(status.service.state, "offline")

    def test_reports_status_read_failure_as_error(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            path.write_text("{}")
            with mock.patch.object(Path, "read_text", side_effect=OSError("gone")):
                status = RecsClient(status_path=path).status()

        self.assertEqual(status.service.state, "error")
        self.assertIn("gone", status.service.last_error or "")

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

    def test_calibrate_sends_recs_protocol_request(self) -> None:
        with TemporaryDirectory() as directory:
            metadata = Path(directory) / "daemon.json"
            metadata.write_text(
                DaemonMetadata(
                    executable=Path("/bin/recs"),
                    platform=Platform.linux,
                    gui_endpoint="/tmp/recs.sock",
                ).model_dump_json()
            )
            connection = FakeRecsConnection(
                [
                    '{"type":"hello","role":"daemon","version":3}\n',
                    '{"type":"calibrated","measurements":{},"noise_floors":{},'
                    '"profiles_path":"/tmp/profiles.json"}\n',
                ]
            )
            client = RecsClient(metadata_path=metadata)

            with mock.patch(CLIENT_CONNECTION, return_value=connection):
                result = client.calibrate()

        self.assertTrue(result.ok)
        self.assertEqual(result.message, "recs calibration succeeded")
        self.assertEqual(
            [json.loads(message) for message in connection.sent],
            [
                {"type": "hello", "role": "gui", "version": 3},
                {"type": "calibrate", "channels": {}},
            ],
        )
        self.assertTrue(connection.closed)

    def test_calibrate_reports_invalid_metadata(self) -> None:
        with TemporaryDirectory() as directory:
            metadata = Path(directory) / "daemon.json"
            metadata.write_text("not json")

            result = RecsClient(metadata_path=metadata).calibrate()

        self.assertFalse(result.ok)
        self.assertIn("could not read recs metadata", result.message)

    def test_calibrate_reports_connection_failure(self) -> None:
        with TemporaryDirectory() as directory:
            metadata = Path(directory) / "daemon.json"
            metadata.write_text(
                DaemonMetadata(
                    executable=Path("/bin/recs"),
                    platform=Platform.linux,
                    gui_endpoint="/tmp/recs.sock",
                ).model_dump_json()
            )
            client = RecsClient(metadata_path=metadata)

            with mock.patch(
                CLIENT_CONNECTION,
                side_effect=OSError("connection refused"),
            ):
                result = client.calibrate()

        self.assertFalse(result.ok)
        self.assertEqual(
            result.message, "could not connect to recs: connection refused"
        )

    def test_set_track_name_sends_recs_protocol_requests(self) -> None:
        with TemporaryDirectory() as directory:
            metadata = Path(directory) / "daemon.json"
            metadata.write_text(
                DaemonMetadata(
                    executable=Path("/bin/recs"),
                    platform=Platform.linux,
                    gui_endpoint="/tmp/recs.sock",
                ).model_dump_json()
            )
            connection = FakeRecsConnection(
                [
                    '{"type":"hello","role":"daemon","version":3}\n',
                    '{"type":"track_names","track_names":{"Mic":{"Old Name":1}}}\n',
                    '{"type":"hello","role":"daemon","version":3}\n',
                    '{"type":"track_names","track_names":{"Mic":{"Lead Vocal":1}}}\n',
                ]
            )
            client = RecsClient(metadata_path=metadata)

            with mock.patch(CLIENT_CONNECTION, return_value=connection):
                result = client.set_track_name("Mic", "Old Name", "Lead Vocal")

        self.assertTrue(result.ok)
        self.assertEqual(result.message, "recs track name set to Lead Vocal")
        self.assertEqual(
            [json.loads(message) for message in connection.sent],
            [
                {"type": "hello", "role": "gui", "version": 3},
                {"type": "get_track_names"},
                {"type": "hello", "role": "gui", "version": 3},
                {
                    "type": "set_track_names",
                    "track_names": {"Mic": {"Lead Vocal": 1}},
                },
            ],
        )

    def test_recs_action_sends_protocol_request_with_fields(self) -> None:
        with TemporaryDirectory() as directory:
            metadata = Path(directory) / "daemon.json"
            metadata.write_text(
                DaemonMetadata(
                    executable=Path("/bin/recs"),
                    platform=Platform.linux,
                    gui_endpoint="/tmp/recs.sock",
                ).model_dump_json()
            )
            connection = FakeRecsConnection(
                [
                    '{"type":"hello","role":"daemon","version":3}\n',
                    '{"type":"noise_floor_set","source":"Mic","channel":1,'
                    '"noise_floor":42.5}\n',
                ]
            )
            client = RecsClient(metadata_path=metadata)

            with mock.patch(CLIENT_CONNECTION, return_value=connection):
                result = client.action(
                    "set_noise_floor",
                    source="Mic",
                    channel=1,
                    noise_floor=42.5,
                )

        self.assertTrue(result.ok)
        self.assertEqual(
            [json.loads(message) for message in connection.sent],
            [
                {"type": "hello", "role": "gui", "version": 3},
                {
                    "type": "set_noise_floor",
                    "channel": 1,
                    "noise_floor": 42.5,
                    "source": "Mic",
                },
            ],
        )
        self.assertIn("recs set_noise_floor succeeded", result.message)

    def test_shutdown_sends_recs_shutdown_message(self) -> None:
        with TemporaryDirectory() as directory:
            metadata = Path(directory) / "daemon.json"
            metadata.write_text(
                DaemonMetadata(
                    executable=Path("/bin/recs"),
                    platform=Platform.linux,
                    gui_endpoint="/tmp/recs.sock",
                ).model_dump_json()
            )
            connection = FakeRecsConnection(
                ['{"type":"hello","role":"daemon","version":3}\n']
            )
            client = RecsClient(metadata_path=metadata)

            with mock.patch(CLIENT_CONNECTION, return_value=connection):
                result = client.shutdown()

        self.assertTrue(result.ok)
        self.assertEqual(
            [json.loads(message) for message in connection.sent],
            [
                {"type": "hello", "role": "gui", "version": 3},
                {"type": "shutdown"},
            ],
        )

    def test_channel_levels_keep_device_context(self) -> None:
        channels = channel_levels(
            [
                {"device": "Mic"},
                {"channel": "1", "signal": 0.1},
                {"device": "X18"},
                {"channel": "2", "signal": 0.2},
            ]
        )

        self.assertEqual([c.device for c in channels], ["Mic", "X18"])

    def test_replace_track_name_removes_old_name_for_channel(self) -> None:
        self.assertEqual(
            replace_track_name({"Mic": {"Old": 1, "Other": 2}}, "Mic", 1, "New"),
            {"Mic": {"Other": 2, "New": 1}},
        )


class FakeRecsConnection:
    def __init__(self, received: list[str]) -> None:
        self.received = received
        self.sent: list[str] = []
        self.closed = False

    def read_lines(self) -> Iterator[str]:
        while self.received:
            yield self.received.pop(0)

    def write(self, message: str) -> bool:
        self.sent.append(message)
        return True

    def close(self) -> None:
        self.closed = True


if __name__ == "__main__":
    unittest.main()
