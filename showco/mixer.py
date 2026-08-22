from __future__ import annotations

import socket
import threading
import time

from .models import MixerStatus

MIXER_TIMEOUT_SECONDS = 0.5
MIXER_PROBE_INTERVAL_SECONDS = 5.0


class MixerMonitor:
    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        protocol: str = "tcp",
        timeout_seconds: float = MIXER_TIMEOUT_SECONDS,
        probe_interval_seconds: float = MIXER_PROBE_INTERVAL_SECONDS,
    ) -> None:
        self.host = host
        self.port = port
        self.protocol = protocol
        self.timeout_seconds = timeout_seconds
        self.probe_interval_seconds = probe_interval_seconds
        self.last_status: MixerStatus | None = None
        self.last_checked_at = 0.0
        self.lock = threading.Lock()

    def status(self) -> MixerStatus:
        if self.host is None or self.port is None:
            return MixerStatus(error="mixer probe not configured")
        with self.lock:
            if (
                self.last_status is not None
                and time.monotonic() - self.last_checked_at
                < self.probe_interval_seconds
            ):
                return self.last_status
            if self.protocol == "tcp":
                self.last_status = self.tcp_status()
            elif self.protocol == "udp":
                self.last_status = self.udp_status()
            else:
                self.last_status = MixerStatus(
                    error=f"unknown mixer probe protocol {self.protocol}"
                )
            self.last_checked_at = time.monotonic()
            return self.last_status

    def tcp_status(self) -> MixerStatus:
        if self.host is None or self.port is None:
            return MixerStatus(error="mixer probe not configured")
        start = time.monotonic()
        try:
            with socket.create_connection(
                (self.host, self.port), timeout=self.timeout_seconds
            ):
                return MixerStatus(latency_ms=elapsed_ms(start))
        except OSError as e:
            return MixerStatus(error=f"mixer TCP probe failed: {e}")

    def udp_status(self) -> MixerStatus:
        if self.host is None or self.port is None:
            return MixerStatus(error="mixer probe not configured")
        start = time.monotonic()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(self.timeout_seconds)
            try:
                sock.connect((self.host, self.port))
                sock.send(b'/xremote\0\0\0\0,\0\0\0')
                sock.recv(1)
                return MixerStatus(latency_ms=elapsed_ms(start))
            except OSError as e:
                return MixerStatus(error=f"mixer UDP probe failed: {e}")


def elapsed_ms(start: float) -> float:
    return (time.monotonic() - start) * 1000
