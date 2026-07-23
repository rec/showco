from __future__ import annotations

from pathlib import Path

from .models import SystemStatus

RASPBERRY_PI_TEMPERATURE = Path("/sys/class/thermal/thermal_zone0/temp")


class SystemMonitor:
    def __init__(self, *, temperature_path: Path = RASPBERRY_PI_TEMPERATURE) -> None:
        self.temperature_path = temperature_path

    def status(self) -> SystemStatus:
        if not self.temperature_path.exists():
            return SystemStatus(temperature_error="temperature sensor unavailable")
        try:
            value = int(self.temperature_path.read_text().strip())
        except ValueError:
            return SystemStatus(temperature_error="temperature sensor is invalid")
        except OSError as e:
            return SystemStatus(temperature_error=f"temperature sensor failed: {e}")
        return SystemStatus(temperature_c=value / 1000)
