from __future__ import annotations

import socket
import time

from .models import MixerStatus

MIXER_TIMEOUT_SECONDS = 0.5


class MixerMonitor:
    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        protocol: str = "tcp",
        timeout_seconds: float = MIXER_TIMEOUT_SECONDS,
    ) -> None:
        self.host = host
        self.port = port
        self.protocol = protocol
        self.timeout_seconds = timeout_seconds

    def status(self) -> MixerStatus:
        if self.host is None or self.port is None:
            return MixerStatus(error="mixer probe not configured")
        if self.protocol == "tcp":
            return self.tcp_status()
        if self.protocol == "udp":
            return self.udp_status()
        return MixerStatus(error=f"unknown mixer probe protocol {self.protocol}")

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
                sock.send(b"\0")
                sock.recv(1)
                return MixerStatus(latency_ms=elapsed_ms(start))
            except OSError as e:
                return MixerStatus(error=f"mixer UDP probe failed: {e}")


def elapsed_ms(start: float) -> float:
    return (time.monotonic() - start) * 1000
