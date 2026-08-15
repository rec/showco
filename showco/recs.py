from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from pydantic import BaseModel
from reccy import ipc, rpc
from recs.cfg.track_names import DeviceTrackNames
from recs.daemon import gui_protocol, paths
from recs.daemon.models import DaemonMetadata

from . import models

STALE_AFTER_SECONDS = 3.0
STATUS_CHANGE_WAIT_SECONDS = 4
STATUS_CHANGE_SAMPLE_COUNT = 3
STATUS_ERROR_LIMIT = 3
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

    def status(self) -> models.RecsStatus:
        if not self.status_path.exists():
            return models.RecsStatus(
                service=models.ServiceStatus(
                    name="recs",
                    state="offline",
                    last_error=f"{self.status_path} does not exist",
                )
            )

        try:
            data = json.loads(self.status_path.read_text())
        except json.JSONDecodeError as e:
            return models.RecsStatus(
                service=models.ServiceStatus(
                    name="recs",
                    state="error",
                    last_error=f"invalid status JSON: {e.msg}",
                )
            )
        except OSError as e:
            return models.RecsStatus(
                service=models.ServiceStatus(
                    name="recs",
                    state="error",
                    last_error=f"could not read status JSON: {e}",
                )
            )

        if not isinstance(data, dict):
            return models.RecsStatus(
                service=models.ServiceStatus(
                    name="recs",
                    state="error",
                    last_error="status JSON is not an object",
                )
            )

        updated_at = _float(data.get("updated_at"))
        gui_ipc_error = _string(data.get("gui_ipc_error"))
        if updated_at is None:
            return models.RecsStatus(
                service=models.ServiceStatus(
                    name="recs",
                    state="error",
                    last_error="status JSON does not have numeric updated_at",
                )
            )
        state = _connection_state(updated_at, self.stale_after_seconds)
        rows = _rows(data.get("rows"))
        totals = rows[0] if rows else {}

        return models.RecsStatus(
            service=models.ServiceStatus(
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
            errors=_error_records(data.get("errors")),
        )

    def calibrate(self) -> models.ActionResult:
        response = self._send_request(
            gui_protocol.Calibrate(type="calibrate"),
            send_error="could not send recs calibrate request",
            failure_prefix="recs calibration failed",
        )
        if isinstance(response, models.ActionResult):
            return response
        if isinstance(response, gui_protocol.Calibrated):
            return models.ActionResult(ok=True, message="recs calibration succeeded")
        return models.ActionResult(
            ok=False, message="recs did not send calibrated response"
        )

    def set_track_name(
        self, device: str, channel: str, track_name: str
    ) -> models.ActionResult:
        device = device.strip()
        channel = channel.strip()
        track_name = track_name.strip()
        if not device:
            return models.ActionResult(
                ok=False, message="recs track name device is missing"
            )
        if not channel:
            return models.ActionResult(
                ok=False, message="recs track name channel is missing"
            )

        track_names = self.track_names()
        if isinstance(track_names, models.ActionResult):
            return track_names
        channel_number = track_channel(device, channel, track_names)
        if channel_number is None:
            return models.ActionResult(
                ok=False,
                message=f"could not resolve recs channel {channel} for {device}",
            )

        updated = replace_track_name(track_names, device, channel_number, track_name)
        response = self._send_request(
            gui_protocol.SetTrackNames(
                type="set_track_names",
                track_names=updated,
            ),
            send_error="could not send recs track name request",
            failure_prefix="recs track name update failed",
        )
        if isinstance(response, models.ActionResult):
            return response
        if isinstance(response, gui_protocol.TrackNames):
            if track_name:
                return models.ActionResult(
                    ok=True, message=f"recs track name set to {track_name}"
                )
            return models.ActionResult(
                ok=True, message=f"recs track name cleared for {channel}"
            )
        return models.ActionResult(
            ok=False, message="recs did not send track_names response"
        )

    def track_names(self) -> DeviceTrackNames | models.ActionResult:
        response = self._send_request(
            gui_protocol.GetTrackNames(type="get_track_names"),
            send_error="could not send recs track name request",
            failure_prefix="recs track name request failed",
        )
        if isinstance(response, models.ActionResult):
            return response
        if not isinstance(response, gui_protocol.TrackNames):
            return models.ActionResult(
                ok=False, message="recs sent invalid track names"
            )
        return response.track_names

    def mutable_attributes(
        self,
    ) -> list[models.MutableAttribute] | models.ActionResult:
        response = self._external_command("mutable_attributes")
        if isinstance(response, models.ActionResult):
            return response
        if not isinstance(response, dict):
            return models.ActionResult(
                ok=False,
                message="recs did not send mutable attributes",
            )
        addresses = response.get("mutable_attributes")
        if not isinstance(addresses, list) or not all(
            isinstance(a, str) for a in addresses
        ):
            return models.ActionResult(
                ok=False,
                message="recs sent invalid mutable attributes",
            )
        attributes: list[models.MutableAttribute] = []
        for address in addresses:
            value = self._external_command("get_cfg", address=address)
            if isinstance(value, models.ActionResult):
                return value
            if not isinstance(value, dict) or value.get("address") != address:
                return models.ActionResult(
                    ok=False,
                    message=f"recs did not send {address} value",
                )
            attributes.append(
                models.MutableAttribute(address=address, value=value.get("value"))
            )
        return attributes

    def set_attr(self, address: str, value: object) -> models.ActionResult:
        response = self._external_command("set_cfg", address=address, value=value)
        if isinstance(response, models.ActionResult):
            return response
        if response != "ok":
            return models.ActionResult(
                ok=False,
                message=f"recs did not set {address}",
            )
        return models.ActionResult(ok=True, message=f"recs set {address}")

    def action(self, command: str, **fields: object) -> models.ActionResult:
        payload: dict[str, object] = {"type": command}
        payload.update({k: v for k, v in fields.items() if v not in ("", None)})
        request = gui_protocol.MESSAGE.validate_python(payload)
        if not isinstance(request, gui_protocol.Request):
            return models.ActionResult(
                ok=False, message=f"recs does not support {command}"
            )
        response = self._send_request(
            request,
            send_error=f"could not send recs {command} request",
            failure_prefix=f"recs {command} failed",
        )
        if isinstance(response, models.ActionResult):
            return response
        if not isinstance(response, gui_protocol.Error):
            return models.ActionResult(
                ok=True,
                message=command_result_message(command, response),
            )
        return models.ActionResult(ok=False, message=response.message)

    def shutdown(self) -> models.ActionResult:
        try:
            metadata = self._metadata()
        except (OSError, ValueError) as e:
            return models.ActionResult(
                ok=False, message=f"could not read recs metadata: {e}"
            )
        if metadata is None:
            return models.ActionResult(
                ok=False, message=f"{self.metadata_path} does not exist"
            )

        try:
            connection = ipc.client_connection(_endpoint(metadata.gui_endpoint))
        except OSError as e:
            return models.ActionResult(
                ok=False, message=f"could not connect to recs: {e}"
            )
        try:
            if not connection.write(gui_hello()):
                return models.ActionResult(
                    ok=False, message="could not send recs hello"
                )
            if error := _expect_daemon_hello(_read_message(connection)):
                return models.ActionResult(ok=False, message=error)
            if not connection.write(ipc.message_json(ipc.Shutdown(type="shutdown"))):
                return models.ActionResult(
                    ok=False, message="could not send recs shutdown"
                )
            return models.ActionResult(ok=True, message="recs shutdown requested")
        except (OSError, ValueError) as e:
            return models.ActionResult(ok=False, message=f"recs shutdown failed: {e}")
        finally:
            connection.close()

    def _send_request(
        self,
        request: gui_protocol.Request,
        *,
        send_error: str,
        failure_prefix: str,
    ) -> gui_protocol.Response | models.ActionResult:
        try:
            metadata = self._metadata()
        except (OSError, ValueError) as e:
            return models.ActionResult(
                ok=False, message=f"could not read recs metadata: {e}"
            )
        if metadata is None:
            return models.ActionResult(
                ok=False, message=f"{self.metadata_path} does not exist"
            )

        try:
            connection = ipc.client_connection(_endpoint(metadata.gui_endpoint))
        except OSError as e:
            return models.ActionResult(
                ok=False, message=f"could not connect to recs: {e}"
            )
        try:
            if not connection.write(gui_hello()):
                return models.ActionResult(
                    ok=False, message="could not send recs hello"
                )
            if error := _expect_daemon_hello(_read_message(connection)):
                return models.ActionResult(ok=False, message=error)

            if not connection.write(ipc.message_json(request, exclude_none=True)):
                return models.ActionResult(ok=False, message=send_error)

            return _response(_read_message(connection))
        except (OSError, ValueError) as e:
            return models.ActionResult(ok=False, message=f"{failure_prefix}: {e}")
        finally:
            connection.close()

    def _external_command(
        self, command: str, **parameters: object
    ) -> str | dict[str, object] | models.ActionResult:
        try:
            return rpc.Client(paths.external_control_endpoint(), role="showco").call(
                command,
                **parameters,
            )
        except (ConnectionError, OSError, TimeoutError, ValueError) as error:
            return models.ActionResult(
                ok=False, message=f"recs {command} failed: {error}"
            )

    def _metadata(self) -> DaemonMetadata | None:
        if not self.metadata_path.exists():
            return None
        return DaemonMetadata.model_validate_json(self.metadata_path.read_text())


class RecsPaths(BaseModel, frozen=True):
    metadata: Path
    status: Path
    gui_endpoint: str


def status_changes_command() -> str:
    return (
        'status="$HOME/.local/state/recs/status.json"; '
        "updated_at() { sed -nE "
        '\'s/.*"updated_at"[[:space:]]*:[[:space:]]*'
        '([0-9]+([.][0-9]+)?).*/\\1/p\' "$status"; }; '
        'previous=""; '
        f"for sample in $(seq {STATUS_CHANGE_SAMPLE_COUNT}); do "
        "current=$(updated_at); "
        'if [ -z "$current" ] || '
        '{ [ -n "$previous" ] && [ "$previous" = "$current" ]; }; then '
        'cat "$status"; exit 1; fi; '
        'previous="$current"; '
        f'[ "$sample" = {STATUS_CHANGE_SAMPLE_COUNT} ] || '
        f"sleep {STATUS_CHANGE_WAIT_SECONDS}; "
        "done"
    )


def status_failure_summary(output: str) -> str:
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return output.strip()
    if not isinstance(data, dict):
        return output.strip()
    result = "Recs status did not advance"
    if isinstance(updated_at := data.get("updated_at"), int | float):
        result += f"; updated_at={updated_at}"
    errors = data.get("errors")
    if not isinstance(errors, list):
        return result
    messages = [error_message(e) for e in errors]
    messages = [m for m in messages if m]
    if not messages:
        return result
    return (
        result
        + "\nRecent Recs errors:\n"
        + "\n".join(f"- {m}" for m in messages[-STATUS_ERROR_LIMIT:])
    )


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
    return ipc.message_json(
        ipc.Hello(type="hello", role="gui", version=gui_protocol.VERSION)
    )


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


def _response(message: object) -> gui_protocol.Response | models.ActionResult:
    if isinstance(message, ipc.Error):
        return models.ActionResult(ok=False, message=message.message)
    if not isinstance(message, gui_protocol.Response):
        return models.ActionResult(ok=False, message="recs did not send a response")
    return message


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


def command_result_message(command: str, response: BaseModel) -> str:
    text = json.dumps(response.model_dump(exclude={"type"}), sort_keys=True)
    if len(text) > 500:
        text = text[:497] + "..."
    return f"recs {command} succeeded: {text}"


def channel_levels(rows: list[dict[str, object]]) -> list[models.ChannelLevel]:
    channels = []
    device = ""
    for row in rows:
        if isinstance(name := row.get("device"), str):
            device = name
        if not isinstance(name := row.get("channel"), str):
            continue
        signal = _float(row.get("signal"))
        channels.append(
            models.ChannelLevel(
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


def _error_records(value: object) -> list[models.ErrorRecord]:
    if not isinstance(value, list):
        return []
    errors = []
    for v in value:
        if not isinstance(v, dict):
            continue
        timestamp = _string(v.get("timestamp"))
        message = _string(v.get("message"))
        if timestamp is not None and message is not None:
            errors.append(models.ErrorRecord(timestamp=timestamp, message=message))
    return errors


def _float(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def error_message(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(message := value.get("message"), str):
        return message
    return ""


def _int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None
