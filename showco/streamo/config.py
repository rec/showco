from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import tyro
from pydantic import BaseModel


class StreamoConfigOptions(BaseModel, frozen=True):
    source: Annotated[Path, tyro.conf.arg(help="Existing Twitcho JSON configuration")]
    target: Annotated[Path, tyro.conf.arg(help="New Streamo TOML configuration")]


def main(argv: list[str] | None = None) -> int:
    options = tyro.cli(StreamoConfigOptions, args=argv)
    convert_twitcho_config(options.source, options.target)
    return 0


def convert_twitcho_config(source: Path, target: Path) -> None:
    try:
        values = json.loads(source.read_text())
    except json.JSONDecodeError as error:
        raise ValueError(f"{source} is not valid JSON: {error}") from error
    if not isinstance(values, dict):
        raise ValueError(f"{source} must contain a JSON object")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(streamo_toml(values))


def streamo_toml(values: dict[str, object]) -> str:
    sample_rate = positive_int(values, "sample_rate", 48_000)
    resolution = text(values, "video_resolution", "640x360")
    frame_rate = positive_int(values, "video_frame_rate", 10)
    lines = [
        assignment("device_name", required_text(values, "device_name")),
        assignment("channel", positive_int(values, "channel")),
    ]
    for name in ("video", "title_card"):
        if value := optional_text(values, name):
            lines.append(assignment(name, value))
    lines.extend(
        [
            assignment("sample_rate", sample_rate),
            assignment("video_resolution", resolution),
            assignment("video_frame_rate", frame_rate),
        ]
    )
    for name, default in OVERLAY_DEFAULTS.items():
        if name in values:
            lines.append(assignment(name, values[name]))
        elif name.startswith("title_") and "title_card" in values:
            lines.append(assignment(name, default))
        elif name.startswith("image_") and "image_dir" in values:
            lines.append(assignment(name, default))
    lines.extend(
        [
            "",
            "[streaming_service]",
            assignment("service", "twitch"),
            optional_assignment(values, "twitch_client_id", "client_id"),
            optional_assignment(values, "twitch_access_token", "access_token"),
            optional_assignment(values, "twitch_broadcaster_id", "broadcaster_id"),
            optional_assignment(values, "twitch_sender_id", "sender_id"),
            optional_assignment(values, "twitch_moderator_id", "moderator_id"),
            optional_assignment(values, "twitch_api_url", "api_url"),
            "",
            "[streaming_service.ingest]",
            assignment(
                "protocol",
                protocol(required_text(values, "twitch_url")),
            ),
            assignment("server_url", required_text(values, "twitch_url")),
            assignment("stream_key", required_text(values, "twitch_key")),
            "",
            "[streaming_service.encoding]",
            assignment("container", "flv"),
            "",
            "[streaming_service.encoding.audio]",
            assignment("codec", "aac"),
            assignment("bitrate", text(values, "audio_bitrate", "160k")),
            assignment("sample_rate", sample_rate),
            assignment("channels", 2),
            "",
            "[streaming_service.encoding.video]",
            assignment("codec", "h264"),
            assignment("bitrate", text(values, "video_bitrate", "150k")),
            assignment("resolution", resolution),
            assignment("frame_rate", frame_rate),
            assignment("keyframe_interval", 2),
            assignment("pixel_format", "yuv420p"),
        ]
    )
    return "\n".join(line for line in lines if line) + "\n"


def assignment(name: str, value: object) -> str:
    return f"{name} = {json.dumps(value)}"


def optional_assignment(values: dict[str, object], source: str, target: str) -> str:
    value = optional_text(values, source)
    return assignment(target, value) if value else ""


def required_text(values: dict[str, object], name: str) -> str:
    if value := optional_text(values, name):
        return value
    raise ValueError(f"Twitcho configuration requires {name}")


def optional_text(values: dict[str, object], name: str) -> str | None:
    value = values.get(name)
    return value if isinstance(value, str) and value else None


def text(values: dict[str, object], name: str, default: str) -> str:
    value = values.get(name, default)
    if isinstance(value, str) and value:
        return value
    raise ValueError(f"Twitcho configuration {name} must be a non-empty string")


def positive_int(
    values: dict[str, object], name: str, default: int | None = None
) -> int:
    value = values.get(name, default)
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    raise ValueError(f"Twitcho configuration {name} must be a positive integer")


def protocol(url: str) -> str:
    if url.startswith("rtmps://"):
        return "rtmps"
    if url.startswith("rtmp://"):
        return "rtmp"
    raise ValueError("Twitcho configuration twitch_url must use rtmp:// or rtmps://")


OVERLAY_DEFAULTS = {
    "title_interval": 180.0,
    "title_duration": 8.0,
    "title_fade": 2.0,
    "image_interval": 0.0,
    "image_duration": 8.0,
    "image_fade": 2.0,
}
