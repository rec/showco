from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, Field
from reccy.runtime import logging

from . import models
from .recs_snapshot import SnapshotStatus
from .system import SystemMonitor

SAMPLE_SECONDS = 1.0
RETENTION_DAYS = 7
MONITORING_DIRECTORY = Path.home() / ".local/state/showco/monitoring"
LOGGER = logging.get_logger(__name__)


class MonitoringSample(BaseModel, frozen=True):
    time: datetime
    system: models.SystemStatus
    disk: models.RecordingDiskStatus | None = None
    errors: dict[str, str] = Field(default_factory=dict)


class MonitoringRecord(BaseModel, frozen=True):
    started_at: datetime
    ended_at: datetime
    sample_count: int
    cpu_average_percent: float | None = None
    cpu_peak_percent: float | None = None
    memory_average_used_bytes: int | None = None
    memory_peak_used_bytes: int | None = None
    memory_total_bytes: int | None = None
    disk_path: str | None = None
    disk_minimum_free_bytes: int | None = None
    disk_total_bytes: int | None = None
    disk_estimated_seconds_remaining: float | None = None
    disk_alert_occurred: bool = False
    disk_pause_occurred: bool = False
    errors: dict[str, str] = Field(default_factory=dict)


class PerformanceMonitor:
    def __init__(
        self,
        system: SystemMonitor,
        snapshot_status: Callable[[], SnapshotStatus],
        *,
        directory: Path = MONITORING_DIRECTORY,
        sample_seconds: float = SAMPLE_SECONDS,
    ) -> None:
        self.system = system
        self.snapshot_status = snapshot_status
        self.directory = directory
        self.sample_seconds = sample_seconds
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.thread: threading.Thread | None = None
        self.latest = models.SystemStatus(cpu_error="sampling", memory_error="sampling")
        self.samples: list[MonitoringSample] = []
        self.metric_errors: dict[str, str] = {}
        self.storage_error: str | None = None
        self.retention_day: date | None = None

    def start(self) -> None:
        if self.thread is not None:
            return
        self.thread = threading.Thread(
            target=self._run,
            name="showco-performance-monitor",
            daemon=True,
        )
        self.thread.start()

    def close(self) -> None:
        self.stopped.set()
        if self.thread is not None:
            self.thread.join()
        self._flush()

    def status(self) -> models.SystemStatus:
        with self.lock:
            return self.latest

    def sample(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        system = self.system.status()
        snapshot = self.snapshot_status()
        errors = metric_errors(system, snapshot)
        sample = MonitoringSample(
            time=now,
            system=system,
            disk=snapshot.disk,
            errors=errors,
        )
        with self.lock:
            self.latest = system
        self._log_metric_errors(errors)
        minute = now.replace(second=0, microsecond=0)
        current_minute = (
            self.samples[0].time.replace(second=0, microsecond=0)
            if self.samples
            else None
        )
        if current_minute is not None and current_minute != minute:
            self._write(monitoring_record(self.samples))
            self.samples = []
        self.samples.append(sample)

    def _run(self) -> None:
        next_sample = time.monotonic()
        while not self.stopped.is_set():
            self.sample()
            next_sample += self.sample_seconds
            self.stopped.wait(max(0.0, next_sample - time.monotonic()))

    def _flush(self) -> None:
        if not self.samples:
            return
        self._write(monitoring_record(self.samples))
        self.samples = []

    def _write(self, record: MonitoringRecord) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / f"{record.started_at.date().isoformat()}.jsonl"
            with path.open("a") as output:
                output.write(record.model_dump_json() + "\n")
            if self.retention_day != record.ended_at.date():
                self._remove_old_files(record.ended_at)
                self.retention_day = record.ended_at.date()
        except OSError as e:
            error = str(e)
            if error != self.storage_error:
                LOGGER.warning("monitoring history update failed: %s", error)
            self.storage_error = error
        else:
            if self.storage_error is not None:
                LOGGER.info("monitoring history writes recovered")
            self.storage_error = None

    def _remove_old_files(self, now: datetime) -> None:
        cutoff = now.date() - timedelta(days=RETENTION_DAYS - 1)
        for path in self.directory.glob("????-??-??.jsonl"):
            try:
                day = datetime.strptime(path.stem, "%Y-%m-%d").date()
            except ValueError:
                continue
            if day < cutoff:
                path.unlink()

    def _log_metric_errors(self, errors: dict[str, str]) -> None:
        for name in self.metric_errors.keys() | errors.keys():
            previous = self.metric_errors.get(name)
            current = errors.get(name)
            if current == previous:
                continue
            if current is None:
                LOGGER.info("%s monitoring recovered", name)
            else:
                LOGGER.warning("%s monitoring unavailable: %s", name, current)
        self.metric_errors = errors


def monitoring_record(samples: list[MonitoringSample]) -> MonitoringRecord:
    cpu = [s.system.cpu_percent for s in samples if s.system.cpu_percent is not None]
    memory = [
        s.system.memory_used_bytes
        for s in samples
        if s.system.memory_used_bytes is not None
    ]
    latest_memory = next(
        (
            s.system
            for s in reversed(samples)
            if s.system.memory_total_bytes is not None
        ),
        None,
    )
    latest_disk = next((s.disk for s in reversed(samples) if s.disk is not None), None)
    disk_free = (
        [
            s.disk.free_bytes
            for s in samples
            if s.disk is not None and s.disk.path == latest_disk.path
        ]
        if latest_disk is not None
        else []
    )
    errors: dict[str, str] = {}
    for sample in samples:
        errors.update(sample.errors)
    return MonitoringRecord(
        started_at=samples[0].time,
        ended_at=samples[-1].time,
        sample_count=len(samples),
        cpu_average_percent=sum(cpu) / len(cpu) if cpu else None,
        cpu_peak_percent=max(cpu, default=None),
        memory_average_used_bytes=round(sum(memory) / len(memory)) if memory else None,
        memory_peak_used_bytes=max(memory, default=None),
        memory_total_bytes=(
            latest_memory.memory_total_bytes if latest_memory is not None else None
        ),
        disk_path=latest_disk.path if latest_disk is not None else None,
        disk_minimum_free_bytes=min(disk_free, default=None),
        disk_total_bytes=latest_disk.total_bytes if latest_disk is not None else None,
        disk_estimated_seconds_remaining=(
            latest_disk.estimated_seconds_remaining if latest_disk is not None else None
        ),
        disk_alert_occurred=any(s.disk and s.disk.alert_active for s in samples),
        disk_pause_occurred=any(
            s.disk and s.disk.paused_for_disk_space for s in samples
        ),
        errors=errors,
    )


def metric_errors(
    system: models.SystemStatus, snapshot: SnapshotStatus
) -> dict[str, str]:
    errors = {}
    if system.cpu_error and system.cpu_error != "sampling":
        errors["cpu"] = system.cpu_error
    if system.memory_error and system.memory_error != "sampling":
        errors["memory"] = system.memory_error
    if snapshot.disk_error or snapshot.error:
        errors["disk"] = snapshot.disk_error or snapshot.error or "disk unavailable"
    return errors
