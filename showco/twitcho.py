from __future__ import annotations

import json
import socket
import uuid
from collections.abc import Mapping

from .models import ActionResult, ServiceStatus, TwitchoStatus

CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = 17_351
CONTROL_TIMEOUT_SECONDS = 1.0


class TwitchoClient:
    def __init__(
        self,
        *,
        host: str = CONTROL_HOST,
        port: int = CONTROL_PORT,
        token: str | None = None,
        timeout_seconds: float = CONTROL_TIMEOUT_SECONDS,
    ) -> None:
        self.host = host
        self.port = port
        self.token = token
        self.timeout_seconds = timeout_seconds

    def status(self) -> TwitchoStatus:
        result = self.command("status")
        if not result.ok:
            return TwitchoStatus(
                service=ServiceStatus("twitcho", "offline", result.message)
            )
        status = result.payload.get("status")
        if not isinstance(status, dict):
            return TwitchoStatus(
                service=ServiceStatus("twitcho", "error", "status reply missing status")
            )
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
            return ActionResult(True, f"twitcho {command} succeeded")
        return ActionResult(False, result.message)

    def command(self, command: str, **fields: object) -> TwitchoReply:
        message = {"type": "command", "id": str(uuid.uuid4()), "command": command}
        message.update(fields)
        try:
            reply = self._exchange(message)
        except (OSError, TimeoutError, json.JSONDecodeError, ValueError) as e:
            return TwitchoReply(False, f"twitcho command failed: {e}", {})
        if reply.get("ok") is True:
            return TwitchoReply(True, "ok", reply)
        return TwitchoReply(
            False, _string(reply.get("error")) or "twitcho command failed", reply
        )

    def _exchange(self, command: Mapping[str, object]) -> dict[str, object]:
        with socket.create_connection(
            (self.host, self.port), timeout=self.timeout_seconds
        ) as sock:
            sock.settimeout(self.timeout_seconds)
            reader = sock.makefile()
            hello = {"type": "hello", "version": 1, "client": "showco"}
            if self.token is not None:
                hello["token"] = self.token
            _write(sock, hello)
            hello_reply = _read_object(reader)
            if hello_reply.get("type") != "hello" or hello_reply.get("version") != 1:
                raise ValueError(f"unexpected twitcho hello reply: {hello_reply}")
            _write(sock, command)
            return _read_object(reader)


class TwitchoReply:
    def __init__(self, ok: bool, message: str, payload: dict[str, object]) -> None:
        self.ok = ok
        self.message = message
        self.payload = payload


def _write(sock: socket.socket, message: Mapping[str, object]) -> None:
    sock.sendall(json.dumps(message, separators=(",", ":")).encode() + b"\n")


def _read_object(reader: object) -> dict[str, object]:
    line = reader.readline()
    if not line:
        raise ConnectionError("connection closed")
    message = json.loads(line)
    if not isinstance(message, dict):
        raise ValueError("reply is not an object")
    return message


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _float(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None
