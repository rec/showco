from __future__ import annotations

import json
import socket
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar, cast
from urllib import parse

from reccy.runtime import logging

from ..streamo.client import StreamoClient
from ..x18.cable_test import (
    cable_tester_from_specs,
)
from . import (
    gui_schema,
    models,
    music,
    services,
    views,
    workflows,
)
from .app import ShowcoApp
from .lyte import LyteClient
from .mixer import MixersMonitor, MixerSpec
from .monitoring import PerformanceMonitor
from .recs import RecsClient
from .system import SystemMonitor
from .waveforms import WaveformBridge

MAX_ACTION_BYTES = 65_536
MAX_CONCURRENT_REQUESTS = 8
MAX_WAVEFORM_CONNECTIONS = 4
MAX_STATUS_REQUESTS = 2
CONNECTION_TIMEOUT_SECONDS = 10
ACTION_BODY_TIMEOUT_SECONDS = 2
LOGGER = logging.get_logger(__name__)


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
        slots = (
            cast(ShowcoServer, self.server).status_slots
            if self.path in {'/status', '/workflow-status'}
            else cast(ShowcoServer, self.server).request_slots
        )
        if not slots.acquire(blocking=False):
            self.send_error(503, 'showCo is busy')
            return
        try:
            self._do_get()
        finally:
            slots.release()

    def _do_get(self) -> None:
        if self.path == '/status':
            self._json(self.app.status())
            return
        if self.path == '/workflow-status':
            with self.app.action_lock:
                payload = workflows.status(self.app)
            self._json(payload)
            return
        if self.path == '/diagnostics':
            workflows.download(self)
            return
        document = gui_schema.current_gui()
        default_path = f'/{document.page(document.default_page).name}'
        path = default_path if self.path == '/' else self.path
        page = document.page_at(path)
        if page is None:
            self.send_error(404)
            return
        self._html(
            views.configured_page(
                page,
                self.app.status(),
                mutable_attributes=(
                    self.app.recs.mutable_attributes()
                    if page.uses_source('recs.mutable_attributes')
                    else None
                ),
                musicians=(
                    self.app.recs.musicians()
                    if page.uses_source('recs.musicians')
                    else None
                ),
                action_log=(
                    self.app.recent_actions()
                    if page.uses_source('show.actions')
                    else None
                ),
                features={
                    feature
                    for feature, enabled in {
                        'streamo_enabled': self.app.streamo is not None,
                        'lyte_enabled': self.app.lyte is not None
                        and self.app.lyte.enabled,
                        'music_enabled': self.app.music is not None,
                    }.items()
                    if enabled
                },
            )
        )

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
            self.connection.settimeout(ACTION_BODY_TIMEOUT_SECONDS)
            try:
                data = self.rfile.read(length)
            finally:
                self.connection.settimeout(CONNECTION_TIMEOUT_SECONDS)
        except TimeoutError:
            raise FormError(408, 'action body timed out') from None
        if len(data) != length:
            raise FormError(400, 'action body is incomplete')
        try:
            body = data.decode()
        except UnicodeDecodeError:
            raise FormError(400, 'action body is not valid UTF-8') from None
        try:
            pairs = parse.parse_qsl(body, strict_parsing=True, keep_blank_values=True)
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
    status_slots: threading.BoundedSemaphore

    def __init__(self, address: tuple[str, int], handler: type[ShowcoHandler]) -> None:
        super().__init__(address, handler)
        self.request_slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
        self.waveform_slots = threading.BoundedSemaphore(MAX_WAVEFORM_CONNECTIONS)
        self.status_slots = threading.BoundedSemaphore(MAX_STATUS_REQUESTS)
        self.connection_slots = threading.BoundedSemaphore(
            MAX_CONCURRENT_REQUESTS + MAX_WAVEFORM_CONNECTIONS + MAX_STATUS_REQUESTS
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
        try:
            if self.performance is not None:
                self.performance.close()
        finally:
            try:
                if self.app.waveforms is not None:
                    self.app.waveforms.close()
            finally:
                try:
                    if self.app.music is not None:
                        self.app.music.close()
                finally:
                    super().server_close()


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
    setup: list[Path] | None = None,
    teardown: list[Path] | None = None,
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
            setup=setup,
            teardown=teardown,
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
