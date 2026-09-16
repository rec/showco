from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError
from reccy.protocol import rpc
from reccy.runtime import logging
from recs.base.waveform import WaveformBatchData, WaveformLayoutData
from recs.daemon import paths

from . import recs_control, recs_snapshot

MAX_WAVEFORM_BATCHES = 80
MAX_WAVEFORM_EVENTS = 400
WAVEFORM_RECONNECT_SECONDS = 1.0
WAVEFORM_FAILURE_LOG_SECONDS = 60.0
WAVEFORM_CLOSE_SECONDS = 1.0
LOGGER = logging.get_logger(__name__)


class WaveformBridge:
    def __init__(
        self,
        *,
        control_endpoint: Path | str | None = None,
        event_endpoint: Path | str | None = None,
        event_client: (
            Callable[[Callable[[rpc.Event], None]], rpc.EventClient] | None
        ) = None,
        control: recs_control.RecsControlClient | None = None,
    ) -> None:
        self.control_endpoint = control_endpoint or paths.external_control_endpoint()
        self.event_endpoint = event_endpoint or paths.external_event_endpoint()
        self.event_client = event_client or self._event_client
        self.control = control or recs_control.RecsControlClient(self.control_endpoint)
        self.layouts: dict[str, WaveformLayoutData] = {}
        self.batches: dict[str, deque[WaveformBatchData]] = {}
        self.events: deque[tuple[int, str, WaveformLayoutData | WaveformBatchData]] = (
            deque(maxlen=MAX_WAVEFORM_EVENTS)
        )
        self.condition = threading.Condition()
        self.changed = 0
        self.stopped = threading.Event()
        self.reconnect = threading.Event()
        self.thread: threading.Thread | None = None
        self.last_failure_log_time = 0.0

    def start(self) -> None:
        if self.thread is not None:
            return
        self.thread = threading.Thread(
            target=self._run, daemon=True, name='ShowcoWaveforms'
        )
        self.thread.start()

    def close(self) -> None:
        try:
            self.control.call('unsubscribe_waveforms', timeout=WAVEFORM_CLOSE_SECONDS)
        except (ConnectionError, OSError, TimeoutError, ValidationError, ValueError):
            pass
        self.stopped.set()
        self.reconnect.set()
        with self.condition:
            self.condition.notify_all()
        if self.thread is not None:
            self.thread.join(WAVEFORM_CLOSE_SECONDS)

    def snapshot(self) -> tuple[list[WaveformLayoutData], list[WaveformBatchData], int]:
        with self.condition:
            return (
                list(self.layouts.values()),
                [b for batches in self.batches.values() for b in batches],
                self.changed,
            )

    def wait_for_change(self, changed: int, timeout: float) -> int:
        with self.condition:
            if self.changed == changed:
                self.condition.wait(timeout)
            return self.changed

    def events_since(
        self, changed: int
    ) -> tuple[bool, list[tuple[int, str, WaveformLayoutData | WaveformBatchData]]]:
        with self.condition:
            if self.events and changed < self.events[0][0] - 1:
                return True, []
            return False, [event for event in self.events if event[0] > changed]

    def receive(self, event: rpc.Event) -> None:
        try:
            if event.name == 'waveform_layout':
                self._layout(WaveformLayoutData.model_validate(event.data))
            elif event.name == 'waveform':
                self._batch(WaveformBatchData.model_validate(event.data))
            elif event.name in {'shutdown', 'stopped'}:
                self.reconnect.set()
        except ValidationError as error:
            LOGGER.error('recs sent invalid waveform event: %s', error)
            self.reconnect.set()

    def _run(self) -> None:
        while not self.stopped.is_set():
            events: rpc.EventClient | None = None
            try:
                self.reconnect.clear()
                events = self.event_client(self.receive)
                events.start()
                result = self.control.call('subscribe_waveforms')
                if (
                    not recs_snapshot.object_dict(result)
                    or result.get('type') != 'waveform_subscription'
                    or result.get('active') is not True
                ):
                    raise ConnectionError('recs did not activate waveforms')
                self.reconnect.wait()
            except (
                ConnectionError,
                OSError,
                TimeoutError,
                ValidationError,
                ValueError,
            ) as error:
                if (
                    time.monotonic() - self.last_failure_log_time
                    >= WAVEFORM_FAILURE_LOG_SECONDS
                ):
                    LOGGER.warning('recs waveform subscription failed: %s', error)
                    self.last_failure_log_time = time.monotonic()
                self.stopped.wait(WAVEFORM_RECONNECT_SECONDS)
            finally:
                if events is not None:
                    events.close()
            if self.reconnect.is_set() and not self.stopped.is_set():
                self.stopped.wait(WAVEFORM_RECONNECT_SECONDS)

    def _event_client(self, receive: Callable[[rpc.Event], None]) -> rpc.EventClient:
        return rpc.EventClient(self.event_endpoint, receive, role='showco')

    def _layout(self, layout: WaveformLayoutData) -> None:
        with self.condition:
            self.layouts[layout.source] = layout
            self.batches.pop(layout.source, None)
            self._record('waveform_layout', layout)

    def _batch(self, batch: WaveformBatchData) -> None:
        with self.condition:
            layout = self.layouts.get(batch.source)
            if layout is None or layout.generation != batch.generation:
                return
            batches = self.batches.setdefault(
                batch.source, deque(maxlen=MAX_WAVEFORM_BATCHES)
            )
            batches.append(batch)
            self._record('waveform', batch)

    def _record(self, name: str, data: WaveformLayoutData | WaveformBatchData) -> None:
        self.changed += 1
        self.events.append((self.changed, name, data))
        self.condition.notify_all()
