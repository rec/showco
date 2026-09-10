from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from streamo.config import Streamo

from showco.streamo.config import convert_twitcho_config


class StreamoConfigTests(unittest.TestCase):
    def test_converts_twitcho_configuration_to_streamo_toml(self) -> None:
        with TemporaryDirectory() as directory:
            source = Path(directory) / "config.json"
            target = Path(directory) / "config.toml"
            source.write_text(json.dumps(twitcho_config()))

            convert_twitcho_config(source, target)

            value = tomllib.loads(target.read_text())

        self.assertEqual(value["device_name"], "X18")
        self.assertEqual(value["channel"], 17)
        self.assertEqual(value["streaming_service"]["service"], "twitch")
        self.assertEqual(
            value["streaming_service"]["ingest"],
            {
                "protocol": "rtmps",
                "server_url": "rtmps://live.twitch.tv/app",
                "stream_key": "stream-key",
            },
        )
        self.assertEqual(
            value["streaming_service"]["encoding"]["video"],
            {
                "codec": "h264",
                "bitrate": "150k",
                "resolution": "640x360",
                "frame_rate": 10,
                "keyframe_interval": 2,
                "pixel_format": "yuv420p",
            },
        )
        self.assertEqual(Streamo.model_validate(value).channel, 17)

    def test_requires_twitcho_ingest_values(self) -> None:
        with TemporaryDirectory() as directory:
            source = Path(directory) / "config.json"
            target = Path(directory) / "config.toml"
            source.write_text(json.dumps({"device_name": "X18", "channel": 17}))

            with self.assertRaisesRegex(ValueError, "twitch_url"):
                convert_twitcho_config(source, target)


def twitcho_config() -> dict[str, object]:
    return {
        "device_name": "X18",
        "channel": 17,
        "video": "visual-bed.mp4",
        "sample_rate": 48_000,
        "video_resolution": "640x360",
        "video_frame_rate": 10,
        "twitch_url": "rtmps://live.twitch.tv/app",
        "twitch_key": "stream-key",
        "twitch_client_id": "client-id",
        "twitch_access_token": "access-token",
        "twitch_broadcaster_id": "1234",
    }
