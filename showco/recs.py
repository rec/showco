from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import cast

from pydantic import BaseModel
from reccy import ipc
from recs.cfg.track_names import DeviceTrackNames
from recs.daemon import gui_protocol
from recs.daemon.models import DaemonMetadata

from .models import ActionResult, ChannelLevel, RecsStatus, ServiceStatus

STALE_AFTER_SECONDS = 3.0
WINDOWS_PIPE = r"\\.\pipe\recs"


class RecsClient:
    def __init__(
        self,
        *,
        status_path: Path | None = None,
        metadata_path: Path | None = None,
        stale_after_seconds: float = STALE_AFTER_SECONDS,
    ) -> None:
        paths = recs_paths()
        self.status_path = status_path or paths.status
        self.metadata_path = metadata_path or paths.metadata
        self.stale_after_seconds = stale_after_seconds

    def status(self) -> RecsStatus:
        if not self.status_path.exists():
            return RecsStatus(
                service=ServiceStatus(
                    name="recs",
                    state="offline",
                    last_error=f"{self.status_path} does not exist",
                )
            )

        try:
            data = json.loads(self.status_path.read_text())
        except json.JSONDecodeError as e:
            return RecsStatus(
                service=ServiceStatus(
                    name="recs",
                    state="error",
                    last_error=f"invalid status JSON: {e.msg}",
                )
            )
        except OSError as e:
            return RecsStatus(
                service=ServiceStatus(
                    name="recs",
                    state="error",
                    last_error=f"could not read status JSON: {e}",
                )
            )

        if not isinstance(data, dict):
            return RecsStatus(
                service=ServiceStatus(
                    name="recs",
                    state="error",
                    last_error="status JSON is not an object",
                )
            )

        updated_at = _float(data.get("updated_at"))
        gui_ipc_error = _string(data.get("gui_ipc_error"))
        state = _connection_state(updated_at, self.stale_after_seconds)
        rows = _rows(data.get("rows"))
        totals = rows[0] if rows else {}

        return RecsStatus(
            service=ServiceStatus(
                name="recs",
                state=state,
                last_error=gui_ipc_error,
                updated_at=updated_at,
            ),
            recording=bool(data.get("recording")),
            elapsed_seconds=_float(totals.get("time")),
            recorded_seconds=_float(totals.get("recorded")),
            file_size=_float(totals.get("file_size")),
            file_count=_int(totals.get("file_count")),
            client_count=_int(data.get("client_count")) or 0,
            channels=channel_levels(rows),
            errors=_string_list(data.get("errors")),
        )

    def calibrate(self) -> ActionResult:
        message_id = str(uuid.uuid4())
        reply = self._send_command(
            gui_protocol.Command(
                type="command",
                id=message_id,
                command="calibrate",
            ),
            send_error="could not send recs calibrate command",
            failure_prefix="recs calibration failed",
            reply_name="calibration",
        )
        if isinstance(reply, ActionResult):
            return reply
        if reply.ok:
            return ActionResult(ok=True, message="recs calibration succeeded")
        return ActionResult(
            ok=False, message=reply.message or "recs calibration failed"
        )

    def set_track_name(
        self, device: str, channel: str, track_name: str
    ) -> ActionResult:
        device = device.strip()
        channel = channel.strip()
        track_name = track_name.strip()
        if not device:
            return ActionResult(ok=False, message="recs track name device is missing")
        if not channel:
            return ActionResult(ok=False, message="recs track name channel is missing")

        track_names = self.track_names()
        if isinstance(track_names, ActionResult):
            return track_names
        channel_number = track_channel(device, channel, track_names)
        if channel_number is None:
            return ActionResult(
                ok=False,
                message=f"could not resolve recs channel {channel} for {device}",
            )

        updated = replace_track_name(track_names, device, channel_number, track_name)
        message_id = str(uuid.uuid4())
        reply = self._send_command(
            gui_protocol.Command(
                type="command",
                id=message_id,
                command="set_track_names",
                track_names=updated,
            ),
            send_error="could not send recs track name command",
            failure_prefix="recs track name update failed",
            reply_name="track name",
        )
        if isinstance(reply, ActionResult):
            return reply
        if reply.ok:
            if track_name:
                return ActionResult(
                    ok=True, message=f"recs track name set to {track_name}"
                )
            return ActionResult(
                ok=True, message=f"recs track name cleared for {channel}"
            )
        return ActionResult(
            ok=False,
            message=reply.message or "recs track name update failed",
        )

    def track_names(self) -> DeviceTrackNames | ActionResult:
        message_id = str(uuid.uuid4())
        reply = self._send_command(
            gui_protocol.Command(
                type="command",
                id=message_id,
                command="get_track_names",
            ),
            send_error="could not send recs track name request",
            failure_prefix="recs track name request failed",
            reply_name="track name",
        )
        if isinstance(reply, ActionResult):
            return reply
        if not reply.ok:
            return ActionResult(
                ok=False,
                message=reply.message or "recs track name request failed",
            )
        track_names = result_track_names(reply.result)
        if track_names is None:
            return ActionResult(ok=False, message="recs sent invalid track names")
        return track_names

    def action(self, command: str, **fields: object) -> ActionResult:
        message_id = str(uuid.uuid4())
        payload: dict[str, object] = {
            "type": "command",
            "id": message_id,
            "command": command,
        }
        payload.update({k: v for k, v in fields.items() if v not in ("", None)})
        reply = self._send_command(
            gui_protocol.Command.model_validate(payload),
            send_error=f"could not send recs {command} command",
            failure_prefix=f"recs {command} failed",
            reply_name=command.replace("_", " "),
        )
        if isinstance(reply, ActionResult):
            return reply
        if reply.ok:
            return ActionResult(
                ok=True,
                message=command_result_message(command, reply.result),
            )
        return ActionResult(
            ok=False,
            message=reply.message or f"recs {command} failed",
        )

    def shutdown(self) -> ActionResult:
        try:
            metadata = self._metadata()
        except (OSError, ValueError) as e:
            return ActionResult(ok=False, message=f"could not read recs metadata: {e}")
        if metadata is None:
            return ActionResult(
                ok=False, message=f"{self.metadata_path} does not exist"
            )

        try:
            connection = ipc.client_connection(_endpoint(metadata.gui_endpoint))
        except OSError as e:
            return ActionResult(ok=False, message=f"could not connect to recs: {e}")
        try:
            if not connection.write(gui_hello()):
                return ActionResult(ok=False, message="could not send recs hello")
            if error := _expect_daemon_hello(_read_message(connection)):
                return ActionResult(ok=False, message=error)
            if not connection.write(ipc.message_json(ipc.Shutdown(type="shutdown"))):
                return ActionResult(ok=False, message="could not send recs shutdown")
            return ActionResult(ok=True, message="recs shutdown requested")
        except (OSError, ValueError) as e:
            return ActionResult(ok=False, message=f"recs shutdown failed: {e}")
        finally:
            connection.close()

    def _send_command(
        self,
        command: gui_protocol.Command,
        *,
        send_error: str,
        failure_prefix: str,
        reply_name: str,
    ) -> gui_protocol.Reply | ActionResult:
        try:
            metadata = self._metadata()
        except (OSError, ValueError) as e:
            return ActionResult(ok=False, message=f"could not read recs metadata: {e}")
        if metadata is None:
            return ActionResult(
                ok=False, message=f"{self.metadata_path} does not exist"
            )

        try:
            connection = ipc.client_connection(_endpoint(metadata.gui_endpoint))
        except OSError as e:
            return ActionResult(ok=False, message=f"could not connect to recs: {e}")
        try:
            if not connection.write(gui_hello()):
                return ActionResult(ok=False, message="could not send recs hello")
            if error := _expect_daemon_hello(_read_message(connection)):
                return ActionResult(ok=False, message=error)

            if not connection.write(ipc.message_json(command, exclude_none=True)):
                return ActionResult(ok=False, message=send_error)

            return _command_reply(_read_message(connection), command.id, reply_name)
        except (OSError, ValueError) as e:
            return ActionResult(ok=False, message=f"{failure_prefix}: {e}")
        finally:
            connection.close()

    def _metadata(self) -> DaemonMetadata | None:
        if not self.metadata_path.exists():
            return None
        return DaemonMetadata.model_validate_json(self.metadata_path.read_text())


class RecsPaths(BaseModel, frozen=True):
    metadata: Path
    status: Path
    gui_endpoint: str


def recs_paths(home: Path | None = None) -> RecsPaths:
    home = home or Path.home()
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA", home / "AppData/Roaming"))
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
        return RecsPaths(
            metadata=appdata / "recs/daemon.json",
            status=local / "recs/status.json",
            gui_endpoint=WINDOWS_PIPE,
        )
    return RecsPaths(
        metadata=home / ".config/recs/daemon.json",
        status=home / ".local/state/recs/status.json",
        gui_endpoint=str(home / ".local/state/recs/gui.sock"),
    )


def _endpoint(endpoint: str) -> Path | str:
    if endpoint == WINDOWS_PIPE:
        return endpoint
    return Path(endpoint)


def gui_hello() -> str:
    return ipc.message_json(ipc.Hello(type="hello", role="gui", version=1))


def _read_message(connection: ipc.Connection) -> object:
    for line in connection.read_lines():
        return ipc.parse_message(line, gui_protocol.MESSAGE)
    return ipc.Error(type="error", message="recs closed the connection")


def _expect_daemon_hello(message: object) -> str | None:
    if isinstance(message, ipc.Error):
        return message.message
    if not isinstance(message, ipc.Hello) or message.role != "daemon":
        return "recs did not send daemon hello"
    return None


def _command_reply(
    message: object, message_id: str, reply_name: str
) -> gui_protocol.Reply | ActionResult:
    if isinstance(message, ipc.Error):
        return ActionResult(ok=False, message=message.message)
    if not isinstance(message, gui_protocol.Reply):
        return ActionResult(ok=False, message=f"recs did not send {reply_name} reply")
    if message.id != message_id:
        return ActionResult(ok=False, message="recs sent reply for a different command")
    return cast(gui_protocol.Reply, message)


def result_track_names(result: dict[str, object] | None) -> DeviceTrackNames | None:
    if result is None:
        return None
    value = result.get("track_names")
    if not isinstance(value, dict):
        return None
    track_names: DeviceTrackNames = {}
    for k, v in value.items():
        if not isinstance(k, str) or not isinstance(v, dict):
            return None
        names: dict[str, int] = {}
        for name, channel in v.items():
            if not isinstance(name, str) or not isinstance(channel, int):
                return None
            names[name] = channel
        track_names[k] = names
    return track_names


def track_channel(
    device: str, channel: str, track_names: DeviceTrackNames
) -> int | None:
    first, _, _ = channel.partition("-")
    if first.isdigit():
        return int(first)
    value = track_names.get(device, {}).get(channel)
    if isinstance(value, int):
        return value
    return None


def replace_track_name(
    track_names: DeviceTrackNames,
    device: str,
    channel: int,
    track_name: str,
) -> DeviceTrackNames:
    updated = {k: dict(v) for k, v in track_names.items()}
    names = updated.setdefault(device, {})
    for name, value in list(names.items()):
        if value == channel:
            del names[name]
    if track_name:
        names[track_name] = channel
    return updated


def command_result_message(command: str, result: dict[str, object] | None) -> str:
    if result is None:
        return f"recs {command} succeeded"
    text = json.dumps(result, sort_keys=True)
    if len(text) > 500:
        text = text[:497] + "..."
    return f"recs {command} succeeded: {text}"


def channel_levels(rows: list[dict[str, object]]) -> list[ChannelLevel]:
    channels = []
    device = ""
    for row in rows:
        if isinstance(name := row.get("device"), str):
            device = name
        if not isinstance(name := row.get("channel"), str):
            continue
        signal = _float(row.get("signal"))
        channels.append(
            ChannelLevel(
                name=name, state=level_state(signal), device=device, signal=signal
            )
        )
    return channels


def level_state(signal: float | None) -> str:
    if signal is None or signal < 0.001:
        return "silent"
    if signal < 1 / 3:
        return "present"
    if signal < 0.9:
        return "healthy"
    return "clipping"


def _connection_state(updated_at: float | None, stale_after_seconds: float) -> str:
    if updated_at is None:
        return "connected"
    if time.time() - updated_at > stale_after_seconds:
        return "stale"
    return "connected"


def _rows(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, object]] = []
    for r in value:
        if isinstance(r, dict):
            row: dict[str, object] = {}
            for k, v in r.items():
                if isinstance(k, str):
                    row[k] = v
            rows.append(row)
    return rows


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [i for i in value if isinstance(i, str)]


def _float(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None
