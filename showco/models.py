from __future__ import annotations

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
    channels: list[int] = Field(default_factory=list)
    signal: float | None = None
    on: bool = False


class RecorderStatus(BaseModel, frozen=True):
    state: str = "disabled"
    log_path: str | None = None
    log_size: int | None = None
    last_error: str | None = None


class MidiStatus(BaseModel, frozen=True):
    name: str
    state: str


class MutableAttribute(BaseModel, frozen=True):
    address: str
    value: object


class ErrorRecord(BaseModel, frozen=True):
    timestamp: str
    message: str


class RecsStatus(BaseModel, frozen=True):
    service: ServiceStatus
    recording: bool = False
    elapsed_seconds: float | None = None
    recorded_seconds: float | None = None
    file_size: float | None = None
    file_count: int | None = None
    client_count: int = 0
    channels: list[ChannelLevel] = Field(default_factory=list)
    errors: list[ErrorRecord] = Field(default_factory=list)
    snapshot_error: str | None = None
    x18: RecorderStatus = Field(default_factory=RecorderStatus)
    midi: list[MidiStatus] = Field(default_factory=list)


class TwitchoStatus(BaseModel, frozen=True):
    service: ServiceStatus
    stream_state: str = "unknown"
    muted: bool = False
    ffmpeg_alive: bool = False
    audio_seconds: float | None = None
    last_audio_at: float | None = None
    clipping: bool = False
    output_bitrate_kbps: float | None = None


class LyteStatus(BaseModel, frozen=True):
    service: ServiceStatus
    daemon_state: str = "disabled"
    output_state: str = "unknown"
    host: str | None = None
    device_mac: str | None = None
    planned_led_count: int | None = None
    actual_led_count: int | None = None
    frame_send_count: int | None = None
    last_frame_sent_at: str | None = None
    queued_test: bool = False
    active_test: bool = False


class SystemStatus(BaseModel, frozen=True):
    temperature_c: float | None = None
    temperature_error: str | None = None


class MixerStatus(BaseModel, frozen=True):
    name: str = ""
    state: str = "waiting"
    audio_ready: bool | None = None
    midi_ready: bool | None = None
    latency_ms: float | None = None
    error: str | None = None


class ShowStatus(BaseModel, frozen=True):
    recs: RecsStatus
    twitcho: TwitchoStatus
    lyte: LyteStatus = Field(
        default_factory=lambda: LyteStatus(
            service=ServiceStatus(name="lyte", state="disabled")
        )
    )
    system: SystemStatus = Field(default_factory=SystemStatus)
    mixers: list[MixerStatus] = Field(default_factory=list)
    x18: RecorderStatus = Field(default_factory=RecorderStatus)
    revision: str | None = None
    run_started_at: float = 0.0
