from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ActionResult(BaseModel, frozen=True):
    ok: bool
    message: str


class ActionLogEntry(BaseModel, frozen=True):
    service: str
    command: str
    timestamp: datetime
    result: ActionResult


class ServiceStatus(BaseModel, frozen=True):
    name: str
    state: str
    last_error: str | None = None

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
    name: str = ""
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


class RecordingDiskStatus(BaseModel, frozen=True):
    path: str
    used_bytes: int
    free_bytes: int
    total_bytes: int
    estimated_seconds_remaining: float | None = None
    alert_threshold: str | None = None
    alert_active: bool = False
    paused_for_disk_space: bool = False


class RecsStatus(BaseModel, frozen=True):
    service: ServiceStatus
    recording: bool = False
    paused: bool = False
    elapsed_seconds: float | None = None
    recorded_seconds: float | None = None
    file_size: float | None = None
    file_count: int | None = None
    channels: list[ChannelLevel] = Field(default_factory=list)
    errors: list[ErrorRecord] = Field(default_factory=list)
    snapshot_error: str | None = None
    disk: RecordingDiskStatus | None = None
    disk_error: str | None = None
    osc: list[RecorderStatus] = Field(default_factory=list)
    midi: list[MidiStatus] = Field(default_factory=list)


class StreamoStatus(BaseModel, frozen=True):
    service: ServiceStatus
    stream_state: str = "unknown"
    streaming_service: str | None = None
    endpoint_host: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    remote_health: dict[str, object] | None = None
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
    cpu_percent: float | None = None
    cpu_error: str | None = None
    memory_used_bytes: int | None = None
    memory_total_bytes: int | None = None
    memory_error: str | None = None


class MixerStatus(BaseModel, frozen=True):
    name: str = ""
    state: str = "waiting"
    audio_ready: bool | None = None
    midi_ready: bool | None = None
    latency_ms: float | None = None
    error: str | None = None


class ReadinessCheck(BaseModel, frozen=True):
    name: str
    ok: bool
    message: str


class ReadinessStatus(BaseModel, frozen=True):
    checks: list[ReadinessCheck] = Field(default_factory=list)
    ready: bool = False


class Incident(BaseModel, frozen=True):
    timestamp: datetime
    message: str


class RecordingProgress(BaseModel, frozen=True):
    ok: bool = False
    message: str = "unknown"


class InputCheck(BaseModel, frozen=True):
    name: str
    ok: bool
    message: str


class ShowStatus(BaseModel, frozen=True):
    recs: RecsStatus
    streamo: StreamoStatus
    lyte: LyteStatus = Field(
        default_factory=lambda: LyteStatus(
            service=ServiceStatus(name="lyte", state="disabled")
        )
    )
    system: SystemStatus = Field(default_factory=SystemStatus)
    mixers: list[MixerStatus] = Field(default_factory=list)
    readiness: ReadinessStatus = Field(default_factory=ReadinessStatus)
    incidents: list[Incident] = Field(default_factory=list)
    recording_progress: RecordingProgress = Field(default_factory=RecordingProgress)
    input_checks: list[InputCheck] = Field(default_factory=list)
    revision: str | None = None
    run_started_at: float = 0.0
