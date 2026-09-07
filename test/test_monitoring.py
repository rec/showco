from __future__ import annotations

import json
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

from showco import models
from showco.monitoring import PerformanceMonitor
from showco.recs_snapshot import SnapshotStatus
from showco.system import SystemMonitor


class SequenceSystemMonitor(SystemMonitor):
    def __init__(self, statuses: list[models.SystemStatus]) -> None:
        self.statuses = iter(statuses)

    def status(self) -> models.SystemStatus:
        return next(self.statuses)


class MonitoringTests(unittest.TestCase):
    def test_completed_minute_records_peaks_averages_and_disk_alerts(self) -> None:
        with TemporaryDirectory() as directory:
            system = SequenceSystemMonitor(
                [
                    system_status(cpu=20, memory=200),
                    system_status(cpu=80, memory=600),
                    system_status(cpu=40, memory=400),
                ]
            )
            snapshots = iter(
                [
                    snapshot(free=900),
                    snapshot(free=800, alert=True),
                    snapshot(free=700),
                ]
            )
            monitor = PerformanceMonitor(
                system,
                lambda: next(snapshots),
                directory=Path(directory),
            )

            monitor.sample(at(12, 0, 0))
            monitor.sample(at(12, 0, 1))
            monitor.sample(at(12, 1, 0))
            record = json.loads((Path(directory) / "2026-09-07.jsonl").read_text())

        self.assertEqual(record["sample_count"], 2)
        self.assertEqual(record["cpu_average_percent"], 50)
        self.assertEqual(record["cpu_peak_percent"], 80)
        self.assertEqual(record["memory_average_used_bytes"], 400)
        self.assertEqual(record["memory_peak_used_bytes"], 600)
        self.assertEqual(record["disk_minimum_free_bytes"], 800)
        self.assertTrue(record["disk_alert_occurred"])

    def test_close_writes_partial_minute_and_removes_old_days(self) -> None:
        with TemporaryDirectory() as directory:
            history = Path(directory)
            (history / "2026-08-31.jsonl").write_text("old\n")
            (history / "2026-09-01.jsonl").write_text("retained\n")
            monitor = PerformanceMonitor(
                SequenceSystemMonitor([system_status(cpu=20, memory=200)]),
                lambda: snapshot(free=900),
                directory=history,
            )

            monitor.sample(at(12, 0, 0))
            monitor.close()

            record = json.loads((history / "2026-09-07.jsonl").read_text())
            files = sorted(p.name for p in history.iterdir())

        self.assertEqual(record["sample_count"], 1)
        self.assertEqual(files, ["2026-09-01.jsonl", "2026-09-07.jsonl"])

    def test_storage_failure_does_not_replace_latest_status(self) -> None:
        with TemporaryDirectory() as directory:
            history = Path(directory) / "not-a-directory"
            history.write_text("file")
            status = system_status(cpu=20, memory=200)
            monitor = PerformanceMonitor(
                SequenceSystemMonitor([status]),
                lambda: snapshot(free=900),
                directory=history,
            )

            monitor.sample(at(12, 0, 0))
            monitor.close()

        self.assertEqual(monitor.status(), status)

    def test_sampler_runs_without_status_requests_and_stops(self) -> None:
        sampled = Event()
        calls = []

        class SignalingSystemMonitor(SystemMonitor):
            def status(self) -> models.SystemStatus:
                calls.append(None)
                sampled.set()
                return system_status(cpu=20, memory=200)

        with TemporaryDirectory() as directory:
            monitor = PerformanceMonitor(
                SignalingSystemMonitor(),
                lambda: snapshot(free=900),
                directory=Path(directory),
                sample_seconds=0.01,
            )

            monitor.start()
            self.assertTrue(sampled.wait(1))
            monitor.close()
            count = len(calls)
            time.sleep(0.03)

        self.assertEqual(len(calls), count)


def system_status(*, cpu: float, memory: int) -> models.SystemStatus:
    return models.SystemStatus(
        temperature_c=50,
        cpu_percent=cpu,
        memory_used_bytes=memory,
        memory_total_bytes=1000,
    )


def snapshot(*, free: int, alert: bool = False) -> SnapshotStatus:
    return SnapshotStatus(
        disk=models.RecordingDiskStatus(
            path="/recordings",
            used_bytes=1000 - free,
            free_bytes=free,
            total_bytes=1000,
            estimated_seconds_remaining=3600,
            alert_active=alert,
        )
    )


def at(hour: int, minute: int, second: int) -> datetime:
    return datetime(2026, 9, 7, hour, minute, second, tzinfo=timezone.utc)


if __name__ == "__main__":
    unittest.main()
