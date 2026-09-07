from __future__ import annotations

import threading
import time
from typing import TypeIs

from pydantic import BaseModel, Field, ValidationError

from . import models
from .recs_control import RecsControlClient

STATUS_SNAPSHOT_CACHE_SECONDS = 1.0
STATUS_SNAPSHOT_TIMEOUT_SECONDS = 0.25


class SnapshotStatus(BaseModel, frozen=True):
    service_state: str = "offline"
    error: str | None = None
    has_snapshot: bool = False
    paused: bool = False
    rows: list[dict[str, object]] = Field(default_factory=list)
    errors: list[models.ErrorRecord] = Field(default_factory=list)
    disk: models.RecordingDiskStatus | None = None
    osc: list[models.RecorderStatus] = Field(default_factory=list)
    midi: list[models.MidiStatus] = Field(default_factory=list)


class RecsSnapshotClient:
    def __init__(
        self,
        control: RecsControlClient,
        *,
        cache_seconds: float = STATUS_SNAPSHOT_CACHE_SECONDS,
        timeout_seconds: float = STATUS_SNAPSHOT_TIMEOUT_SECONDS,
    ) -> None:
        self.control = control
        self.cache_seconds = cache_seconds
        self.timeout_seconds = timeout_seconds
        self.lock = threading.Lock()
        self.current = SnapshotStatus()
        self.checked_at = 0.0

    def status(self) -> SnapshotStatus:
        with self.lock:
            now = time.monotonic()
            if now - self.checked_at < self.cache_seconds:
                return self.current
            self.checked_at = now
            try:
                response = self.control.call(
                    "status_snapshot", timeout=self.timeout_seconds
                )
            except TimeoutError as error:
                return self._transport_failure(
                    f"recs status_snapshot timed out: {error}"
                )
            except (ConnectionError, OSError) as error:
                return self._transport_failure(f"recs status_snapshot failed: {error}")
            except (ValidationError, ValueError) as error:
                return self._invalid(f"recs status_snapshot failed: {error}")

            if isinstance(parsed := snapshot_status(response), str):
                return self._invalid(parsed)
            self.current = parsed
            return self.current

    def _transport_failure(self, error: str) -> SnapshotStatus:
        state = "stale" if self.current.has_snapshot else "offline"
        self.current = self.current.model_copy(
            update={"service_state": state, "error": error}
        )
        return self.current

    def _invalid(self, error: str) -> SnapshotStatus:
        self.current = self.current.model_copy(
            update={"service_state": "error", "error": error}
        )
        return self.current


def snapshot_status(value: object) -> SnapshotStatus | str:
    if not object_dict(value):
        return "recs status snapshot is not an object"
    if value.get("type") != "status_snapshot_result":
        return "recs status snapshot has invalid type"
    if not isinstance(rows := value.get("rows"), list) or not all(
        object_dict(r) for r in rows
    ):
        return "recs status snapshot has invalid rows"
    if isinstance(errors := error_records(value.get("errors")), str):
        return errors
    recording = value.get("recording")
    if not object_dict(recording) or not isinstance(
        paused := recording.get("paused"), bool
    ):
        return "recs status snapshot has invalid recording state"
    if isinstance(disk := recording_disk_status(value.get("disk")), str):
        return disk
    osc = osc_status(value)
    if not isinstance(nodes := value.get("osc"), list) or len(osc) != len(nodes):
        return "recs status snapshot has invalid OSC status"
    midi = midi_status(value)
    if not isinstance(inputs := value.get("midi"), list) or len(midi) != len(inputs):
        return "recs status snapshot has invalid MIDI status"
    return SnapshotStatus(
        service_state="connected",
        has_snapshot=True,
        paused=paused,
        rows=rows,
        errors=errors,
        disk=disk,
        osc=osc,
        midi=midi,
    )


def object_dict(value: object) -> TypeIs[dict[str, object]]:
    return isinstance(value, dict) and all(isinstance(k, str) for k in value)


def osc_status(value: object) -> list[models.RecorderStatus]:
    if not object_dict(value):
        return []
    nodes = value.get("osc")
    if not isinstance(nodes, list):
        return []
    statuses = []
    for node in nodes:
        if not object_dict(node) or not (name := _string(node.get("name"))):
            continue
        state = node.get("state", "running")
        path = node.get("path")
        size = node.get("size")
        error = node.get("last_error")
        if (
            not isinstance(state, str)
            or path is not None
            and not isinstance(path, str)
            or size is not None
            and _int(size) is None
            or error is not None
            and not isinstance(error, str)
        ):
            continue
        statuses.append(
            models.RecorderStatus(
                name=name,
                state=state,
                log_path=path,
                log_size=size,
                last_error=error,
            )
        )
    return statuses


def midi_status(value: object) -> list[models.MidiStatus]:
    if not object_dict(value):
        return []
    midi = value.get("midi")
    if not isinstance(midi, list):
        return []
    return [
        models.MidiStatus(name=name, state=state)
        for item in midi
        if object_dict(item)
        and (name := _string(item.get("name"))) is not None
        and (state := _string(item.get("state"))) is not None
    ]


def error_records(value: object) -> list[models.ErrorRecord] | str:
    if not isinstance(value, list):
        return "recs status snapshot has invalid errors"
    errors = []
    for item in value:
        if (
            not object_dict(item)
            or (timestamp := _string(item.get("timestamp"))) is None
            or (message := _string(item.get("message"))) is None
        ):
            return "recs status snapshot has invalid errors"
        errors.append(models.ErrorRecord(timestamp=timestamp, message=message))
    return errors


def recording_disk_status(value: object) -> models.RecordingDiskStatus | str:
    if not object_dict(value):
        return "recs disk status is not an object"
    path = _string(value.get("path"))
    used = _int(value.get("used_bytes"))
    free = _int(value.get("free_bytes"))
    total = _int(value.get("total_bytes"))
    remaining_value = value.get("estimated_seconds_remaining")
    remaining = _number(remaining_value)
    threshold_value = value.get("alert_threshold")
    threshold = _string(threshold_value)
    alert = value.get("alert_active", False)
    paused = value.get("paused_for_disk_space", False)
    if (
        not path
        or used is None
        or free is None
        or total is None
        or min(used, free) < 0
        or total <= 0
        or used + free > total
        or (remaining is None and remaining_value is not None)
        or (threshold is None and threshold_value is not None)
        or not isinstance(alert, bool)
        or not isinstance(paused, bool)
    ):
        return "recs disk status is invalid"
    return models.RecordingDiskStatus(
        path=path,
        used_bytes=used,
        free_bytes=free,
        total_bytes=total,
        estimated_seconds_remaining=remaining,
        alert_threshold=threshold,
        alert_active=alert,
        paused_for_disk_space=paused,
    )


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return float(value)
    return None
