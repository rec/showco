from __future__ import annotations

import unittest
from pathlib import Path

from showco.twitcho.client import TwitchoClient


class TwitchoTests(unittest.TestCase):
    def test_control_endpoint_uses_twitcho_service_socket(self) -> None:
        self.assertEqual(
            TwitchoClient().control_endpoint,
            Path.home() / ".local/state/twitcho/gui.sock",
        )

    def test_status_maps_successful_reply(self) -> None:
        client = FakeTwitchoClient(
            {
                "state": "streaming",
                "muted": True,
                "ffmpeg_alive": True,
                "audio_seconds": 12.0,
                "clipping": False,
                "output_bitrate_kbps": 312.5,
            }
        )

        status = client.status()

        self.assertEqual(status.service.state, "connected")
        self.assertEqual(status.stream_state, "streaming")
        self.assertTrue(status.muted)
        self.assertEqual(status.audio_seconds, 12.0)
        self.assertEqual(status.output_bitrate_kbps, 312.5)

    def test_status_reports_failed_command(self) -> None:
        client = FakeTwitchoClient(ConnectionError("not running"))

        status = client.status()

        self.assertEqual(status.service.state, "offline")
        self.assertEqual(status.service.last_error, "not running")


class FakeTwitchoClient(TwitchoClient):
    def __init__(self, reply: str | dict[str, object] | ConnectionError) -> None:
        self.reply = reply

    def _call(self, command: str, **fields: object) -> str | dict[str, object]:
        if isinstance(self.reply, ConnectionError):
            raise self.reply
        return self.reply


if __name__ == "__main__":
    unittest.main()
