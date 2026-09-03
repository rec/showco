from __future__ import annotations

import sys
import time
from pathlib import Path

import tyro
from pydantic import BaseModel
from reccy import rpc

from .. import machine_role
from ..models import ActionResult, ServiceStatus, TwitchoStatus

CONTROL_ENDPOINT = Path.home() / ".local/state/twitcho/gui.sock"
AUDIO_STALE_SECONDS = 5.0
ACTIVE_STREAM_STATES = {"streaming", "muted"}
LOCAL_ACTIONS = {"mute", "unmute", "stop"}
TWITCH_API_ACTIONS = {"update_stream_info", "chat", "announce", "clip", "marker"}


class TwitchoHealthOptions(BaseModel, frozen=True):
    pass


def health_main(argv: list[str] | None = None) -> int:
    machine_role.require_target_machine("showco run twitcho-health")
    tyro.cli(TwitchoHealthOptions, args=argv, description="Check Twitcho health")
    status = TwitchoClient().status()
    if status.service.state == "connected":
        return 0
    print(status.service.last_error or "twitcho is not healthy", file=sys.stderr)
    return 1


class TwitchoClient:
    def __init__(
        self,
        *,
        control_endpoint: Path = CONTROL_ENDPOINT,
    ) -> None:
        self.control_endpoint = control_endpoint

    def status(self) -> TwitchoStatus:
        try:
            status = self._call("status")
        except (ConnectionError, OSError, TimeoutError, ValueError) as error:
            return TwitchoStatus(
                service=ServiceStatus(
                    name="twitcho",
                    state="offline",
                    last_error=str(error),
                )
            )
        if not isinstance(status, dict):
            return TwitchoStatus(
                service=ServiceStatus(
                    name="twitcho",
                    state="error",
                    last_error="twitcho status reply is not an object",
                )
            )
        stream_state = _string(status.get("state")) or "unknown"
        last_error = _health_error(status, stream_state)
        service_state = "error" if last_error else "connected"
        return TwitchoStatus(
            service=ServiceStatus(
                name="twitcho",
                state=service_state,
                last_error=last_error,
            ),
            stream_state=stream_state,
            muted=bool(status.get("muted")),
            ffmpeg_alive=bool(status.get("ffmpeg_alive")),
            audio_seconds=_float(status.get("audio_seconds")),
            last_audio_at=_float(status.get("last_audio_at")),
            clipping=bool(status.get("clipping")),
            output_bitrate_kbps=_float(status.get("output_bitrate_kbps")),
        )

    def action(self, command: str, **fields: object) -> ActionResult:
        try:
            result = self._call(command, **fields)
        except (ConnectionError, OSError, TimeoutError, ValueError) as error:
            return ActionResult(
                ok=False,
                message=str(error),
            )
        if command in LOCAL_ACTIONS and result == "ok":
            return ActionResult(ok=True, message=f"twitcho {command} succeeded")
        if command in TWITCH_API_ACTIONS and isinstance(result, dict):
            return ActionResult(ok=True, message=f"twitcho {command} succeeded")
        return ActionResult(
            ok=False,
            message=f"twitcho sent an invalid {command} response",
        )

    def _call(self, command: str, **fields: object) -> str | dict[str, object]:
        return rpc.Client(self.control_endpoint, role="showco").call(command, **fields)


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _float(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _health_error(status: dict[str, object], stream_state: str) -> str | None:
    if error := _string(status.get("last_error")):
        return error
    if stream_state == "failed":
        return "Twitcho stream failed"
    if stream_state not in ACTIVE_STREAM_STATES:
        return None
    if not bool(status.get("ffmpeg_alive")):
        return "Twitcho encoder is not running"
    if (last_audio_at := _float(status.get("last_audio_at"))) is None:
        return "Twitcho has not received audio"
    if (stalled_seconds := time.time() - last_audio_at) > AUDIO_STALE_SECONDS:
        return f"Twitcho audio has not advanced for {stalled_seconds:.1f} seconds"
    return None
