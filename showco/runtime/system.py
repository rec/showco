from __future__ import annotations

from pathlib import Path

from .models import SystemStatus

RASPBERRY_PI_TEMPERATURE = Path("/sys/class/thermal/thermal_zone0/temp")
PROC_STAT = Path("/proc/stat")
PROC_MEMINFO = Path("/proc/meminfo")


class SystemMonitor:
    def __init__(
        self,
        *,
        temperature_path: Path = RASPBERRY_PI_TEMPERATURE,
        stat_path: Path = PROC_STAT,
        meminfo_path: Path = PROC_MEMINFO,
    ) -> None:
        self.temperature_path = temperature_path
        self.stat_path = stat_path
        self.meminfo_path = meminfo_path
        self.previous_cpu: tuple[int, int] | None = None

    def status(self) -> SystemStatus:
        temperature_c, temperature_error = self._temperature()
        cpu_percent, cpu_error = self._cpu()
        memory_used_bytes, memory_total_bytes, memory_error = self._memory()
        return SystemStatus(
            temperature_c=temperature_c,
            temperature_error=temperature_error,
            cpu_percent=cpu_percent,
            cpu_error=cpu_error,
            memory_used_bytes=memory_used_bytes,
            memory_total_bytes=memory_total_bytes,
            memory_error=memory_error,
        )

    def _temperature(self) -> tuple[float | None, str | None]:
        if not self.temperature_path.exists():
            return None, "temperature sensor unavailable"
        try:
            value = int(self.temperature_path.read_text().strip())
        except ValueError:
            return None, "temperature sensor is invalid"
        except OSError as e:
            return None, f"temperature sensor failed: {e}"
        return value / 1000, None

    def _cpu(self) -> tuple[float | None, str | None]:
        try:
            fields = self.stat_path.read_text().splitlines()[0].split()
            if not fields or fields[0] != "cpu" or len(fields) < 5:
                raise ValueError
            counters = [int(v) for v in fields[1:9]]
            if any(v < 0 for v in counters):
                raise ValueError
        except (IndexError, ValueError):
            return None, "CPU counters are invalid"
        except FileNotFoundError:
            return None, "CPU counters are unavailable"
        except OSError as e:
            return None, f"CPU counters failed: {e}"

        current = sum(counters), counters[3] + (counters[4] if len(counters) > 4 else 0)
        previous = self.previous_cpu
        self.previous_cpu = current
        if previous is None:
            return None, "sampling"
        total_delta = current[0] - previous[0]
        idle_delta = current[1] - previous[1]
        if total_delta <= 0:
            return None, "CPU counters did not advance"
        percent = 100 * (total_delta - idle_delta) / total_delta
        return min(100.0, max(0.0, percent)), None

    def _memory(self) -> tuple[int | None, int | None, str | None]:
        try:
            values = {}
            for line in self.meminfo_path.read_text().splitlines():
                name, separator, value = line.partition(":")
                if separator and name in {"MemTotal", "MemAvailable"}:
                    fields = value.split()
                    if len(fields) != 2 or fields[1] != "kB":
                        raise ValueError
                    values[name] = int(fields[0]) * 1024
            total = values["MemTotal"]
            available = values["MemAvailable"]
            if total <= 0 or available < 0 or available > total:
                raise ValueError
        except (KeyError, ValueError):
            return None, None, "memory information is invalid"
        except FileNotFoundError:
            return None, None, "memory information is unavailable"
        except OSError as e:
            return None, None, f"memory information failed: {e}"
        return total - available, total, None
