from __future__ import annotations

import threading
import time
from pathlib import Path

from pydantic import BaseModel, Field
from reccy.protocol import rpc
from recs.daemon import paths
from typing_extensions import TypeIs

from . import models

STATUS_SNAPSHOT_CACHE_SECONDS = 1.0
STATUS_SNAPSHOT_TIMEOUT_SECONDS = 0.25


class SnapshotStatus(BaseModel, frozen=True):
    error: str | None = None
    disk: models.RecordingDiskStatus | None = None
    disk_error: str | None = None
    osc: list[models.RecorderStatus] = Field(default_factory=list)
    midi: list[models.MidiStatus] = Field(default_factory=list)


class RecsSnapshotClient:
    def __init__(
        self,
        *,
        control_endpoint: Path | str | None = None,
        cache_seconds: float = STATUS_SNAPSHOT_CACHE_SECONDS,
        timeout_seconds: float = STATUS_SNAPSHOT_TIMEOUT_SECONDS,
    ) -> None:
        self.control_endpoint = control_endpoint or paths.external_control_endpoint()
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
                response = rpc.Client(
                    self.control_endpoint,
                    role="showco",
                    timeout=self.timeout_seconds,
                ).call("status_snapshot")
            except (ConnectionError, OSError, TimeoutError, ValueError) as error:
                return self._failure(f"recs status_snapshot failed: {error}")
            if not object_dict(response):
                return self._failure("recs status snapshot is not an object")
            disk, disk_error = recording_disk_status(response.get("disk"))
            self.current = SnapshotStatus(
                disk=disk or self.current.disk,
                disk_error=disk_error,
                osc=osc_status(response),
                midi=midi_status(response),
            )
            return self.current

    def _failure(self, error: str) -> SnapshotStatus:
        self.current = self.current.model_copy(update={"error": error})
        return self.current


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
        if not object_dict(node):
            continue
        name = _string(node.get("name"))
        if name is None:
            continue
        statuses.append(
            models.RecorderStatus(
                name=name,
                state=_string(node.get("state")) or "running",
                log_path=_string(node.get("path")),
                log_size=_int(node.get("size")),
                last_error=_string(node.get("last_error")),
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


def recording_disk_status(
    value: object,
) -> tuple[models.RecordingDiskStatus | None, str | None]:
    if not object_dict(value):
        return None, "recs disk status is not an object"
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
        return None, "recs disk status is invalid"
    return (
        models.RecordingDiskStatus(
            path=path,
            used_bytes=used,
            free_bytes=free,
            total_bytes=total,
            estimated_seconds_remaining=remaining,
            alert_threshold=threshold,
            alert_active=alert,
            paused_for_disk_space=paused,
        ),
        None,
    )


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return float(value)
    return None
