from __future__ import annotations

import time
import unittest
from pathlib import Path
from unittest import mock

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
                "last_audio_at": time.time(),
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

    def test_status_reports_stream_failure(self) -> None:
        client = FakeTwitchoClient({"state": "failed", "last_error": "encoder exited"})

        status = client.status()

        self.assertEqual(status.service.state, "error")
        self.assertEqual(status.service.last_error, "encoder exited")

    def test_status_reports_missing_encoder(self) -> None:
        client = FakeTwitchoClient(
            {
                "state": "streaming",
                "ffmpeg_alive": False,
                "last_audio_at": 100.0,
            }
        )

        status = client.status()

        self.assertEqual(status.service.state, "error")
        self.assertEqual(status.service.last_error, "Twitcho encoder is not running")

    @mock.patch("showco.twitcho.client.time.time", return_value=106.0)
    def test_status_reports_stalled_audio(self, current_time: mock.Mock) -> None:
        client = FakeTwitchoClient(
            {
                "state": "streaming",
                "ffmpeg_alive": True,
                "last_audio_at": 100.0,
            }
        )

        status = client.status()

        self.assertEqual(status.service.state, "error")
        self.assertEqual(
            status.service.last_error,
            "Twitcho audio has not advanced for 6.0 seconds",
        )
        current_time.assert_called_once_with()


class FakeTwitchoClient(TwitchoClient):
    def __init__(self, reply: str | dict[str, object] | ConnectionError) -> None:
        self.reply = reply

    def _call(self, command: str, **fields: object) -> str | dict[str, object]:
        if isinstance(self.reply, ConnectionError):
            raise self.reply
        return self.reply


if __name__ == "__main__":
    unittest.main()
