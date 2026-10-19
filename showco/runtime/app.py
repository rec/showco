from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError
from reccy.runtime import logging

from ..streamo.client import StreamoClient
from ..x18.cable_test import (
    TONE_SECONDS,
    CableTester,
    parse_range,
    validate_channels,
    validate_duration,
    validate_sends,
)
from . import (
    incidents,
    input_check,
    lighting,
    models,
    music,
    performance,
    readiness,
    recording_progress,
    recovery,
    recs_actions,
    services,
    setlist,
    soundcheck,
    workflows,
)
from .lyte import LyteClient
from .mixer import MixersMonitor
from .monitoring import PerformanceMonitor
from .recs import RecsClient
from .system import SystemMonitor
from .waveforms import WaveformBridge

LOGGER = logging.get_logger(__name__)


class ShowcoApp:
    def __init__(
        self,
        recs: RecsClient,
        streamo: StreamoClient | None,
        system: SystemMonitor | PerformanceMonitor,
        mixers: MixersMonitor,
        streamo_restart: Callable[[], models.ActionResult] | None = None,
        waveforms: WaveformBridge | None = None,
        lyte: LyteClient | None = None,
        cable_tester: CableTester | None = None,
        music_controller: music.MusicController | None = None,
        state_directory: Path | None = None,
    ) -> None:
        self.recs = recs
        self.streamo = streamo
        self.system = system
        self.mixers = mixers
        self.streamo_restart = streamo_restart or (
            lambda: services.restart_service('streamo')
        )
        self.recovery_restart: Callable[[str], models.ActionResult] = (
            services.restart_service
        )
        self.waveforms = waveforms
        self.lyte = lyte
        self.cable_tester = cable_tester
        self.music = music_controller
        self.revision = source_revision()
        self.run_started_at = time.time()
        self.action_log: list[models.ActionLogEntry] = []
        self.action_lock = threading.Lock()
        self.action_in_progress = False
        self.action_log_lock = threading.Lock()
        self.cable_test_lock = threading.Lock()
        self.cable_test_value = models.CableTestStatus()
        self.cable_test_started_at = 0.0
        self.status_lock = threading.Lock()
        self.soundcheck_lock = threading.Lock()
        self.soundcheck_error: str | None = None
        self.performance_lock = performance.PerformanceLock(
            state_directory / 'performance.json' if state_directory else None
        )
        self.incidents = incidents.IncidentTimeline(
            state_directory / 'incidents.json' if state_directory else None
        )
        self.recording_progress = recording_progress.ProgressMonitor()
        self.setlist = setlist.SetListController(
            state_directory / 'setlist.json' if state_directory else None
        )
        self.lighting = lighting.LightingController(
            state_directory / 'lighting.json' if state_directory else None
        )
        self.soundcheck = soundcheck.Soundcheck(
            state_directory / 'soundcheck.json' if state_directory else None
        )
        self.recovery = recovery.Recovery(
            state_directory / 'recovery.json' if state_directory else None
        )

    def status(self) -> models.ShowStatus:
        with self.status_lock:
            if self.streamo is None:
                streamo = models.StreamoStatus(
                    service=models.ServiceStatus(name='streamo', state='disabled')
                )
            else:
                streamo = self.streamo.status()
            recs = self.recs.status()
            recs = recs.model_copy(
                update={'errors': errors_since(recs.errors, self.run_started_at)}
            )
            lyte = (
                self.lyte.status()
                if self.lyte is not None
                else models.LyteStatus(
                    service=models.ServiceStatus(name='lyte', state='disabled')
                )
            )
            status = models.ShowStatus(
                recs=recs,
                streamo=streamo,
                lyte=lyte,
                system=self.system.status(),
                mixers=self.mixers.status(
                    {channel.device for channel in recs.channels},
                    {midi.name: midi.state for midi in recs.midi},
                ),
                revision=self.revision,
                run_started_at=self.run_started_at,
                music=self.music.status()
                if self.music is not None
                else models.MusicStatus(),
            )
            status = status.model_copy(update={'readiness': readiness.status(status)})
            status = status.model_copy(
                update={'recording_progress': self.recording_progress.observe(recs)}
            )
            with self.soundcheck_lock:
                try:
                    workflows.refresh_soundcheck(self, status)
                except OSError as error:
                    self.soundcheck_error = f'Soundcheck evidence unavailable: {error}'
                else:
                    self.soundcheck_error = None
            observed = self.incidents.observe(status)
            cable_status = self.cable_test_status()
            return status.model_copy(
                update={
                    'incidents': observed,
                    'active_faults': list(self.incidents.faults),
                    'performance_locked': self.performance_lock.locked,
                    'observed_at': datetime.now().astimezone(),
                    'monitoring_error': '; '.join(
                        error
                        for error in (
                            self.performance_lock.error,
                            self.incidents.storage_error,
                            self.soundcheck_error,
                            self.setlist.load_error,
                            self.lighting.load_error,
                            self.soundcheck.load_error,
                            self.recovery.load_error,
                            self.system.observation_error
                            if isinstance(self.system, PerformanceMonitor)
                            else None,
                            self.system.storage_error
                            if isinstance(self.system, PerformanceMonitor)
                            else None,
                        )
                        if error
                    )
                    or None,
                    'input_checks': input_check.checks(recs.channels),
                    'cable_test': cable_status,
                    'recording_controls_available': not self.action_in_progress
                    and status.music.mode not in {'closing', 'transitioning'}
                    and cable_status.state != 'running',
                }
            )

    def cable_test_status(self) -> models.CableTestStatus:
        with self.cable_test_lock:
            value = self.cable_test_value
            if value.state == 'running':
                return value.model_copy(
                    update={
                        'elapsed_seconds': time.monotonic() - self.cable_test_started_at
                    }
                )
            return value

    def start_cable_test(self, form: dict[str, str]) -> models.ActionResult:
        if self.cable_tester is None:
            return models.ActionResult(
                ok=False, message='X18 cable test is not configured'
            )
        if self.music is not None:
            status = self.music.status()
            if (
                status.mode in {'closing', 'transitioning', 'fault'}
                or status.state == 'playing'
            ):
                return models.ActionResult(
                    ok=False,
                    message='Stop incidental music and resolve any show transition '
                    'before testing cables',
                )
        channels = parse_range(form.get('channels', ''), 1, 18, 'channels')
        sends = parse_range(form.get('sends', ''), 1, 6, 'sends')
        duration = float(form.get('duration-seconds', str(TONE_SECONDS)))
        validate_channels(channels)
        validate_sends(sends)
        validate_duration(duration)
        with self.cable_test_lock:
            if self.cable_test_value.state == 'running':
                return models.ActionResult(
                    ok=False, message='X18 cable test is already running'
                )
            self.cable_test_started_at = time.monotonic()
            self.cable_test_value = models.CableTestStatus(
                state='running',
                message=f'Testing channels {", ".join(str(c) for c in channels)}',
                duration_seconds=duration,
            )
        thread = threading.Thread(
            target=self._run_cable_test,
            args=(channels, sends, duration),
            name='showco-cable-test',
            daemon=True,
        )
        try:
            thread.start()
        except RuntimeError as error:
            with self.cable_test_lock:
                self.cable_test_value = models.CableTestStatus(
                    state='failed', message=f'Could not start cable test: {error}'
                )
            return models.ActionResult(ok=False, message=str(error))
        return models.ActionResult(
            ok=True, message='X18 cable test started; its result appears on Actions'
        )

    def _run_cable_test(
        self, channels: list[int], sends: list[int], duration: float
    ) -> None:
        assert self.cable_tester is not None
        try:
            report = self.cable_tester.run(channels, sends, duration_seconds=duration)
        except (
            ConnectionError,
            OSError,
            TimeoutError,
            ValueError,
            RuntimeError,
            TypeError,
            MemoryError,
        ) as error:
            state, message = 'failed', str(error)
        else:
            state = 'passed' if report.passed else 'failed'
            message = report.message()
        with self.cable_test_lock:
            self.cable_test_value = models.CableTestStatus(
                state=state,
                message=message,
                elapsed_seconds=time.monotonic() - self.cable_test_started_at,
                duration_seconds=duration,
            )
        (LOGGER.info if state == 'passed' else LOGGER.error)(
            'X18 cable test %s: %s', state, message.replace('\n', '; ')
        )

    def run_action(self, form: dict[str, str]) -> models.ActionResult:
        action = form.get('action', '')
        acquired = self.action_lock.acquire(blocking=action not in RECORDING_ACTIONS)
        try:
            if not acquired:
                result = models.ActionResult(
                    ok=False,
                    message='Another action is in progress; '
                    'retry recording controls when it finishes',
                )
            else:
                self.action_in_progress = True
                try:
                    result = self._dispatch_action(action, form)
                except (
                    ConnectionError,
                    OSError,
                    subprocess.SubprocessError,
                    TimeoutError,
                    ValidationError,
                    ValueError,
                ) as error:
                    result = models.ActionResult(ok=False, message=str(error))
        finally:
            if acquired:
                self.action_in_progress = False
                self.action_lock.release()
        with self.action_log_lock:
            entry = action_log_entry(action, result)
            self.action_log = [entry, *self.action_log[:9]]
        return result

    def _dispatch_action(
        self, action: str, form: dict[str, str]
    ) -> models.ActionResult:
        if action in {'performance-lock', 'performance-unlock'}:
            locked = action == 'performance-lock'
            if not locked and form.get('confirmation') != 'unlock':
                return models.ActionResult(
                    ok=False,
                    message='Confirm unlock before changing protected controls',
                )
            self.performance_lock.set(locked)
            return models.ActionResult(
                ok=True,
                message='Performance lock enabled'
                if locked
                else 'Performance lock disabled',
            )
        if self.performance_lock.locked and action in performance.PROTECTED_ACTIONS:
            return models.ActionResult(
                ok=False,
                message='Performance lock blocks this action. '
                'Unlock explicitly, then submit it again.',
            )
        if action in CABLE_TEST_CONFLICTING_ACTIONS and (
            self.cable_test_status().state == 'running'
        ):
            return models.ActionResult(
                ok=False,
                message='X18 cable test owns the recs pause; wait for its result '
                'before changing recording, playback or show mode',
            )
        if action == 'acknowledge-fault':
            with self.status_lock:
                self.incidents.acknowledge(
                    form.get('name', ''), form.get('started_at', '')
                )
            return models.ActionResult(
                ok=True, message='Fault acknowledged; it remains active until recovery'
            )
        if (
            action in RECORDING_ACTIONS
            and self.music is not None
            and self.music.status().mode in {'closing', 'transitioning'}
        ):
            return models.ActionResult(
                ok=False,
                message='A show transition is in progress; '
                'retry recording controls when it finishes',
            )
        if action.startswith(('setlist-', 'soundcheck-', 'recovery-', 'lighting-')):
            return workflows.run(self, form)
        if action in performance.PROTECTED_ACTIONS:
            with self.soundcheck_lock:
                try:
                    self.soundcheck.invalidate(
                        'Configuration or output test requested; repeat soundcheck'
                    )
                except OSError as error:
                    self.soundcheck_error = (
                        f'Soundcheck invalidation not saved: {error}'
                    )
        if action.startswith('recs-'):
            return recs_actions.run(self.recs, action, form)
        if action == 'streamo-toggle':
            if self.streamo is None:
                return models.ActionResult(ok=False, message='streamO is disabled')
            status = self.streamo.status()
            if not status.toggle_available or (
                self.music is not None
                and self.music.status().mode in {'closing', 'transitioning', 'fault'}
            ):
                return models.ActionResult(
                    ok=False,
                    message='Twitch switching is unavailable during a transition '
                    'or closing sequence, or when streamO is disabled',
                )
            if status.toggle_starts_stream:
                if self.music is not None and self.music.status().mode == 'teardown':
                    return models.ActionResult(
                        ok=False,
                        message='The show has ended. Use Resume show on Actions to '
                        'restore the room mix and recording before broadcasting.',
                    )
                return self.streamo_restart()
            result = self.streamo.action('stop')
            if not result.ok:
                return result
            try:
                service = services.service_registry().controller('streamo')
                service.stop()
                stopped = service.status().running is False
            except (OSError, subprocess.SubprocessError) as error:
                return models.ActionResult(
                    ok=False, message=f'Twitch stop could not be confirmed: {error}'
                )
            return models.ActionResult(
                ok=stopped,
                message='Twitch stopped'
                if stopped
                else 'Twitch stop could not be confirmed',
            )
        if action == 'streamo-restart' and self.streamo is None:
            return models.ActionResult(ok=False, message='streamo is disabled')
        if action == 'streamo-restart':
            return self.streamo_restart()
        if action == 'lyte-test':
            return (
                self.lyte.test()
                if self.lyte is not None
                else models.ActionResult(ok=False, message='lyte is disabled')
            )
        if action == 'cable-test':
            return self.start_cable_test(form)
        if action.startswith('music-'):
            if self.music is None:
                return models.ActionResult(
                    ok=False, message='X18 music is not configured'
                )
            match action:
                case 'music-setup':
                    return self.music.setup()
                case 'music-record':
                    return self.music.record()
                case 'music-teardown':
                    return self.music.teardown()
                case 'music-restart':
                    if not self.music.status().restart_available:
                        return models.ActionResult(
                            ok=False, message='Teardown has not been requested'
                        )
                    return self.music.record(restart=True)
                case 'music-close-cancel':
                    return self.music.recover_closing(False)
                case 'music-close-finish':
                    if form.get('confirmation') != 'broadcast-stopped':
                        return models.ActionResult(
                            ok=True, message='Manual closing recovery canceled'
                        )
                    return self.music.recover_closing(True)
                case 'music-stop':
                    if form.get('confirmation') != 'shutdown':
                        return models.ActionResult(
                            ok=True, message='Pi shutdown canceled'
                        )
                    return self.music.stop()
            return models.ActionResult(
                ok=False, message=f'unknown music action {action}'
            )
        if action in STREAMO_ACTIONS:
            if self.streamo is None:
                return models.ActionResult(ok=False, message='streamo is disabled')
            return self.streamo.action(STREAMO_ACTIONS[action], **_streamo_fields(form))
        return models.ActionResult(ok=False, message=f'unknown action {action}')

    def recent_actions(self) -> list[models.ActionLogEntry]:
        with self.action_log_lock:
            return list(self.action_log)


def source_revision() -> str | None:
    try:
        result = subprocess.run(
            ['git', '-C', str(Path(__file__).parent.parent), 'rev-parse', 'HEAD'],
            capture_output=True,
            check=False,
            text=True,
        )
    except FileNotFoundError:
        return None
    if result.returncode:
        return None
    return result.stdout.strip() or None


def errors_since(
    errors: list[models.ErrorRecord], started_at: float
) -> list[models.ErrorRecord]:
    return [
        e
        for e in errors
        if (timestamp := error_timestamp(e.timestamp)) is None
        or timestamp >= started_at
    ]


def error_timestamp(value: str) -> float | None:
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def action_log_entry(action: str, result: models.ActionResult) -> models.ActionLogEntry:
    service, separator, command = action.partition('-')
    if not separator:
        service, command = 'showco', action or 'unknown'
    return models.ActionLogEntry(
        service=service,
        command=command,
        timestamp=datetime.now().astimezone(),
        result=result,
    )


def _streamo_fields(form: dict[str, str]) -> dict[str, object]:
    return {k: v for k, v in form.items() if k != 'action' and v}


RECORDING_ACTIONS = {
    'recs-pause-recording',
    'recs-resume-recording',
    'soundcheck-start-recording',
    'soundcheck-pause-recording',
}


STREAMO_ACTIONS = {
    'streamo-mute': 'mute',
    'streamo-unmute': 'unmute',
    'streamo-stop': 'stop',
    'streamo-title': 'update_stream_info',
    'streamo-chat': 'chat',
    'streamo-announce': 'announce',
    'streamo-clip': 'clip',
    'streamo-marker': 'marker',
}


CABLE_TEST_CONFLICTING_ACTIONS = {
    'music-setup',
    'music-record',
    'music-teardown',
    'music-restart',
    'music-stop',
    'music-close-cancel',
    'music-close-finish',
    'soundcheck-start-recording',
    'soundcheck-pause-recording',
    'recovery-restart',
    'recs-pause-recording',
    'recs-resume-recording',
    'recs-new-session',
    'recs-shutdown',
    'recs-calibrate',
    'recs-reload-profiles',
    'recs-playback-play',
    'recs-playback-pause',
    'recs-playback-stop',
    'recs-playback-jump',
    'recs-playback-jump-session',
}
