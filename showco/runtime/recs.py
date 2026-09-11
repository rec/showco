from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError
from reccy.protocol import rpc
from reccy.runtime import logging
from recs.base.waveform import WaveformBatchData, WaveformLayoutData
from recs.daemon import paths

from . import models, recs_control, recs_snapshot

STATUS_CHANGE_WAIT_SECONDS = 4
STATUS_CHANGE_SAMPLE_COUNT = 3
STATUS_ERROR_LIMIT = 3
MAX_WAVEFORM_BATCHES = 80
MAX_WAVEFORM_EVENTS = 400
WAVEFORM_RECONNECT_SECONDS = 1.0
WAVEFORM_FAILURE_LOG_SECONDS = 60.0
WAVEFORM_CLOSE_SECONDS = 1.0
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
        control: recs_control.RecsControlClient | None = None,
    ) -> None:
        self.control_endpoint = control_endpoint or paths.external_control_endpoint()
        self.event_endpoint = event_endpoint or paths.external_event_endpoint()
        self.event_client = event_client or self._event_client
        self.control = control or recs_control.RecsControlClient(self.control_endpoint)
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
        try:
            self.control.call("unsubscribe_waveforms", timeout=WAVEFORM_CLOSE_SECONDS)
        except (ConnectionError, OSError, TimeoutError, ValidationError, ValueError):
            pass
        self.stopped.set()
        self.reconnect.set()
        with self.condition:
            self.condition.notify_all()
        if self.thread is not None:
            self.thread.join(WAVEFORM_CLOSE_SECONDS)

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
    ) -> tuple[bool, list[tuple[int, str, WaveformLayoutData | WaveformBatchData]]]:
        with self.condition:
            if self.events and changed < self.events[0][0] - 1:
                return True, []
            return False, [event for event in self.events if event[0] > changed]

    def receive(self, event: rpc.Event) -> None:
        try:
            if event.name == "waveform_layout":
                self._layout(WaveformLayoutData.model_validate(event.data))
            elif event.name == "waveform":
                self._batch(WaveformBatchData.model_validate(event.data))
            elif event.name in {"shutdown", "stopped"}:
                self.reconnect.set()
        except ValidationError as error:
            LOGGER.error("recs sent invalid waveform event: %s", error)
            self.reconnect.set()

    def _run(self) -> None:
        while not self.stopped.is_set():
            events: rpc.EventClient | None = None
            try:
                self.reconnect.clear()
                events = self.event_client(self.receive)
                events.start()
                result = self.control.call("subscribe_waveforms")
                if (
                    not recs_snapshot.object_dict(result)
                    or result.get("type") != "waveform_subscription"
                    or result.get("active") is not True
                ):
                    raise ConnectionError("recs did not activate waveforms")
                self.reconnect.wait()
            except (
                ConnectionError,
                OSError,
                TimeoutError,
                ValidationError,
                ValueError,
            ) as error:
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
        control: recs_control.RecsControlClient | None = None,
        snapshot_cache_seconds: float = recs_snapshot.STATUS_SNAPSHOT_CACHE_SECONDS,
        snapshot_timeout_seconds: float = recs_snapshot.STATUS_SNAPSHOT_TIMEOUT_SECONDS,
    ) -> None:
        self.control = control or recs_control.RecsControlClient()
        self.track_name_lock = threading.Lock()
        self.snapshot_client = recs_snapshot.RecsSnapshotClient(
            self.control,
            cache_seconds=snapshot_cache_seconds,
            timeout_seconds=snapshot_timeout_seconds,
        )

    def status(self) -> models.RecsStatus:
        snapshot = self.snapshot_client.status()
        rows = snapshot.rows
        totals = rows[0] if rows else {}
        return models.RecsStatus(
            service=models.ServiceStatus(
                name="recs",
                state=snapshot.service_state,
                last_error=snapshot.error,
            ),
            recording=snapshot.has_snapshot,
            paused=snapshot.paused,
            elapsed_seconds=_float(totals.get("time")),
            recorded_seconds=_float(totals.get("recorded")),
            file_size=_float(totals.get("file_size")),
            file_count=_int(totals.get("file_count")),
            channels=channel_levels(rows),
            errors=snapshot.errors,
            snapshot_error=snapshot.error,
            disk=snapshot.disk,
            disk_error=snapshot.error,
            playback=snapshot.playback,
            osc=snapshot.osc,
            midi=snapshot.midi,
        )

    def play(self) -> models.ActionResult:
        if self.status().playback.state == "paused":
            return self.action("continue_playback")
        return self.action("play_session", session=-1)

    def calibrate(
        self, device: str = "", channels: list[int] | None = None
    ) -> models.ActionResult:
        if device or channels is not None:
            if not device:
                return models.ActionResult(
                    ok=False, message="recs calibration device is missing"
                )
            if not channels:
                return models.ActionResult(
                    ok=False, message="recs calibration channels are missing"
                )
            parameters: dict[str, object] | None = {"channels": {device: channels}}
        else:
            parameters = None
        response = self._control_command("calibrate", parameters)
        if isinstance(response, models.ActionResult):
            return response
        if calibrated_response(response):
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
            response = self._control_command(
                "set_track_names",
                {"track_names": updated},
            )
        if isinstance(response, models.ActionResult):
            return response
        if response == "ok":
            if track_name:
                return models.ActionResult(
                    ok=True, message=f"recs track name set to {track_name}"
                )
            return models.ActionResult(
                ok=True, message=f"recs track name cleared for {channel}"
            )
        return models.ActionResult(
            ok=False, message="recs did not confirm track name update"
        )

    def set_stereo(self, device: str, channels: list[int]) -> models.ActionResult:
        with self.track_name_lock:
            tracks = stereo_tracks(self.status().channels, device, channels)
            if isinstance(tracks, models.ActionResult):
                return tracks
            track_names = self.track_names()
            if isinstance(track_names, models.ActionResult):
                return track_names
            response = self._control_command(
                "set_tracks",
                {
                    "source": device,
                    "tracks": [
                        {
                            "channels": track,
                            "name": track_name(track_names, device, track[0]),
                        }
                        for track in tracks
                    ],
                },
            )
        if isinstance(response, models.ActionResult):
            return response
        if response == "ok":
            return models.ActionResult(ok=True, message="recs stereo updated")
        return models.ActionResult(ok=False, message="recs did not update stereo")

    def track_names(self) -> dict[str, dict[str, int]] | models.ActionResult:
        response = self._control_command("get_track_names")
        if isinstance(response, models.ActionResult):
            return response
        if (track_names := track_names_response(response)) is None:
            return models.ActionResult(
                ok=False, message="recs sent invalid track names"
            )
        return track_names

    def mutable_attributes(
        self,
    ) -> list[models.MutableAttribute] | models.ActionResult:
        response = self._control_command("mutable_attributes")
        if isinstance(response, models.ActionResult):
            return response
        if not isinstance(response, dict):
            return models.ActionResult(
                ok=False,
                message="recs did not send mutable attributes",
            )
        if response.get("type") != "mutable_attributes_result":
            return models.ActionResult(
                ok=False,
                message="recs sent invalid mutable attributes",
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
            value = self._control_command("get_cfg", {"address": address})
            if isinstance(value, models.ActionResult):
                return value
            if (
                not recs_snapshot.object_dict(value)
                or value.get("type") != "cfg_value"
                or value.get("address") != address
            ):
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
        response = self._control_command(
            "set_cfg", {"address": address, "value": value}
        )
        if isinstance(response, models.ActionResult):
            return response
        if response != "ok":
            return models.ActionResult(
                ok=False,
                message=f"recs did not set {address}",
            )
        return models.ActionResult(ok=True, message=f"recs set {address}")

    def action(self, command: str, **fields: object) -> models.ActionResult:
        if command not in ACTION_COMMANDS:
            return models.ActionResult(
                ok=False, message=f"recs does not support {command}"
            )
        parameters = {k: v for k, v in fields.items() if v not in ("", None)}
        response = self._control_command(command, parameters or None)
        if command in PLAYBACK_COMMANDS:
            self.snapshot_client.invalidate()
        if isinstance(response, models.ActionResult):
            return response
        if command in DATA_RESPONSE_TYPES:
            if valid_data_response(command, response):
                return models.ActionResult(
                    ok=True,
                    message=command_result_message(command, response),
                )
        elif response == "ok":
            return models.ActionResult(
                ok=True,
                message=command_result_message(command, response),
            )
        return models.ActionResult(
            ok=False, message=f"recs sent invalid {command} response"
        )

    def shutdown(self) -> models.ActionResult:
        response = self._control_command("shutdown")
        if isinstance(response, models.ActionResult):
            return response
        if response == "ok":
            return models.ActionResult(ok=True, message="recs shutdown requested")
        return models.ActionResult(ok=False, message="recs did not confirm shutdown")

    def _control_command(
        self, command: str, parameters: dict[str, object] | None = None
    ) -> object | models.ActionResult:
        try:
            if parameters is None:
                return self.control.call(command)
            return self.control.call(command, parameters)
        except (
            ConnectionError,
            OSError,
            TimeoutError,
            ValidationError,
            ValueError,
        ) as error:
            return models.ActionResult(
                ok=False, message=f"recs {command} failed: {error}"
            )


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


def track_names_response(value: object) -> dict[str, dict[str, int]] | None:
    if not recs_snapshot.object_dict(value) or value.get("type") != "track_names":
        return None
    raw = value.get("track_names")
    if not recs_snapshot.object_dict(raw):
        return None
    result: dict[str, dict[str, int]] = {}
    for device, names in raw.items():
        if not recs_snapshot.object_dict(names):
            return None
        if any(not isinstance(v, int) or isinstance(v, bool) for v in names.values()):
            return None
        result[device] = {k: v for k, v in names.items() if isinstance(v, int)}
    return result


def calibrated_response(value: object) -> bool:
    if not recs_snapshot.object_dict(value) or value.get("type") != "calibrated":
        return False
    measurements = value.get("measurements")
    noise_floors = value.get("noise_floors")
    return (
        recs_snapshot.object_dict(measurements)
        and all(_number(v) is not None for v in measurements.values())
        and recs_snapshot.object_dict(noise_floors)
        and all(
            recs_snapshot.object_dict(v)
            and all(_number(n) is not None for n in v.values())
            for v in noise_floors.values()
        )
    )


def valid_data_response(command: str, value: object) -> bool:
    if not recs_snapshot.object_dict(value):
        return False
    if value.get("type") != DATA_RESPONSE_TYPES[command]:
        return False
    if command == "capabilities":
        commands = value.get("commands")
        version = value.get("version")
        return (
            isinstance(commands, list)
            and all(isinstance(v, str) for v in commands)
            and isinstance(version, int)
            and not isinstance(version, bool)
        )
    if command == "card_replace":
        return all(
            isinstance(value.get(k), str) and bool(value.get(k))
            for k in ("deadline", "old_mount", "old_uuid")
        )
    if command == "new_session":
        return all(
            isinstance(value.get(k), str) and bool(value.get(k))
            for k in (
                "session_id",
                "session_directory",
                "previous_record_path",
                "record_path",
            )
        )
    if command == "disk_status":
        disk = {k: v for k, v in value.items() if k != "type"}
        return not isinstance(recs_snapshot.recording_disk_status(disk), str)
    if command == "list_devices":
        devices = value.get("devices")
        return isinstance(devices, list) and all(valid_device(v) for v in devices)
    if command == "status_snapshot":
        return not isinstance(recs_snapshot.snapshot_status(value), str)
    if command in PLAYBACK_COMMANDS:
        playback = {k: v for k, v in value.items() if k != "type"}
        return not isinstance(recs_snapshot.playback_status(playback), str)
    return False


def valid_device(value: object) -> bool:
    if not recs_snapshot.object_dict(value):
        return False
    channels = value.get("channels")
    sample_rate = _number(value.get("sample_rate"))
    return (
        isinstance(value.get("name"), str)
        and isinstance(channels, int)
        and not isinstance(channels, bool)
        and channels > 0
        and sample_rate is not None
        and sample_rate > 0
        and isinstance(value.get("online"), bool)
    )


def command_result_message(command: str, response: object) -> str:
    if recs_snapshot.object_dict(response):
        response = {k: v for k, v in response.items() if k != "type"}
    elif response == "ok":
        return f"recs {command} succeeded"
    text = json.dumps(response, sort_keys=True)
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
    if recs_snapshot.object_dict(value) and isinstance(
        message := value.get("message"), str
    ):
        return message
    return ""


def _int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _number(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


ACTION_COMMANDS = {
    "capabilities",
    "card_replace",
    "disk_status",
    "list_devices",
    "mark",
    "new_session",
    "pause_recording",
    "pause_playback",
    "play_session",
    "reload_profiles",
    "resume_recording",
    "set_key_label",
    "set_noise_floor",
    "status_snapshot",
    "stop_playback",
    "continue_playback",
    "jump_playback",
    "jump_session",
}

DATA_RESPONSE_TYPES = {
    "capabilities": "capabilities_result",
    "card_replace": "card_replace_started",
    "disk_status": "disk_status_result",
    "list_devices": "devices",
    "new_session": "new_session_started",
    "pause_playback": "playback_state",
    "play_session": "playback_state",
    "status_snapshot": "status_snapshot_result",
    "stop_playback": "playback_state",
    "continue_playback": "playback_state",
    "jump_playback": "playback_state",
    "jump_session": "playback_state",
}

PLAYBACK_COMMANDS = {
    "pause_playback",
    "play_session",
    "stop_playback",
    "continue_playback",
    "jump_playback",
    "jump_session",
}
