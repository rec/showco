from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field, computed_field


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


class ChannelLevel(BaseModel, frozen=True):
    name: str
    state: str
    device: str = ''
    channels: list[int] = Field(default_factory=list)
    signal: float | None = None
    on: bool = False
    recorded_seconds: float | None = None
    source_key: str = ''
    capture_running: bool | None = None
    writing_enabled: bool | None = None
    capture_age_seconds: float | None = None
    write_requested: bool | None = None


class RecorderStatus(BaseModel, frozen=True):
    name: str = ''
    state: str = 'disabled'
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
    on_system_disk: bool | None = None


class PlaybackStatus(BaseModel, frozen=True):
    state: str = 'waiting'
    session: int | None = None
    path: str | None = None
    source: str | None = None
    channel: str | None = None
    output_channel: str | None = None
    position_seconds: float | None = None
    duration_seconds: float | None = None


class RecsStatus(BaseModel, frozen=True):
    service: ServiceStatus
    snapshot_available: bool = False
    paused: bool = False
    session_started_at: float | None = None
    session_directory: str | None = None
    instance_token: str | None = None
    elapsed_seconds: float | None = None
    recorded_seconds: float | None = None
    file_size: float | None = None
    file_count: int | None = None
    channels: list[ChannelLevel] = Field(default_factory=list)
    offline_sources: list[str] = Field(default_factory=list)
    missing_audio_inputs: list[str] = Field(default_factory=list)
    errors: list[ErrorRecord] = Field(default_factory=list)
    snapshot_error: str | None = None
    disk: RecordingDiskStatus | None = None
    disk_error: str | None = None
    playback: PlaybackStatus = Field(default_factory=PlaybackStatus)
    osc: list[RecorderStatus] = Field(default_factory=list)
    midi: list[MidiStatus] = Field(default_factory=list)

    @computed_field
    @property
    def destination_text(self) -> str:
        if self.disk is None or not self.snapshot_available:
            return 'unavailable'
        if self.service.state != 'connected':
            return f'{self.disk.path} (last known; live destination unavailable)'
        if self.disk.on_system_disk:
            return f'{self.disk.path} (warning: system disk)'
        return self.disk.path


class ClosingStatus(BaseModel, frozen=True):
    operation_id: str | None = None
    state: str = 'idle'
    phase: str = 'idle'
    page: int | None = None
    page_count: int = 0
    elapsed_seconds: float = 0.0
    duration_seconds: float = 0.0
    started_at: float | None = None
    black_started_at: float | None = None
    black_at: float | None = None
    error: str | None = None


class StreamoStatus(BaseModel, frozen=True):
    service: ServiceStatus
    stream_state: str = 'unknown'
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
    closing: ClosingStatus | None = None

    @computed_field
    @property
    def toggle_label(self) -> str:
        if self.service.state == 'disabled':
            return 'Twitch disabled'
        if self.closing_in_progress or self.stream_state == 'stopping':
            return 'Stopping...'
        if self.stream_state == 'publishing':
            return 'Stop Twitch attempt'
        if self.service.state == 'offline' or self.stream_state == 'unknown':
            return 'Stop Twitch (status unavailable)'
        if self.stream_state == 'starting':
            return 'Stop Twitch (starting)'
        if self.toggle_starts_stream:
            return (
                'Retry Twitch (error)'
                if self.stream_state == 'failed'
                else 'Start Twitch'
            )
        return 'Stop Twitch'

    @computed_field
    @property
    def toggle_starts_stream(self) -> bool:
        return (
            self.service.state in {'connected', 'error'}
            and self.stream_state in {'stopped', 'failed'}
            and not self.ffmpeg_alive
        )

    @computed_field
    @property
    def toggle_available(self) -> bool:
        return (
            self.service.state != 'disabled'
            and self.stream_state != 'stopping'
            and not self.closing_in_progress
        )

    @property
    def closing_in_progress(self) -> bool:
        return self.closing is not None and self.closing.state not in {
            'idle',
            'completed',
            'failed',
        }


class LyteStringStatus(BaseModel, frozen=True):
    state: str = 'pending'
    host: str | None = None
    mac: str | None = None
    led_count: int | None = None
    frame_count: int = 0
    failure_count: int = 0
    last_error: str | None = None


class LyteStatus(BaseModel, frozen=True):
    service: ServiceStatus
    running: bool = False
    animations: list[str] = Field(default_factory=list)
    blackout: bool = False
    active_animation: str | None = None
    queued_animation: str | None = None
    strings: dict[str, LyteStringStatus] = Field(default_factory=dict)
    bindings: dict[str, str] = Field(default_factory=dict)
    midi_connected: bool = False
    midi_error: str | None = None
    note: int | None = None
    breath: int | None = None
    pitch_bend: int | None = None
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
    name: str = ''
    state: str = 'waiting'
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


class InputRecording(BaseModel, frozen=True):
    name: str
    message: str
    ok: bool = False
    fault: bool = False


class RecordingProgress(BaseModel, frozen=True):
    ok: bool = False
    message: str = 'unknown'


class ActiveFault(BaseModel, frozen=True):
    name: str
    started_at: datetime
    message: str
    next_action: str
    acknowledged: bool = False


class InputCheck(BaseModel, frozen=True):
    name: str
    ok: bool
    message: str


class MusicStatus(BaseModel, frozen=True):
    x18_usb_routing: ReadinessCheck | None = None
    closing_warning: str | None = None
    mode: str = 'stopped'
    state: str = 'stopped'
    directory: Path | None = None
    track: Path | None = None
    error: str | None = None
    closing: ClosingStatus | None = None

    @computed_field
    @property
    def restart_available(self) -> bool:
        return self.mode in {'closing', 'teardown'} or (
            self.closing is not None and self.closing.operation_id is not None
        )


class CableTestStatus(BaseModel, frozen=True):
    state: str = 'idle'
    message: str = 'No cable test has run'
    elapsed_seconds: float = 0.0
    duration_seconds: float = 0.0


class NetworkStatus(BaseModel, frozen=True):
    connected: bool = False
    message: str


class ShowStatus(BaseModel, frozen=True):
    recs: RecsStatus
    streamo: StreamoStatus
    lyte: LyteStatus = Field(
        default_factory=lambda: LyteStatus(
            service=ServiceStatus(name='lyte', state='disabled')
        )
    )
    system: SystemStatus = Field(default_factory=SystemStatus)
    network: NetworkStatus | None = None
    mixers: list[MixerStatus] = Field(default_factory=list)
    readiness: ReadinessStatus = Field(default_factory=ReadinessStatus)
    incidents: list[Incident] = Field(default_factory=list)
    recording_progress: RecordingProgress = Field(default_factory=RecordingProgress)
    recording_inputs: list[InputRecording] = Field(default_factory=list)
    input_checks: list[InputCheck] = Field(default_factory=list)
    music: MusicStatus = Field(default_factory=MusicStatus)
    cable_test: CableTestStatus = Field(default_factory=CableTestStatus)
    revision: str | None = None
    run_started_at: float = 0.0
    performance_locked: bool = False
    recording_controls_available: bool = True
    active_faults: list[ActiveFault] = Field(default_factory=list)
    observed_at: datetime | None = None
    monitoring_error: str | None = None

    @computed_field
    @property
    def recording_coverage(self) -> RecordingProgress:
        if self.recs.service.state != 'connected' or not self.recs.snapshot_available:
            return RecordingProgress(
                ok=False, message='Input recording status unavailable'
            )
        if self.recs.paused:
            return RecordingProgress(ok=False, message='Recording paused on all inputs')
        if not self.recording_inputs:
            return RecordingProgress(ok=False, message='No recording inputs available')
        failed = sum(i.fault for i in self.recording_inputs)
        if failed:
            return RecordingProgress(
                ok=False,
                message=f'Incomplete recording on {failed} input'
                + ('s' if failed > 1 else ''),
            )
        if any(not i.ok for i in self.recording_inputs):
            return RecordingProgress(ok=False, message='Recording coverage unconfirmed')
        return RecordingProgress(ok=True, message='All configured inputs accounted for')

    twitch_blockers: list[str] = Field(default_factory=list)
    twitch_recovery_pending: bool = False
    twitch_recovery_message: str = ''

    @computed_field
    @property
    def twitch_message(self) -> str:
        if self.streamo.service.state == 'disabled':
            return ''
        parts = ['Twitch off'] if self.streamo.toggle_starts_stream else []
        if self.streamo.stream_state == 'publishing':
            parts.append('Twitch live status is unverified')
        if self.twitch_recovery_message:
            parts.append(self.twitch_recovery_message)
        if self.twitch_recovery_pending and self.twitch_blockers:
            parts.append(
                'Automatic recovery waiting: ' + '. '.join(self.twitch_blockers)
            )
        return '. '.join(parts)

    @computed_field
    @property
    def twitch_action(self) -> str:
        return 'streamo-start' if self.streamo.toggle_starts_stream else 'streamo-stop'

    @computed_field
    @property
    def twitch_label(self) -> str:
        return self.streamo.toggle_label

    @computed_field
    @property
    def twitch_stop_available(self) -> bool:
        return self.streamo.toggle_available and self.music.mode not in {
            'closing',
            'transitioning',
        }

    @computed_field
    @property
    def twitch_toggle_available(self) -> bool:
        return self.twitch_stop_available and (
            not self.streamo.toggle_starts_stream
            or (
                not self.performance_locked
                and self.music.mode not in {'fault', 'teardown'}
            )
        )

    @computed_field
    @property
    def end_show_available(self) -> bool:
        return (
            not self.performance_locked
            and self.music.mode not in {'closing', 'transitioning', 'fault'}
            and (
                self.music.mode != 'teardown'
                or self.streamo.stream_state in {'publishing', 'streaming', 'muted'}
            )
        )
