from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ValidationError
from reccy import ipc, logging, rpc
from reccy.models import DaemonMetadata
from recs.base.waveform import WaveformBatchData, WaveformLayoutData
from recs.daemon import gui_protocol, paths
from typing_extensions import TypeIs

from . import models

STALE_AFTER_SECONDS = 3.0
STATUS_CHANGE_WAIT_SECONDS = 4
STATUS_CHANGE_SAMPLE_COUNT = 3
STATUS_ERROR_LIMIT = 3
STATUS_SNAPSHOT_CACHE_SECONDS = 1.0
STATUS_SNAPSHOT_TIMEOUT_SECONDS = 0.25
WINDOWS_PIPE = r"\\.\pipe\recs"
MAX_WAVEFORM_BATCHES = 80
MAX_WAVEFORM_EVENTS = 400
WAVEFORM_RECONNECT_SECONDS = 1.0
WAVEFORM_FAILURE_LOG_SECONDS = 60.0
LOGGER = logging.get_logger(__name__)


class WaveformBridge:
    def __init__(
        self,
        *,
        control_endpoint: Path | str | None = None,
        event_endpoint: Path | str | None = None,
        event_client: (
            Callable[[Callable[[rpc.Event], None]], rpc.EventClient] | None
        ) = None,
        control_client: Callable[[], rpc.Client] | None = None,
    ) -> None:
        self.control_endpoint = control_endpoint or paths.external_control_endpoint()
        self.event_endpoint = event_endpoint or paths.external_event_endpoint()
        self.event_client = event_client or self._event_client
        self.control_client = control_client or self._control_client
        self.layouts: dict[str, WaveformLayoutData] = {}
        self.batches: dict[str, deque[WaveformBatchData]] = {}
        self.events: deque[tuple[int, str, WaveformLayoutData | WaveformBatchData]] = (
            deque(maxlen=MAX_WAVEFORM_EVENTS)
        )
        self.condition = threading.Condition()
        self.changed = 0
        self.stopped = threading.Event()
        self.reconnect = threading.Event()
        self.thread: threading.Thread | None = None
        self.last_failure_log_time = 0.0

    def start(self) -> None:
        if self.thread is not None:
            return
        self.thread = threading.Thread(
            target=self._run, daemon=True, name="ShowcoWaveforms"
        )
        self.thread.start()

    def close(self) -> None:
        self.stopped.set()
        self.reconnect.set()
        with self.condition:
            self.condition.notify_all()

    def snapshot(self) -> tuple[list[WaveformLayoutData], list[WaveformBatchData], int]:
        with self.condition:
            return (
                list(self.layouts.values()),
                [b for batches in self.batches.values() for b in batches],
                self.changed,
            )

    def wait_for_change(self, changed: int, timeout: float) -> int:
        with self.condition:
            if self.changed == changed:
                self.condition.wait(timeout)
            return self.changed

    def events_since(
        self, changed: int
    ) -> list[tuple[int, str, WaveformLayoutData | WaveformBatchData]]:
        with self.condition:
            return [event for event in self.events if event[0] > changed]

    def receive(self, event: rpc.Event) -> None:
        if event.name == "waveform_layout":
            self._layout(WaveformLayoutData.model_validate(event.data))
        elif event.name == "waveform":
            self._batch(WaveformBatchData.model_validate(event.data))
        elif event.name in {"shutdown", "stopped"}:
            self.reconnect.set()

    def _run(self) -> None:
        while not self.stopped.is_set():
            events: rpc.EventClient | None = None
            try:
                self.reconnect.clear()
                events = self.event_client(self.receive)
                events.start()
                result = self.control_client().call("subscribe_waveforms")
                if not isinstance(result, dict) or result.get("active") is not True:
                    raise ConnectionError("recs did not activate waveforms")
                self.reconnect.wait()
            except (ConnectionError, OSError, TimeoutError, ValueError) as error:
                if (
                    time.monotonic() - self.last_failure_log_time
                    >= WAVEFORM_FAILURE_LOG_SECONDS
                ):
                    LOGGER.warning("recs waveform subscription failed: %s", error)
                    self.last_failure_log_time = time.monotonic()
                self.stopped.wait(WAVEFORM_RECONNECT_SECONDS)
            finally:
                if events is not None:
                    events.close()
            if self.reconnect.is_set() and not self.stopped.is_set():
                self.stopped.wait(WAVEFORM_RECONNECT_SECONDS)

    def _event_client(self, receive: Callable[[rpc.Event], None]) -> rpc.EventClient:
        return rpc.EventClient(self.event_endpoint, receive, role="showco")

    def _control_client(self) -> rpc.Client:
        return rpc.Client(self.control_endpoint, role="showco", timeout=6)

    def _layout(self, layout: WaveformLayoutData) -> None:
        with self.condition:
            self.layouts[layout.source] = layout
            self.batches.pop(layout.source, None)
            self._record("waveform_layout", layout)

    def _batch(self, batch: WaveformBatchData) -> None:
        with self.condition:
            layout = self.layouts.get(batch.source)
            if layout is None or layout.generation != batch.generation:
                return
            batches = self.batches.setdefault(
                batch.source, deque(maxlen=MAX_WAVEFORM_BATCHES)
            )
            batches.append(batch)
            self._record("waveform", batch)

    def _record(self, name: str, data: WaveformLayoutData | WaveformBatchData) -> None:
        self.changed += 1
        self.events.append((self.changed, name, data))
        self.condition.notify_all()


class RecsClient:
    def __init__(
        self,
        *,
        status_path: Path | None = None,
        metadata_path: Path | None = None,
        stale_after_seconds: float = STALE_AFTER_SECONDS,
        snapshot_cache_seconds: float = STATUS_SNAPSHOT_CACHE_SECONDS,
        snapshot_timeout_seconds: float = STATUS_SNAPSHOT_TIMEOUT_SECONDS,
    ) -> None:
        paths = recs_paths()
        self.status_path = status_path or paths.status
        self.metadata_path = metadata_path or paths.metadata
        self.stale_after_seconds = stale_after_seconds
        self.snapshot_cache_seconds = snapshot_cache_seconds
        self.snapshot_timeout_seconds = snapshot_timeout_seconds
        self.track_name_lock = threading.Lock()
        self.snapshot_lock = threading.Lock()
        self.snapshot: dict[str, object] | None = None
        self.snapshot_checked_at = 0.0
        self.snapshot_error: str | None = None

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

        snapshot, snapshot_error = self._status_snapshot()
        x18 = _x18_status(snapshot)
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
            snapshot_error=snapshot_error,
            x18=x18,
            midi=_midi_status(snapshot),
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

        with self.track_name_lock:
            track_names = self.track_names()
            if isinstance(track_names, models.ActionResult):
                return track_names
            channel_number = track_channel(device, channel, track_names)
            if channel_number is None:
                return models.ActionResult(
                    ok=False,
                    message=f"could not resolve recs channel {channel} for {device}",
                )

            updated = replace_track_name(
                track_names, device, channel_number, track_name
            )
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

    def set_stereo(self, device: str, channels: list[int]) -> models.ActionResult:
        with self.track_name_lock:
            tracks = stereo_tracks(self.status().channels, device, channels)
            if isinstance(tracks, models.ActionResult):
                return tracks
            track_names = self.track_names()
            if isinstance(track_names, models.ActionResult):
                return track_names
            response = self._send_request(
                gui_protocol.SetTracks(
                    type="set_tracks",
                    source=device,
                    tracks=[
                        gui_protocol.ChannelTrack(
                            channels=track,
                            name=track_name(track_names, device, track[0]),
                        )
                        for track in tracks
                    ],
                ),
                send_error="could not send recs stereo request",
                failure_prefix="recs stereo update failed",
            )
        if isinstance(response, models.ActionResult):
            return response
        if isinstance(response, gui_protocol.TracksSet):
            return models.ActionResult(ok=True, message="recs stereo updated")
        return models.ActionResult(ok=False, message="recs did not update stereo")

    def track_names(self) -> dict[str, dict[str, int]] | models.ActionResult:
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
        address_values = response.get("mutable_attributes")
        if not isinstance(address_values, list):
            return models.ActionResult(
                ok=False,
                message="recs sent invalid mutable attributes",
            )
        addresses = [a for a in address_values if isinstance(a, str)]
        if len(addresses) != len(address_values):
            return models.ActionResult(
                ok=False,
                message="recs sent invalid mutable attributes",
            )
        attributes: list[models.MutableAttribute] = []
        for address in addresses:
            value = self._external_command("get_cfg", address=address)
            if isinstance(value, models.ActionResult):
                return value
            if not _object_dict(value) or value.get("address") != address:
                return models.ActionResult(
                    ok=False,
                    message=f"recs did not send {address} value",
                )
            attributes.append(
                models.MutableAttribute(
                    address=address,
                    value=value.get("value"),
                )
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
        try:
            request = gui_protocol.MESSAGE.validate_python(payload)
        except ValidationError:
            return models.ActionResult(
                ok=False, message=f"recs does not support {command}"
            )
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
            connection = ipc.client_connection(_endpoint(metadata.control_endpoint))
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
            connection = ipc.client_connection(_endpoint(metadata.control_endpoint))
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

    def _status_snapshot(self) -> tuple[dict[str, object] | None, str | None]:
        with self.snapshot_lock:
            now = time.monotonic()
            if now - self.snapshot_checked_at < self.snapshot_cache_seconds:
                return self.snapshot, self.snapshot_error
            response = self._external_command(
                "status_snapshot", timeout=self.snapshot_timeout_seconds
            )
            self.snapshot_checked_at = now
            if _object_dict(response):
                self.snapshot = response
                self.snapshot_error = None
            elif isinstance(response, models.ActionResult):
                self.snapshot_error = response.message
            else:
                self.snapshot_error = "recs status snapshot is not an object"
            return self.snapshot, self.snapshot_error

    def _external_command(
        self, command: str, *, timeout: float = 1.0, **parameters: object
    ) -> str | dict[str, object] | models.ActionResult:
        try:
            return rpc.Client(
                paths.external_control_endpoint(), role="showco", timeout=timeout
            ).call(command, **parameters)
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
    device: str, channel: str, track_names: dict[str, dict[str, int]]
) -> int | None:
    first, _, _ = channel.partition("-")
    if first.isdigit():
        return int(first)
    value = track_names.get(device, {}).get(channel)
    if isinstance(value, int):
        return value
    return None


def replace_track_name(
    track_names: dict[str, dict[str, int]],
    device: str,
    channel: int,
    track_name: str,
) -> dict[str, dict[str, int]]:
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
                name=name,
                state=level_state(signal),
                device=device,
                channels=_channels(row.get("channels")),
                signal=signal,
                on=row.get("on") is True,
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


def stereo_tracks(
    channels: list[models.ChannelLevel], device: str, selected: list[int]
) -> list[list[int]] | models.ActionResult:
    source_tracks = [
        channel.channels for channel in channels if channel.device == device
    ]
    if selected not in source_tracks:
        return models.ActionResult(
            ok=False, message="recs channel is no longer available"
        )
    if len(selected) == 2:
        tracks: list[list[int]] = []
        for track in source_tracks:
            if track == selected:
                tracks.extend([[selected[0]], [selected[1]]])
            else:
                tracks.append(track)
        return tracks
    if len(selected) != 1:
        return models.ActionResult(ok=False, message="recs channel layout is invalid")
    right = [selected[0] + 1]
    if right not in source_tracks:
        return models.ActionResult(
            ok=False, message="recs channel cannot be paired with its right neighbor"
        )
    tracks = []
    for track in source_tracks:
        if track == selected:
            tracks.append(selected + right)
        elif track != right:
            tracks.append(track)
    return tracks


def track_name(
    track_names: dict[str, dict[str, int]], device: str, channel: int
) -> str:
    for name, first_channel in track_names.get(device, {}).items():
        if first_channel == channel:
            return name
    return ""


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
        if _object_dict(r):
            rows.append(r)
    return rows


def _object_dict(value: object) -> TypeIs[dict[str, object]]:
    return isinstance(value, dict) and all(isinstance(k, str) for k in value)


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _error_records(value: object) -> list[models.ErrorRecord]:
    if not isinstance(value, list):
        return []
    errors = []
    for v in value:
        if not _object_dict(v):
            continue
        timestamp = _string(v.get("timestamp"))
        message = _string(v.get("message"))
        if timestamp is not None and message is not None:
            errors.append(models.ErrorRecord(timestamp=timestamp, message=message))
    return errors


def _x18_status(value: object) -> models.RecorderStatus:
    if not _object_dict(value):
        return models.RecorderStatus()
    nodes = value.get("osc")
    if not isinstance(nodes, list):
        return models.RecorderStatus()
    for node in nodes:
        if not _object_dict(node):
            continue
        if (_string(node.get("name")) or "").casefold() != "x18":
            continue
        return models.RecorderStatus(
            state=_string(node.get("state")) or "running",
            log_path=_string(node.get("path")),
            log_size=_int(node.get("size")),
            last_error=_string(node.get("last_error")),
        )
    return models.RecorderStatus()


def _midi_status(value: object) -> list[models.MidiStatus]:
    if not _object_dict(value):
        return []
    midi = value.get("midi")
    if not isinstance(midi, list):
        return []
    return [
        models.MidiStatus(name=name, state=state)
        for item in midi
        if _object_dict(item)
        and (name := _string(item.get("name"))) is not None
        and (state := _string(item.get("state"))) is not None
    ]


def _float(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _channels(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    channels: list[int] = []
    for channel in value:
        if not isinstance(channel, int):
            return []
        channels.append(channel)
    return channels


def error_message(value: object) -> str:
    if isinstance(value, str):
        return value
    if _object_dict(value) and isinstance(message := value.get("message"), str):
        return message
    return ""


def _int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None
