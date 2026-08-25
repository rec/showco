from __future__ import annotations

import socket
import threading
import time
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from reccy.device import AudioMidiDeviceSpec

from .models import MixerStatus

MIXER_TIMEOUT_SECONDS = 0.5
MIXER_PROBE_INTERVAL_SECONDS = 5.0


class MixerProbeSpec(BaseModel):
    host: str
    port: int
    protocol: Literal["tcp", "udp"] = "tcp"

    model_config = ConfigDict(frozen=True)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("port")
    @classmethod
    def validate_port(cls, value: int) -> int:
        if not 0 < value <= 65_535:
            raise ValueError("must be between 1 and 65535")
        return value


class MixerOscSpec(BaseModel):
    host: str
    port: int
    subscription_path: str
    resubscribe_period: float

    model_config = ConfigDict(frozen=True)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("port")
    @classmethod
    def validate_port(cls, value: int) -> int:
        if not 0 < value <= 65_535:
            raise ValueError("must be between 1 and 65535")
        return value

    @field_validator("subscription_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("must start with /")
        return value

    @field_validator("resubscribe_period")
    @classmethod
    def validate_period(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("must be positive")
        return value


class MixerSpec(AudioMidiDeviceSpec, frozen=True):
    probe: MixerProbeSpec | None = None
    osc: MixerOscSpec | None = None


class MixerSpecs(BaseModel):
    mixers: list[MixerSpec] = Field(default_factory=list)

    model_config = ConfigDict(frozen=True)

    @model_validator(mode="after")
    def validate_names(self) -> MixerSpecs:
        names = [mixer.name for mixer in self.mixers]
        if len(names) != len(set(names)):
            raise ValueError("mixer names must be unique")
        return self


def load_mixer_specs(path: Path) -> list[MixerSpec]:
    if not path.name:
        return []
    return MixerSpecs.model_validate(tomllib.loads(path.read_text())).mixers


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
                sock.send(b"/xremote\0\0\0\0,\0\0\0")
                sock.recv(1)
                return MixerStatus(latency_ms=elapsed_ms(start))
            except OSError as e:
                return MixerStatus(error=f"mixer UDP probe failed: {e}")


def elapsed_ms(start: float) -> float:
    return (time.monotonic() - start) * 1000


class MixersMonitor:
    def __init__(self, specs: list[MixerSpec]) -> None:
        self.specs = specs
        self.monitors = {
            spec.name: MixerMonitor(
                host=spec.probe.host,
                port=spec.probe.port,
                protocol=spec.probe.protocol,
            )
            for spec in specs
            if spec.probe is not None
        }
        self.probe_seen: set[str] = set()

    def status(
        self, audio_devices: set[str], midi: dict[str, str]
    ) -> list[MixerStatus]:
        return [
            self._status(spec, audio_devices, midi)
            for spec in sorted(self.specs, key=lambda spec: spec.name)
        ]

    def _status(
        self, spec: MixerSpec, audio_devices: set[str], midi: dict[str, str]
    ) -> MixerStatus:
        audio_ready = (
            any(
                name.startswith(prefix)
                for prefix in spec.audio_device_names
                for name in audio_devices
            )
            if spec.audio_device_names
            else None
        )
        midi_ready = (
            any(
                name.startswith(prefix) and state == "recording"
                for prefix in spec.midi_input_names
                for name, state in midi.items()
            )
            if spec.midi_input_names
            else None
        )
        ready = [value for value in (audio_ready, midi_ready) if value is not None]
        state = "connected" if all(ready) else "partial" if any(ready) else "waiting"
        monitor = self.monitors.get(spec.name)
        if monitor is None:
            return MixerStatus(
                name=spec.name,
                state=state,
                audio_ready=audio_ready,
                midi_ready=midi_ready,
            )
        probe = monitor.status()
        if probe.error:
            waiting = state == "waiting" or spec.name not in self.probe_seen
            return MixerStatus(
                name=spec.name,
                state="waiting" if waiting else "error",
                audio_ready=audio_ready,
                midi_ready=midi_ready,
                error=None if waiting else probe.error,
            )
        self.probe_seen.add(spec.name)
        return MixerStatus(
            name=spec.name,
            state=state,
            audio_ready=audio_ready,
            midi_ready=midi_ready,
            latency_ms=probe.latency_ms,
        )
