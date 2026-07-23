from __future__ import annotations

import unittest

from showco.twitcho import TwitchoClient


class TwitchoTests(unittest.TestCase):
    def test_status_maps_successful_reply(self) -> None:
        client = FakeTwitchoClient(
            {
                "type": "reply",
                "id": "1",
                "ok": True,
                "status": {
                    "state": "streaming",
                    "muted": True,
                    "ffmpeg_alive": True,
                    "audio_seconds": 12.0,
                    "clipping": False,
                    "output_bitrate_kbps": 312.5,
                },
            }
        )

        status = client.status()

        self.assertEqual(status.service.state, "connected")
        self.assertEqual(status.stream_state, "streaming")
        self.assertTrue(status.muted)
        self.assertEqual(status.audio_seconds, 12.0)
        self.assertEqual(status.output_bitrate_kbps, 312.5)

    def test_status_reports_failed_command(self) -> None:
        client = FakeTwitchoClient(
            {"type": "reply", "id": "1", "ok": False, "error": "not running"}
        )

        status = client.status()

        self.assertEqual(status.service.state, "offline")
        self.assertEqual(status.service.last_error, "not running")


class FakeTwitchoClient(TwitchoClient):
    def __init__(self, reply: dict[str, object]) -> None:
        super().__init__()
        self.reply = reply

    def _exchange(self, command: object) -> dict[str, object]:
        return self.reply


if __name__ == "__main__":
    unittest.main()
