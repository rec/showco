from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from showco.system import SystemMonitor


class SystemTests(unittest.TestCase):
    def test_status_calculates_cpu_and_memory_usage(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            temperature = root / "temp"
            stat = root / "stat"
            meminfo = root / "meminfo"
            temperature.write_text("52750\n")
            stat.write_text("cpu 100 0 0 100 0 0 0 0\n")
            meminfo.write_text("MemTotal: 1000 kB\nMemAvailable: 250 kB\n")
            monitor = SystemMonitor(
                temperature_path=temperature,
                stat_path=stat,
                meminfo_path=meminfo,
            )

            initial = monitor.status()
            stat.write_text("cpu 150 0 0 150 0 0 0 0\n")
            status = monitor.status()

        self.assertIsNone(initial.cpu_percent)
        self.assertEqual(initial.cpu_error, "sampling")
        self.assertEqual(status.cpu_percent, 50)
        self.assertIsNone(status.cpu_error)
        self.assertEqual(status.memory_used_bytes, 750 * 1024)
        self.assertEqual(status.memory_total_bytes, 1000 * 1024)

    def test_status_reports_independent_cpu_and_memory_errors(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            temperature = root / "temp"
            stat = root / "stat"
            meminfo = root / "meminfo"
            temperature.write_text("52750\n")
            stat.write_text("not cpu data\n")
            meminfo.write_text("MemTotal: 1000 kB\n")

            status = SystemMonitor(
                temperature_path=temperature,
                stat_path=stat,
                meminfo_path=meminfo,
            ).status()

        self.assertEqual(status.temperature_c, 52.75)
        self.assertEqual(status.cpu_error, "CPU counters are invalid")
        self.assertEqual(status.memory_error, "memory information is invalid")

    def test_status_rejects_cpu_counters_that_do_not_advance(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            stat = root / "stat"
            stat.write_text("cpu 100 0 0 100 0 0 0 0\n")
            monitor = SystemMonitor(
                temperature_path=root / "temp",
                stat_path=stat,
                meminfo_path=root / "meminfo",
            )

            monitor.status()
            status = monitor.status()

        self.assertIsNone(status.cpu_percent)
        self.assertEqual(status.cpu_error, "CPU counters did not advance")

    def test_status_reads_raspberry_pi_temperature(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "temp"
            path.write_text("52750\n")

            status = SystemMonitor(temperature_path=path).status()

        self.assertEqual(status.temperature_c, 52.75)
        self.assertIsNone(status.temperature_error)

    def test_status_reports_missing_temperature_sensor(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "temp"

            status = SystemMonitor(temperature_path=path).status()

        self.assertIsNone(status.temperature_c)
        self.assertEqual(status.temperature_error, "temperature sensor unavailable")


if __name__ == "__main__":
    unittest.main()
