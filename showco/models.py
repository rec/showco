from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    message: str


@dataclass(frozen=True)
class ServiceStatus:
    name: str
    state: str
    last_error: str | None = None
    updated_at: float | None = None

    @property
    def fresh(self) -> bool:
        return self.state == "connected"


@dataclass(frozen=True)
class ChannelLevel:
    name: str
    state: str
    signal: float | None = None


@dataclass(frozen=True)
class RecsStatus:
    service: ServiceStatus
    recording: bool = False
    elapsed_seconds: float | None = None
    recorded_seconds: float | None = None
    file_size: float | None = None
    file_count: int | None = None
    client_count: int = 0
    channels: list[ChannelLevel] = field(default_factory=list)


@dataclass(frozen=True)
class TwitchoStatus:
    service: ServiceStatus
    stream_state: str = "unknown"
    muted: bool = False
    ffmpeg_alive: bool = False
    audio_seconds: float | None = None
    clipping: bool = False


@dataclass(frozen=True)
class SystemStatus:
    temperature_c: float | None = None
    temperature_error: str | None = None


@dataclass(frozen=True)
class ShowStatus:
    recs: RecsStatus
    twitcho: TwitchoStatus
    system: SystemStatus = field(default_factory=SystemStatus)
    generated_at: float = field(default_factory=time.time)
