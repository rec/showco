from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar, cast
from urllib import parse

from pydantic import ValidationError
from reccy.runtime import logging

from ..streamo.client import StreamoClient
from ..x18.cable_test import (
    TONE_SECONDS,
    CableTester,
    cable_tester_from_specs,
    parse_range,
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
    services,
    setlist,
    soundcheck,
    views,
    workflows,
)
from .lyte import LyteClient
from .mixer import MixersMonitor, MixerSpec
from .monitoring import PerformanceMonitor
from .recs import RecsClient
from .system import SystemMonitor
from .waveforms import WaveformBridge

MAX_ACTION_BYTES = 65_536
MAX_CONCURRENT_REQUESTS = 8
MAX_WAVEFORM_CONNECTIONS = 4
CONNECTION_TIMEOUT_SECONDS = 10
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
        self.action_log_lock = threading.Lock()
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
            return status.model_copy(
                update={
                    'incidents': observed,
                    'active_faults': list(self.incidents.faults),
                    'performance_locked': self.performance_lock.locked,
                    'observed_at': datetime.now().astimezone(),
                    'monitoring_error': self.performance_lock.error
                    or self.incidents.storage_error
                    or self.soundcheck_error
                    or (
                        self.system.observation_error
                        if isinstance(self.system, PerformanceMonitor)
                        else None
                    ),
                    'input_checks': input_check.checks(recs.channels),
                    'music': self.music.status()
                    if self.music is not None
                    else models.MusicStatus(),
                }
            )

    def run_action(self, form: dict[str, str]) -> models.ActionResult:
        with self.action_lock:
            action = form.get('action', '')
            try:
                result = self._dispatch_action(action, form)
            except (
                ConnectionError,
                OSError,
                TimeoutError,
                ValidationError,
                ValueError,
            ) as error:
                result = models.ActionResult(ok=False, message=str(error))
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
        if action == 'acknowledge-fault':
            with self.status_lock:
                self.incidents.acknowledge(
                    form.get('name', ''), form.get('started_at', '')
                )
            return models.ActionResult(
                ok=True, message='Fault acknowledged; it remains active until recovery'
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
        if action == 'recs-calibrate':
            device = form.get('device', '')
            channels = _channel_numbers(form.get('channels', ''))
            if device or channels:
                return self.recs.calibrate(device, channels)
            return self.recs.calibrate()
        if action in {'recs-musician-add', 'recs-musician-edit'}:
            return self._save_musician(action, form)
        if action == 'recs-track-name':
            return self.recs.set_track_name(
                form.get('device', ''),
                form.get('channel', ''),
                form.get('track_name', ''),
            )
        if action == 'recs-set-stereo':
            return self.recs.set_stereo(
                form.get('device', ''), _channel_numbers(form.get('channels', ''))
            )
        if action == 'recs-set-attr':
            try:
                value = json.loads(form.get('value', ''))
            except json.JSONDecodeError:
                return models.ActionResult(
                    ok=False,
                    message='recs attribute value must be valid JSON',
                )
            return self.recs.set_attr(form.get('address', ''), value)
        if action == 'recs-shutdown':
            if form.get('confirmation') == 'shutdown':
                return self.recs.shutdown()
            return models.ActionResult(ok=True, message='recs shutdown canceled')
        if action == 'recs-playback-play':
            return self.recs.play()
        if action in RECS_ACTIONS:
            return self.recs.action(RECS_ACTIONS[action], **_recs_fields(form))
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
            if self.cable_tester is None:
                return models.ActionResult(
                    ok=False, message='X18 cable test is not configured'
                )
            report = self.cable_tester.run(
                parse_range(form.get('channels', ''), 1, 18, 'channels'),
                parse_range(form.get('sends', ''), 1, 6, 'sends'),
                duration_seconds=float(form.get('duration-seconds', str(TONE_SECONDS))),
            )
            return models.ActionResult(ok=report.passed, message=report.message())
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
                case 'music-stop':
                    return self.music.stop()
            return models.ActionResult(
                ok=False, message=f'unknown music action {action}'
            )
        if action in STREAMO_ACTIONS:
            if self.streamo is None:
                return models.ActionResult(ok=False, message='streamo is disabled')
            return self.streamo.action(STREAMO_ACTIONS[action], **_streamo_fields(form))
        return models.ActionResult(ok=False, message=f'unknown action {action}')

    def _save_musician(self, action: str, form: dict[str, str]) -> models.ActionResult:
        nickname = form.get('nickname', '').strip()
        if not nickname:
            return models.ActionResult(
                ok=False, message='Musician nickname is required'
            )
        names = _text_lines(form.get('names', ''))
        copyright_name = form.get('copyright_name', '').strip() or None
        public_keys = (
            _text_lines(form['public_keys']) if 'public_keys' in form else None
        )
        links = _text_lines(form.get('links', ''))
        if action == 'recs-musician-add':
            return self.recs.add_musician(
                nickname, names, copyright_name, public_keys or [], links
            )
        return self.recs.edit_musician(nickname, names, public_keys, links)

    def recent_actions(self) -> list[models.ActionLogEntry]:
        with self.action_log_lock:
            return list(self.action_log)


class FormError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message


class ShowcoHandler(BaseHTTPRequestHandler):
    app: ClassVar[ShowcoApp]

    def do_GET(self) -> None:
        if self.path == '/waveforms':
            self._waveforms()
            return
        if not self._acquire_request():
            return
        try:
            self._do_get()
        finally:
            cast(ShowcoServer, self.server).request_slots.release()

    def _do_get(self) -> None:
        if self.path == '/status':
            self._json(self.app.status())
            return
        if self.path in {'/', '/channels'}:
            self._html(views.channels_page(self.app.status()))
            return
        if self.path == '/musicians':
            self._html(
                views.musicians_page(
                    self.app.recs.musicians(), self.app.recent_actions()
                )
            )
            return
        if self.path == '/performance':
            self._html(views.performance_page())
            return
        if self.path in {'/setlist', '/soundcheck', '/recovery', '/lighting'}:
            self._html(views.workflow_page(self.path[1:]))
            return
        if self.path == '/workflow-status':
            with self.app.action_lock:
                payload = workflows.status(self.app)
            self._json(payload)
            return
        if self.path == '/diagnostics':
            workflows.download(self)
            return
        if self.path == '/health':
            self._html(views.health_page(self.app.status()))
            return
        if self.path == '/playback':
            self._html(views.playback_page(self.app.status().recs.playback))
            return
        if self.path == '/attributes':
            self._html(views.attributes_page(self.app.recs.mutable_attributes()))
            return
        if self.path == '/errors':
            self._html(views.errors_page(self.app.status().recs.errors))
            return
        if self.path == '/actions':
            self._html(
                views.actions_page(
                    self.app.recent_actions(),
                    music=(
                        self.app.music.status() if self.app.music is not None else None
                    ),
                    streamo_enabled=self.app.streamo is not None,
                    lyte_enabled=self.app.lyte is not None and self.app.lyte.enabled,
                )
            )
            return
        self.send_error(404)

    def _waveforms(self) -> None:
        bridge = self.app.waveforms
        if bridge is None:
            self.send_response(204)
            self.end_headers()
            return
        server = cast(ShowcoServer, self.server)
        if not server.waveform_slots.acquire(blocking=False):
            self.send_error(503, 'Too many waveform connections')
            return
        try:
            self.send_response(200)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Connection', 'keep-alive')
            self.end_headers()
            layouts, batches, changed = bridge.snapshot()
            for layout in layouts:
                self._waveform_event('waveform_layout', layout.model_dump())
            for batch in batches:
                self._waveform_event('waveform', batch.model_dump())
            while not bridge.stopped.is_set():
                updated = bridge.wait_for_change(changed, 15)
                if updated == changed:
                    self.wfile.write(b': heartbeat\n\n')
                    self.wfile.flush()
                    continue
                missed, events = bridge.events_since(changed)
                if missed:
                    self._waveform_event('waveform_resync', {})
                    layouts, batches, changed = bridge.snapshot()
                    for layout in layouts:
                        self._waveform_event('waveform_layout', layout.model_dump())
                    for batch in batches:
                        self._waveform_event('waveform', batch.model_dump())
                    continue
                for _, name, event in events:
                    self._waveform_event(name, event.model_dump())
                changed = updated
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        finally:
            server.waveform_slots.release()

    def _waveform_event(self, name: str, data: dict[str, object]) -> None:
        message = f'event: {name}\ndata: {json.dumps(data, separators=(",", ":"))}\n\n'
        self.wfile.write(message.encode())
        self.wfile.flush()

    def do_POST(self) -> None:
        if not self._acquire_request():
            return
        try:
            self._do_post()
        finally:
            cast(ShowcoServer, self.server).request_slots.release()

    def _do_post(self) -> None:
        if self.path not in {'/actions', '/musicians'}:
            self.send_error(404)
            return
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            self.send_error(403, 'cross-site actions are not allowed')
            return
        if (origin := self.headers.get('Origin')) is not None:
            try:
                parsed_origin = parse.urlsplit(origin)
            except ValueError:
                self.send_error(403, 'invalid action origin')
                return
            if parsed_origin.scheme not in {
                'http',
                'https',
            } or parsed_origin.netloc != self.headers.get('Host'):
                self.send_error(403, 'action origin does not match this server')
                return
        try:
            form = self._form()
        except FormError as error:
            self.send_error(error.status, error.message)
            return
        result = self.app.run_action(form)
        self._log_action(form.get('action', ''), result)
        if self.headers.get('Accept') == 'application/json':
            self._json_action(result)
            return
        self.send_response(303)
        self.send_header('Location', self.path)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return

    def _form(self) -> dict[str, str]:
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise FormError(413, 'invalid Content-Length') from None
        if length < 0 or length > MAX_ACTION_BYTES:
            raise FormError(413, f'action body exceeds {MAX_ACTION_BYTES} bytes')
        try:
            body = self.rfile.read(length).decode()
        except UnicodeDecodeError:
            raise FormError(400, 'action body is not valid UTF-8') from None
        try:
            pairs = parse.parse_qsl(body, strict_parsing=True)
        except ValueError:
            raise FormError(400, 'action body is malformed') from None
        return {key: value for key, value in pairs}

    def _acquire_request(self) -> bool:
        if cast(ShowcoServer, self.server).request_slots.acquire(blocking=False):
            return True
        self.send_error(503, 'showCo is busy')
        return False

    def _log_action(self, action: str, result: models.ActionResult) -> None:
        detail = result.message.replace('\n', ' ')[:240]
        log = LOGGER.info if result.ok else LOGGER.error
        log(
            'showco action source=%s action=%r ok=%s detail=%r',
            self.client_address[0],
            action,
            result.ok,
            detail,
        )

    def _html(self, body: str) -> None:
        data = body.encode()
        self.send_response(200)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, value: models.ShowStatus | workflows.WorkflowStatus) -> None:
        data = value.model_dump_json().encode()
        self.send_response(200)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json_action(self, value: models.ActionResult) -> None:
        data = value.model_dump_json().encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class ShowcoServer(ThreadingHTTPServer):
    app: ShowcoApp
    performance: PerformanceMonitor | None
    request_slots: threading.BoundedSemaphore
    waveform_slots: threading.BoundedSemaphore

    def __init__(self, address: tuple[str, int], handler: type[ShowcoHandler]) -> None:
        super().__init__(address, handler)
        self.request_slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
        self.waveform_slots = threading.BoundedSemaphore(MAX_WAVEFORM_CONNECTIONS)
        self.connection_slots = threading.BoundedSemaphore(
            MAX_CONCURRENT_REQUESTS + MAX_WAVEFORM_CONNECTIONS
        )
        self.performance = None
        self.daemon_threads = True

    def process_request(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: tuple[str, int],
    ) -> None:
        cast(socket.socket, request).settimeout(CONNECTION_TIMEOUT_SECONDS)
        if not self.connection_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        started = False
        try:
            super().process_request(request, client_address)
            started = True
        finally:
            if not started:
                self.connection_slots.release()

    def process_request_thread(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: tuple[str, int],
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connection_slots.release()

    def server_close(self) -> None:
        if self.performance is not None:
            self.performance.close()
        if self.app.waveforms is not None:
            self.app.waveforms.close()
        if self.app.music is not None:
            self.app.music.close()
        super().server_close()


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


def make_server(
    host: str,
    port: int,
    *,
    recs: RecsClient | None = None,
    streamo: StreamoClient | None = None,
    system: SystemMonitor | None = None,
    mixers: MixersMonitor | None = None,
    streamo_restart: Callable[[], models.ActionResult] | None = None,
    streamo_enabled: bool = False,
    lyte_enabled: bool = False,
    lyte: LyteClient | None = None,
    performance_enabled: bool = False,
    mixer_specs: list[MixerSpec] | None = None,
) -> ThreadingHTTPServer:
    handler = type('ConfiguredShowcoHandler', (ShowcoHandler,), {})
    recs_client = recs or RecsClient()
    streamo_client = (streamo or StreamoClient()) if streamo_enabled else None
    streamo_restart_action = streamo_restart or (
        lambda: services.restart_service('streamo')
    )
    waveforms = (
        WaveformBridge(control=recs_client.control)
        if type(recs_client) is RecsClient
        else None
    )
    if waveforms is not None:
        waveforms.start()
    system_monitor = system or SystemMonitor()
    performance = (
        PerformanceMonitor(system_monitor, recs_client.snapshot_client.status)
        if performance_enabled
        else None
    )
    app = ShowcoApp(
        recs_client,
        streamo_client,
        performance or system_monitor,
        mixers or MixersMonitor([]),
        streamo_restart_action if streamo_enabled else None,
        waveforms,
        lyte if lyte is not None else LyteClient(enabled=lyte_enabled),
        (
            cable_tester_from_specs(recs_client, mixer_specs)
            if mixer_specs and any(m.name == 'X18' for m in mixer_specs)
            else None
        ),
        music.controller_from_specs(
            recs_client,
            mixer_specs or [],
            streamo_client,
            streamo_restart_action if streamo_enabled else None,
        ),
        state_directory=Path.home() / '.local/state/showco'
        if performance_enabled
        else None,
    )
    handler.app = app
    server = ShowcoServer((host, port), handler)
    server.app = app
    server.performance = performance
    if performance is not None:
        performance.observe = app.status
        performance.start()
    return server


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


def _channel_numbers(value: str) -> list[int]:
    try:
        return [int(number) for number in value.split(',') if number]
    except ValueError:
        return []


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


def _recs_fields(form: dict[str, str]) -> dict[str, object]:
    fields: dict[str, object] = {}
    for k, v in form.items():
        if k == 'action' or not v:
            continue
        if k in {'noise_floor', 'seconds'}:
            try:
                fields[k] = float(v)
            except ValueError:
                raise ValueError(f'{k} must be a number') from None
        elif k in {'offset', 'session'}:
            try:
                fields[k] = int(v)
            except ValueError:
                raise ValueError(f'{k} must be an integer') from None
        else:
            fields[k] = v
    return fields


def _text_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


RECS_ACTIONS = {
    'recs-capabilities': 'capabilities',
    'recs-disk-status': 'disk_status',
    'recs-key-label': 'set_key_label',
    'recs-list-devices': 'list_devices',
    'recs-marker': 'mark',
    'recs-new-session': 'new_session',
    'recs-pause-recording': 'pause_recording',
    'recs-playback-jump': 'jump_playback',
    'recs-playback-jump-session': 'jump_session',
    'recs-playback-pause': 'pause_playback',
    'recs-playback-stop': 'stop_playback',
    'recs-reload-profiles': 'reload_profiles',
    'recs-resume-recording': 'resume_recording',
    'recs-set-noise-floor': 'set_noise_floor',
    'recs-status-snapshot': 'status_snapshot',
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
