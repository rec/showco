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
    lighting,
    models,
    music,
    performance,
    readiness,
    recording_progress,
    recovery,
    recs_actions,
    recs_snapshot,
    services,
    setlist,
    soundcheck,
    twitch,
    workflows,
)
from .lyte import LyteClient
from .mixer import MixersMonitor
from .monitoring import PerformanceMonitor
from .network import ExternalWifi
from .recs import RecsClient
from .system import SystemMonitor
from .waveforms import WaveformBridge

LOGGER = logging.get_logger(__name__)
STATUS_REFRESH_SECONDS = 1.0
STATUS_MAX_AGE_SECONDS = 5.0
STATUS_WAIT_SECONDS = 0.4
STATUS_STORAGE_WARNING_SECONDS = 2.0
SOUNDCHECK_INVALIDATED = 'Configuration or output test requested; repeat soundcheck'


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
        external_wifi: ExternalWifi | None = None,
        streamo_stop: Callable[[], models.ActionResult] | None = None,
        twitch_readiness: twitch.TwitchReadiness | None = None,
        twitch_state: Path | None = None,
    ) -> None:
        self.recs = recs
        self.streamo = streamo
        self.system = system
        self.mixers = mixers
        self.streamo_restart = streamo_restart or (
            lambda: services.restart_service('streamo')
        )
        self.streamo_stop = streamo_stop or services.stop_streamo
        self.twitch_readiness = twitch_readiness
        self.twitch = twitch.TwitchRecovery(twitch_state)
        self.twitch_probe_lock = threading.Lock()
        self.twitch_probe_after = 0.0
        self.twitch_blockers = (
            ['Checking Twitch readiness'] if twitch_readiness is not None else []
        )
        self.recovery_restart: Callable[[str], models.ActionResult] = (
            services.restart_service
        )
        self.waveforms = waveforms
        self.lyte = lyte
        self.cable_tester = cable_tester
        self.music = music_controller
        self.external_wifi = external_wifi
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
        self.status_ready = threading.Event()
        self.latest_status: tuple[models.ShowStatus, float] | None = None
        self.recs_clock_session: float | None = None
        self.recs_clock_uncertain = False
        self.incident_lock = threading.Lock()
        self.status_storage_lock = threading.Lock()
        self.status_storage_started: float | None = None
        self.soundcheck_lock = threading.Lock()
        self.soundcheck_dirty = threading.Event()
        self.soundcheck_error: str | None = None
        self.reported_monitoring_error: str | None = None
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
        if not self.status_lock.acquire(blocking=False):
            raise TimeoutError('Live status is being collected; retry shortly')
        return self._refresh_status()

    def observe_status(self) -> None:
        latest = self.latest_status
        if latest is None or time.monotonic() - latest[1] >= STATUS_REFRESH_SECONDS:
            if self.status_lock.acquire(blocking=False):
                self.status_ready.clear()
                try:
                    threading.Thread(
                        target=self._observe,
                        name='showco-status-observation',
                        daemon=True,
                    ).start()
                except RuntimeError as error:
                    self.status_ready.set()
                    self.status_lock.release()
                    LOGGER.error('Cannot start live status observation: %s', error)

    def http_status(self) -> models.ShowStatus | None:
        self.observe_status()
        latest = self.latest_status
        if latest is None or time.monotonic() - latest[1] >= STATUS_MAX_AGE_SECONDS:
            self.status_ready.wait(STATUS_WAIT_SECONDS)
            latest = self.latest_status
        if latest is not None and time.monotonic() - latest[1] < STATUS_MAX_AGE_SECONDS:
            status = latest[0]
            return status.model_copy(
                update={
                    'performance_locked': self.performance_lock.locked,
                    'recording_controls_available': status.recording_controls_available
                    and not self.action_in_progress,
                }
            )
        return None

    def unavailable_status(self) -> models.ShowStatus:
        message = 'Live status unavailable; waiting for a fresh observation'
        return models.ShowStatus(
            recs=models.RecsStatus(
                service=models.ServiceStatus(
                    name='recs', state='offline', last_error=message
                )
            ),
            streamo=models.StreamoStatus(
                service=models.ServiceStatus(
                    name='streamo',
                    state='disabled' if self.streamo is None else 'offline',
                    last_error=message,
                ),
            ),
            readiness=models.ReadinessStatus(
                checks=[
                    models.ReadinessCheck(name='Live status', ok=False, message=message)
                ]
            ),
            recording_controls_available=False,
            music=models.MusicStatus(mode='fault', error=message),
            performance_locked=self.performance_lock.locked,
            monitoring_error=message,
            revision=self.revision,
        )

    def _observe(self) -> None:
        try:
            status = self._refresh_status()
            self.observe_twitch(status)
        except (OSError, ValueError, RuntimeError) as error:
            LOGGER.warning('Service observation failed: %s', error)

    def _refresh_status(self) -> models.ShowStatus:
        self.status_ready.clear()
        started = time.monotonic()
        try:
            status = self._collect_status()
            self.latest_status = status, started
        finally:
            self.status_ready.set()
            self.status_lock.release()
        self._schedule_status_storage()
        return status

    def _schedule_status_storage(self) -> None:
        if self.latest_status is None or not self.status_storage_lock.acquire(False):
            return
        self.status_storage_started = time.monotonic()
        try:
            threading.Thread(
                target=self._save_observation, name='showco-status-storage', daemon=True
            ).start()
        except RuntimeError as error:
            self.status_storage_started = None
            self.status_storage_lock.release()
            LOGGER.error('Cannot start status storage: %s', error)

    def _save_observation(self) -> None:
        try:
            with self.incident_lock:
                history = (
                    self.incidents.history() if self.incidents.pending_save else None
                )
                self.incidents.pending_save = False
            if history is not None:
                try:
                    self.incidents.write(history)
                except OSError as error:
                    with self.incident_lock:
                        self.incidents.pending_save = True
                        self.incidents.storage_error = (
                            f'Cannot save incident history: {error}'
                        )
                else:
                    self.incidents.storage_error = self.incidents.load_error
            latest = self.latest_status
            if latest is not None and self.soundcheck_lock.acquire(blocking=False):
                try:
                    if self.soundcheck_dirty.is_set():
                        self.soundcheck_dirty.clear()
                        self.soundcheck.invalidate(SOUNDCHECK_INVALIDATED)
                    workflows.refresh_soundcheck(
                        self.soundcheck, latest[0], self.revision
                    )
                except OSError as error:
                    self.soundcheck_error = f'Soundcheck evidence unavailable: {error}'
                else:
                    self.soundcheck_error = None
                finally:
                    self.soundcheck_lock.release()
        finally:
            self.status_storage_started = None
            self.status_storage_lock.release()

    def _collect_status(self) -> models.ShowStatus:
        observed_at = datetime.now().astimezone()
        if self.streamo is None:
            streamo = models.StreamoStatus(
                service=models.ServiceStatus(name='streamo', state='disabled')
            )
        else:
            streamo = self.streamo.status()
        twitch_off = streamo.toggle_starts_stream
        twitch_run = self.twitch.run
        recovery_needed = bool(twitch_run and twitch_run.needs_recovery(streamo))
        recs = self.recs.status()
        session_started_at = recs.session_started_at
        if session_started_at != self.recs_clock_session:
            self.recs_clock_session = session_started_at
            self.recs_clock_uncertain = False
        previous = self.latest_status
        previous_at = previous[0].observed_at if previous is not None else None
        # A session name is a wall-clock label. Once its clock disagrees with
        # live observations, keep the recorder's warnings for this session.
        if (
            session_started_at is not None
            and session_started_at > observed_at.timestamp()
        ) or (previous_at is not None and observed_at < previous_at):
            self.recs_clock_uncertain = True
        if not self.recs_clock_uncertain:
            cutoff = (
                self.run_started_at
                if session_started_at is None
                else session_started_at
            )
            recs = recs.model_copy(
                update={'errors': recs_snapshot.errors_since(recs.errors, cutoff)}
            )
        lyte = (
            self.lyte.status()
            if self.lyte is not None
            else models.LyteStatus(
                service=models.ServiceStatus(name='lyte', state='disabled')
            )
        )
        mixers = self.mixers.status(
            {channel.device for channel in recs.channels},
            {midi.name: midi.state for midi in recs.midi},
        )
        status = models.ShowStatus(
            recs=recs,
            streamo=streamo,
            lyte=lyte,
            system=self.system.status(),
            network=self.external_wifi.status() if self.external_wifi else None,
            mixers=mixers,
            revision=self.revision,
            run_started_at=self.run_started_at,
            twitch_blockers=self.twitch_blockers if twitch_off else [],
            twitch_recovery_pending=bool(
                recovery_needed
                and twitch_run
                and twitch_run.attempts < twitch.MAX_RECOVERIES
            ),
            twitch_recovery_message=self.twitch.message(
                recovery_needed, streamo.service.last_error or ''
            ),
            music=self.music.status()
            if self.music is not None
            else models.MusicStatus(),
        )
        status = status.model_copy(update={'readiness': readiness.status(status)})
        status = status.model_copy(
            update={'recording_progress': self.recording_progress.observe(recs)}
        )
        with self.incident_lock:
            observed = self.incidents.observe(status, persist=False)
            faults = list(self.incidents.faults)
        cable_status = self.cable_test_status()
        storage_started = self.status_storage_started
        monitoring_error = (
            '; '.join(
                error
                for error in (
                    self.performance_lock.error,
                    self.incidents.storage_error,
                    'Status history or soundcheck storage is stalled'
                    if storage_started is not None
                    and time.monotonic() - storage_started
                    >= STATUS_STORAGE_WARNING_SECONDS
                    else None,
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
            or None
        )
        if monitoring_error != self.reported_monitoring_error:
            if monitoring_error:
                LOGGER.error('Show monitoring: %s', monitoring_error)
            else:
                LOGGER.info('Show monitoring recovered')
            self.reported_monitoring_error = monitoring_error
        return status.model_copy(
            update={
                'incidents': observed,
                'active_faults': faults,
                'performance_locked': self.performance_lock.locked,
                'observed_at': observed_at,
                'monitoring_error': monitoring_error,
                'input_checks': readiness.input_checks(recs.channels),
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
        generation = self.twitch.generation
        stop = action == 'streamo-stop'
        acquired = not stop and self.action_lock.acquire(blocking=False)
        try:
            if stop:
                if self.twitch.stop_lock.acquire(blocking=False):
                    try:
                        result = self._twitch_action(action, generation)
                    except (OSError, ValueError, subprocess.SubprocessError) as error:
                        result = models.ActionResult(ok=False, message=str(error))
                    finally:
                        self.twitch.stop_lock.release()
                else:
                    result = models.ActionResult(
                        ok=False, message='Twitch stop is in progress'
                    )
            elif not acquired:
                result = models.ActionResult(
                    ok=False,
                    message='Another action is in progress; retry when it finishes',
                )
            else:
                self.action_in_progress = True
                try:
                    result = self._dispatch_action(action, form, generation)
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
        self, action: str, form: dict[str, str], generation: int
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
            with self.incident_lock:
                self.incidents.acknowledge(
                    form.get('name', ''), form.get('started_at', ''), persist=False
                )
            self._schedule_status_storage()
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
            return self._run_workflow(form)
        if action in performance.PROTECTED_ACTIONS and action not in {
            'streamo-start',
            'streamo-restart',
        }:
            self.soundcheck_dirty.set()
            self._schedule_status_storage()
        if action.startswith('recs-'):
            return recs_actions.run(self.recs, action, form)
        if action in {'streamo-start', 'streamo-restart'}:
            return self._twitch_action(action, generation)
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
                    self.twitch.cancel()
                    return self.music.setup()
                case 'music-record':
                    return self.music.record()
                case 'music-teardown':
                    self.twitch.cancel()
                    return self.music.teardown()
                case 'music-restart':
                    if not self.music.status().restart_available:
                        return models.ActionResult(
                            ok=False, message='Teardown has not been requested'
                        )
                    self.twitch.cancel()
                    return self.music.record(restart=True)
                case 'music-close-cancel':
                    self.twitch.cancel()
                    return self.music.abandon_closing()
                case 'music-close-finish':
                    if form.get('confirmation') != 'broadcast-stopped':
                        return models.ActionResult(
                            ok=True, message='Manual closing recovery canceled'
                        )
                    return self.music.finish_closing()
                case 'music-stop':
                    if form.get('confirmation') != 'shutdown':
                        return models.ActionResult(
                            ok=True, message='Pi shutdown canceled'
                        )
                    self.twitch.cancel()
                    return self.music.stop()
            return models.ActionResult(
                ok=False, message=f'unknown music action {action}'
            )
        if action in STREAMO_ACTIONS:
            if self.streamo is None:
                return models.ActionResult(ok=False, message='streamo is disabled')
            return self.streamo.action(STREAMO_ACTIONS[action], **_streamo_fields(form))
        return models.ActionResult(ok=False, message=f'unknown action {action}')

    def workflow_status(
        self, show: models.ShowStatus | None = None
    ) -> workflows.WorkflowStatus:
        show = show or self.status()
        return workflows.WorkflowStatus(
            show=show,
            setlist=self.setlist.state,
            lighting=self.lighting.state,
            soundcheck=self.soundcheck.state,
            recovery=self.recovery.state,
            soundcheck_error=self.soundcheck_error,
            inputs=[
                workflows.ExpectedInput(key=soundcheck.input_key(c), name=c.name)
                for c in show.recs.channels
            ],
        )

    def _run_workflow(self, form: dict[str, str]) -> models.ActionResult:
        action = form['action']
        if action.startswith('lighting-'):
            return workflows.run_lighting(self.lighting, self.lyte, form)
        if action.startswith('setlist-'):
            return workflows.run_setlist(self.setlist, self.recs, form)
        show = self.status()
        if action.startswith('recovery-'):
            if action == 'recovery-restart' and form.get('service') == 'streamo':
                return models.ActionResult(
                    ok=False, message='Use Start Twitch on the Show page'
                )
            result = workflows.run_recovery(
                self.recovery,
                form,
                show,
                self._refresh_recovery,
                self.recovery_restart,
                self._remember_recovery,
                self._invalidate_soundcheck,
            )
            if action == 'recovery-restart':
                self.recs.snapshot_client.invalidate()
            return result
        if not self.soundcheck_lock.acquire(blocking=False):
            raise TimeoutError('Soundcheck evidence is busy; retry shortly')
        try:
            if self.soundcheck_dirty.is_set():
                raise TimeoutError('Soundcheck invalidation is pending; retry shortly')
            if self.soundcheck_error:
                raise ValueError(self.soundcheck_error)
            return workflows.run_soundcheck(
                self.soundcheck,
                self.recs,
                self.lyte,
                form,
                show,
                verify_capture=self.twitch_readiness is not None,
            )
        finally:
            self.soundcheck_lock.release()

    def _refresh_recovery(self) -> models.ShowStatus:
        self.recs.snapshot_client.invalidate()
        return self.status()

    def _twitch_action(self, action: str, generation: int) -> models.ActionResult:
        if self.streamo is None:
            return models.ActionResult(ok=False, message='streamO is disabled')
        mode = self.music.status().mode if self.music is not None else None
        if mode == 'transitioning':
            return models.ActionResult(
                ok=False, message='A show transition is in progress'
            )
        if mode == 'closing':
            return models.ActionResult(
                ok=False,
                message='Use the closing recovery controls during closing credits',
            )
        if action == 'streamo-stop':
            self.twitch.cancel()
        status = (
            self.streamo.status(include_service_status=False)
            if action == 'streamo-stop'
            else self.streamo.status()
        )
        if status.closing_in_progress:
            return models.ActionResult(
                ok=False,
                message='Use the closing recovery controls during closing credits',
            )
        if status.service.state == 'disabled':
            return models.ActionResult(ok=False, message='streamO is disabled')
        if action == 'streamo-stop':
            service = self.streamo_stop()
            if service.ok:
                if self.twitch.error:
                    return models.ActionResult(
                        ok=False, message=f'{service.message}. {self.twitch.error}'
                    )
                return service
            stop = self.streamo.action('stop')
            if not stop.ok:
                return models.ActionResult(
                    ok=False,
                    message=f'{service.message}. '
                    f'Publisher stop failed or is unconfirmed: {stop.message}',
                )
            after = self.streamo.status(include_service_status=False)
            publisher = (
                'Twitch publisher stopped'
                if after.stream_state == 'stopped' and not after.ffmpeg_alive
                else 'streamO accepted the publisher stop request. '
                'Broadcast stop is unconfirmed'
            )
            return models.ActionResult(
                ok=False, message=f'{publisher}. {service.message}'
            )
        if action == 'streamo-restart':
            return models.ActionResult(
                ok=False, message='Use Start Twitch on the Show page'
            )
        if mode == 'teardown':
            return models.ActionResult(
                ok=False,
                message='The show has ended. Use Resume show on Actions to '
                'restore the room mix and recording before broadcasting.',
            )
        if mode == 'fault' or not status.toggle_starts_stream:
            return models.ActionResult(
                ok=False,
                message='Twitch start refused: publisher may already be active '
                'or the show phase needs recovery',
            )
        if (
            self.twitch_readiness is not None
            and self.twitch.run
            and self.twitch.run.attempts < twitch.MAX_RECOVERIES
            and (
                self.twitch.run.needs_recovery(status)
                or time.monotonic() < self.twitch.run.retry_after
            )
        ):
            return models.ActionResult(
                ok=False, message='Automatic Twitch recovery is pending'
            )
        config_sha256 = None
        if self.twitch_readiness is not None:
            if not self.twitch_probe_lock.acquire(blocking=False):
                return models.ActionResult(
                    ok=False, message='Checking Twitch readiness; retry shortly'
                )
            try:
                show = self._fresh_twitch_status(status)
                evidence = self.soundcheck.state
                self.twitch_blockers = self._check_twitch(show)
                if self.twitch_blockers:
                    return models.ActionResult(
                        ok=False, message='. '.join(self.twitch_blockers)
                    )
                if not self._twitch_check_still_current(show, evidence):
                    return models.ActionResult(
                        ok=False, message='Twitch readiness changed; retry when ready'
                    )
                config_sha256 = self.twitch_readiness.config_sha256
            finally:
                self.twitch_probe_lock.release()
        authorized = self.twitch.authorize(
            twitch.BroadcastRun(
                retry_after=time.monotonic() + twitch.RETRY_DELAY_SECONDS
            ),
            generation,
            config_sha256,
        )
        if not authorized:
            return models.ActionResult(ok=False, message='Twitch start canceled')
        return self._launch_twitch(generation)

    def _fresh_twitch_status(self, streamo: models.StreamoStatus) -> models.ShowStatus:
        self.recs.snapshot_client.invalidate()
        return models.ShowStatus(
            recs=self.recs.status(),
            streamo=streamo,
            music=self.music.status()
            if self.music is not None
            else models.MusicStatus(),
        )

    def _check_twitch(self, show: models.ShowStatus) -> list[str]:
        if self.twitch_readiness is None:
            return []
        if (
            self.soundcheck_dirty.is_set()
            or self.soundcheck_error
            or self.soundcheck.pending_save
        ):
            return ['Waiting for current Soundcheck evidence']
        return self.twitch_readiness.check(show, self.soundcheck, self.revision)

    def _launch_twitch(self, generation: int) -> models.ActionResult:
        if generation != self.twitch.generation:
            return models.ActionResult(ok=False, message='Twitch start canceled')
        try:
            result = self.streamo_restart()
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            result = models.ActionResult(ok=False, message=str(error))
        if generation != self.twitch.generation:
            stopped = self.streamo_stop()
            return models.ActionResult(
                ok=False, message=f'Twitch start canceled. {stopped.message}'
            )
        if not result.ok:
            LOGGER.error('Twitch start failed: %s', result.message)
            self.twitch.launch_failed(generation, result.message)
        return result

    def observe_twitch(self, status: models.ShowStatus) -> None:
        if (
            self.twitch_readiness is None
            or self.streamo is None
            or not status.streamo.toggle_starts_stream
            or status.music.mode in {'closing', 'teardown', 'transitioning', 'fault'}
            or time.monotonic() < self.twitch_probe_after
            or not self.twitch_probe_lock.acquire(blocking=False)
        ):
            return
        try:
            generation = self.twitch.generation
            run = self.twitch.run
            if (
                run is not None
                and status.streamo.stream_state == 'stopped'
                and not run.launch_failed
                and time.monotonic() >= run.retry_after
                and self.action_lock.acquire(False)
            ):
                try:
                    if (
                        self.twitch.run is run
                        and self.streamo.status().stream_state == 'stopped'
                    ):
                        self.twitch.cancel(generation=generation)
                finally:
                    self.action_lock.release()
            show = self._fresh_twitch_status(status.streamo)
            evidence = self.soundcheck.state
            self.twitch_blockers = self._check_twitch(show)
            if (
                self.twitch_blockers
                or run is None
                or run.attempts >= twitch.MAX_RECOVERIES
                or not run.needs_recovery(status.streamo)
                or time.monotonic() < run.retry_after
                or not self.action_lock.acquire(False)
            ):
                return
            try:
                if self.twitch.run is not run:
                    return
                if not self._twitch_check_still_current(show, evidence):
                    return
                failure = (
                    status.streamo.service.last_error
                    or run.failure
                    or 'Publisher stopped'
                )
                if not self.twitch.authorize(
                    twitch.BroadcastRun(
                        attempts=run.attempts + 1,
                        retry_after=time.monotonic() + twitch.RETRY_DELAY_SECONDS,
                        failure=failure,
                    ),
                    generation,
                    self.twitch_readiness.config_sha256,
                ):
                    return
                LOGGER.warning(
                    'Twitch automatic recovery %d/%d: %s',
                    run.attempts + 1,
                    twitch.MAX_RECOVERIES,
                    failure,
                )
                self._launch_twitch(generation)
            finally:
                self.action_lock.release()
        finally:
            self.twitch_probe_after = time.monotonic() + twitch.RETRY_DELAY_SECONDS
            self.twitch_probe_lock.release()

    def _twitch_check_still_current(
        self, checked: models.ShowStatus, evidence: soundcheck.SoundcheckState
    ) -> bool:
        assert self.streamo is not None
        fresh = self._fresh_twitch_status(self.streamo.status())
        current = (
            fresh.streamo.toggle_starts_stream
            and fresh.streamo.stream_state == checked.streamo.stream_state
            and twitch.same_recording(fresh.recs, checked.recs)
            and fresh.music.mode
            not in {'closing', 'teardown', 'transitioning', 'fault'}
            and not self.soundcheck_dirty.is_set()
            and self.soundcheck.state is evidence
            and self.soundcheck.scope(
                fresh, workflows.soundcheck_configuration(self.revision)
            )
            == evidence.scope
        )
        if not current or not evidence.playback_pause:
            return current
        assert self.twitch_readiness is not None
        if not self.soundcheck_lock.acquire(blocking=False):
            return False
        try:
            if self.soundcheck.state is not evidence:
                return False
            capture_clocks = self.twitch_readiness.capture_clocks
            self.soundcheck.save(
                evidence.model_copy(
                    update={
                        'recording_clocks': capture_clocks,
                        'playback_pause': False,
                    }
                )
            )
            return True
        finally:
            self.soundcheck_lock.release()

    def _remember_recovery(self, message: str) -> None:
        with self.incident_lock:
            self.incidents.record(message, persist=False)
        self._schedule_status_storage()

    def _invalidate_soundcheck(
        self, message: str = 'Service restart requested; repeat soundcheck'
    ) -> None:
        if not self.soundcheck_lock.acquire(blocking=False):
            raise TimeoutError('Soundcheck evidence is busy; retry shortly')
        try:
            self.soundcheck.invalidate(message)
        finally:
            self.soundcheck_lock.release()

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
