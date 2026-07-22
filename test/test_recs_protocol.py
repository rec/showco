from __future__ import annotations

import json
import unittest
from collections.abc import Iterator

from recs.cfg import Cfg
from recs.daemon import gui_ipc
from recs.ui.key_events import KeyEvent


class RecsProtocolTests(unittest.TestCase):
    def test_recs_replies_to_hello_key_events_and_calibrate_command(self) -> None:
        key_events: list[KeyEvent] = []
        control_requests: list[gui_ipc.ControlRequest] = []
        connection = FakeConnection(
            [
                '{"type":"hello","role":"gui","version":1}\n',
                '{"type":"key_pressed","key":"g"}\n',
                '{"type":"key_released","key":"g"}\n',
                '{"type":"command","id":"c1","command":"calibrate"}\n',
            ]
        )
        listener = gui_ipc.GuiListener(
            connection,
            key_events.append,
            control_requests.append,
        )

        listener._read()
        control_requests[0].reply(
            ok=True,
            result={"profiles": {"Mic": {"noise_floor": 15.0}}},
        )

        self.assertEqual(
            [json.loads(message) for message in connection.sent],
            [
                {"type": "hello", "role": "daemon", "version": 1},
                {
                    "type": "reply",
                    "id": "c1",
                    "ok": True,
                    "result": {"profiles": {"Mic": {"noise_floor": 15.0}}},
                },
            ],
        )
        self.assertEqual(
            key_events,
            [
                KeyEvent(type="key_pressed", key="g"),
                KeyEvent(type="key_released", key="g"),
            ],
        )
        self.assertEqual(control_requests[0].command.command, "calibrate")

    def test_recs_rejects_commands_before_hello(self) -> None:
        connection = FakeConnection(
            ['{"type":"command","id":"c1","command":"calibrate"}\n']
        )
        listener = gui_ipc.GuiListener(connection, lambda event: None)

        listener._read()

        self.assertEqual(
            [json.loads(message) for message in connection.sent],
            [
                {
                    "type": "error",
                    "message": "GUI hello required before other messages",
                }
            ],
        )
        self.assertTrue(connection.closed)

    def test_recs_rejects_unsupported_protocol_versions(self) -> None:
        connection = FakeConnection(['{"type":"hello","role":"gui","version":2}\n'])
        listener = gui_ipc.GuiListener(connection, lambda event: None)

        listener._read()

        self.assertEqual(
            [json.loads(message) for message in connection.sent],
            [
                {
                    "type": "error",
                    "message": (
                        "GUI protocol version 2 is not supported; daemon requires 1"
                    ),
                }
            ],
        )
        self.assertTrue(connection.closed)

    def test_recs_sends_rows_messages(self) -> None:
        server = gui_ipc.DaemonGuiServer(lambda: iter([{"device": "Mic"}]), Cfg())
        listener = FakeListener()
        server.clients = [listener]

        server.broadcast([{"device": "Mic"}])

        self.assertEqual(
            [json.loads(message) for message in listener.messages],
            [{"type": "rows", "rows": [{"device": "Mic"}]}],
        )


class FakeConnection:
    def __init__(self, received: list[str]) -> None:
        self.received = received
        self.sent: list[str] = []
        self.closed = False

    def read_lines(self) -> Iterator[str]:
        return iter(self.received)

    def write(self, message: str) -> bool:
        self.sent.append(message)
        return True

    def close(self) -> None:
        self.closed = True


class FakeListener:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def write(self, message: str) -> bool:
        self.messages.append(message)
        return True

    def close(self) -> None:
        pass


if __name__ == "__main__":
    unittest.main()
