from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel
from reccy import rpc

from ..models import ActionResult, ServiceStatus, TwitchoStatus

CONTROL_ENDPOINT = Path.home() / ".local/state/twitcho/control.sock"


class TwitchoClient:
    def __init__(
        self,
        *,
        control_endpoint: Path = CONTROL_ENDPOINT,
    ) -> None:
        self.control_endpoint = control_endpoint

    def status(self) -> TwitchoStatus:
        result = self.command("status")
        if not result.ok:
            return TwitchoStatus(
                service=ServiceStatus(
                    name="twitcho", state="offline", last_error=result.message
                )
            )
        value = result.payload.get("status")
        if not isinstance(value, dict):
            return TwitchoStatus(
                service=ServiceStatus(
                    name="twitcho",
                    state="error",
                    last_error="status reply missing status",
                )
            )
        status = dict(value)
        return TwitchoStatus(
            service=ServiceStatus(
                name="twitcho",
                state="connected",
                last_error=_string(status.get("last_error")),
            ),
            stream_state=_string(status.get("state")) or "unknown",
            muted=bool(status.get("muted")),
            ffmpeg_alive=bool(status.get("ffmpeg_alive")),
            audio_seconds=_float(status.get("audio_seconds")),
            clipping=bool(status.get("clipping")),
            output_bitrate_kbps=_float(status.get("output_bitrate_kbps")),
        )

    def action(self, command: str, **fields: object) -> ActionResult:
        result = self.command(command, **fields)
        if result.ok:
            return ActionResult(ok=True, message=f"twitcho {command} succeeded")
        return ActionResult(ok=False, message=result.message)

    def command(self, command: str, **fields: object) -> TwitchoReply:
        try:
            reply = self._call(command, **fields)
        except (ConnectionError, OSError, TimeoutError, ValueError) as e:
            return TwitchoReply(
                ok=False, message=f"twitcho command failed: {e}", payload={}
            )
        if reply.ok:
            return TwitchoReply(ok=True, message="ok", payload=reply.result)
        return TwitchoReply(
            ok=False,
            message=reply.message or "twitcho command failed",
            payload=reply.result,
        )

    def _call(self, command: str, **fields: object) -> rpc.Response:
        return rpc.Client(self.control_endpoint, role="showco").call(command, **fields)


class TwitchoReply(BaseModel, frozen=True):
    ok: bool
    message: str
    payload: dict[str, object]


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _float(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None
