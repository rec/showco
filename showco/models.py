from __future__ import annotations

import time

from pydantic import BaseModel, Field


class ActionResult(BaseModel, frozen=True):
    ok: bool
    message: str


class ServiceStatus(BaseModel, frozen=True):
    name: str
    state: str
    last_error: str | None = None
    updated_at: float | None = None

    @property
    def fresh(self) -> bool:
        return self.state == "connected"


class ChannelLevel(BaseModel, frozen=True):
    name: str
    state: str
    device: str = ""
    signal: float | None = None


class RecsStatus(BaseModel, frozen=True):
    service: ServiceStatus
    recording: bool = False
    elapsed_seconds: float | None = None
    recorded_seconds: float | None = None
    file_size: float | None = None
    file_count: int | None = None
    client_count: int = 0
    channels: list[ChannelLevel] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class TwitchoStatus(BaseModel, frozen=True):
    service: ServiceStatus
    stream_state: str = "unknown"
    muted: bool = False
    ffmpeg_alive: bool = False
    audio_seconds: float | None = None
    clipping: bool = False
    output_bitrate_kbps: float | None = None


class SystemStatus(BaseModel, frozen=True):
    temperature_c: float | None = None
    temperature_error: str | None = None


class MixerStatus(BaseModel, frozen=True):
    latency_ms: float | None = None
    error: str | None = None


class ShowStatus(BaseModel, frozen=True):
    recs: RecsStatus
    twitcho: TwitchoStatus
    system: SystemStatus = Field(default_factory=SystemStatus)
    mixer: MixerStatus = Field(default_factory=MixerStatus)
    generated_at: float = Field(default_factory=time.time)
