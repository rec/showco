from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from showco.system import SystemMonitor


class SystemTests(unittest.TestCase):
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
