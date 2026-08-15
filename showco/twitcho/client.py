from __future__ import annotations

from pathlib import Path

from reccy import rpc

from ..models import ActionResult, ServiceStatus, TwitchoStatus

CONTROL_ENDPOINT = Path.home() / ".local/state/twitcho/gui.sock"


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
        last_error = _string(status.get("last_error"))
        service_state = (
            "error" if last_error or stream_state == "failed" else "connected"
        )
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
        if isinstance(result, str):
            return ActionResult(ok=True, message=result)
        return ActionResult(ok=True, message=f"twitcho {command} succeeded")

    def _call(self, command: str, **fields: object) -> str | dict[str, object]:
        return rpc.Client(self.control_endpoint, role="showco").call(command, **fields)


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _float(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None
