from __future__ import annotations

import sys
import time
from pathlib import Path

import tyro
from pydantic import BaseModel
from reccy.protocol import rpc
from reccy.services import paths
from streamo.config import STREAMO_SERVICE

from ..deployment import machine_role
from ..runtime.models import ActionResult, ServiceStatus, StreamoStatus

AUDIO_STALE_SECONDS = 5.0
ACTIVE_STREAM_STATES = {"streaming", "muted"}
LOCAL_ACTIONS = {"mute", "unmute", "stop"}
STREAM_API_ACTIONS = {"update_stream_info", "chat", "announce", "clip", "marker"}


class StreamoHealthOptions(BaseModel, frozen=True):
    pass


def health_main(argv: list[str] | None = None) -> int:
    machine_role.require_target_machine("showco run streamo-health")
    tyro.cli(StreamoHealthOptions, args=argv, description="Check Streamo health")
    status = StreamoClient().status()
    if status.service.state == "connected":
        return 0
    print(status.service.last_error or "streamo is not healthy", file=sys.stderr)
    return 1


class StreamoClient:
    def __init__(
        self,
        *,
        control_endpoint: Path | str | None = None,
    ) -> None:
        self.control_endpoint = (
            control_endpoint
            or paths.service_paths(
                STREAMO_SERVICE, paths.current_platform()
            ).control_endpoint
        )

    def status(self) -> StreamoStatus:
        try:
            status = self._call("status")
        except (ConnectionError, OSError, TimeoutError, ValueError) as error:
            return StreamoStatus(
                service=ServiceStatus(
                    name="streamo",
                    state="offline",
                    last_error=str(error),
                )
            )
        if not isinstance(status, dict):
            return StreamoStatus(
                service=ServiceStatus(
                    name="streamo",
                    state="error",
                    last_error="streamo status reply is not an object",
                )
            )
        stream_state = _string(status.get("state")) or "unknown"
        last_error = _health_error(status, stream_state)
        service_state = "error" if last_error else "connected"
        return StreamoStatus(
            service=ServiceStatus(
                name="streamo",
                state=service_state,
                last_error=last_error,
            ),
            stream_state=stream_state,
            streaming_service=_string(status.get("service")),
            endpoint_host=_string(status.get("endpoint_host")),
            capabilities=_strings(status.get("capabilities")),
            remote_health=_dictionary(status.get("remote_health")),
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
            return ActionResult(ok=True, message=f"streamo {command} succeeded")
        if command in STREAM_API_ACTIONS and isinstance(result, dict):
            return ActionResult(ok=True, message=f"streamo {command} succeeded")
        return ActionResult(
            ok=False,
            message=f"streamo sent an invalid {command} response",
        )

    def _call(self, command: str, **fields: object) -> object:
        return rpc.Client(self.control_endpoint, role="showco").call(command, **fields)


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _float(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _strings(value: object) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _dictionary(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        return None
    return value


def _health_error(status: dict[str, object], stream_state: str) -> str | None:
    if error := _string(status.get("last_error")):
        return error
    if stream_state == "failed":
        return "Streamo stream failed"
    if stream_state not in ACTIVE_STREAM_STATES:
        return None
    if not bool(status.get("ffmpeg_alive")):
        return "Streamo encoder is not running"
    if (last_audio_at := _float(status.get("last_audio_at"))) is None:
        return "Streamo has not received audio"
    if (stalled_seconds := time.time() - last_audio_at) > AUDIO_STALE_SECONDS:
        return f"Streamo audio has not advanced for {stalled_seconds:.1f} seconds"
    return None
