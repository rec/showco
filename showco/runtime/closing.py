from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import sounddevice
from pydantic import BaseModel, ValidationError
from reccy.runtime import logging
from reccy.runtime.files import atomic_output

from ..streamo.client import StreamoClient
from ..x18.music import MusicPlayer, RoomScene, X18MusicRouting
from . import models
from .recs import RecsClient

LOGGER = logging.get_logger(__name__)


class ClosingRecord(BaseModel, frozen=True):
    operation_id: str
    phase: str
    room_scene: RoomScene
    error: str | None = None


class ClosingSnapshot(BaseModel, frozen=True):
    record: ClosingRecord | None = None
    remote: models.ClosingStatus | None = None
    error: str | None = None
    observing: bool = False


class ClosingController:
    """Own closing outputs until stopped or finalized, publishing without I/O locks.

    Application actions serialize requests. Normal transitions must stop this
    observer before touching outputs. The publication lock never spans I/O or a
    join; the observer is the only worker and never acquires application locks.
    """

    def __init__(
        self,
        recs: RecsClient,
        player: MusicPlayer,
        routing: X18MusicRouting,
        streamo: StreamoClient | None,
        fade_seconds: float,
        teardown_audio: list[Path],
        path: Path | None,
        save_phase: Callable[[str], None],
    ) -> None:
        self.recs = recs
        self.player = player
        self.routing = routing
        self.streamo = streamo
        self.fade_seconds = fade_seconds
        self.teardown_audio = teardown_audio
        self.path = path
        self.save_phase = save_phase
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot = ClosingSnapshot()
        self._black_fade_done = False
        self._load()

    def status(self) -> ClosingSnapshot:
        with self._lock:
            return self._snapshot.model_copy(
                update={
                    'observing': self._thread is not None and self._thread.is_alive()
                }
            )

    def observe_existing(self) -> None:
        record = self.status().record
        if record is not None and record.phase not in {
            'teardown-music-playing',
            'failed',
        }:
            self._start_observer()

    def request(self) -> models.ActionResult:
        assert self.streamo is not None
        previous = self.status().record
        if previous is not None and previous.phase != 'teardown-music-playing':
            return models.ActionResult(
                ok=False,
                message='A closing operation already exists; review its status',
            )
        # A terminal record can be published before the phase-save callback exits.
        if not self.stop(timeout=5):
            return models.ActionResult(
                ok=False, message='Closing observer is still active'
            )
        scene = (
            previous.room_scene if previous is not None else self.routing.capture_room()
        )
        record = ClosingRecord(
            operation_id=str(uuid.uuid4()), phase='credits-requesting', room_scene=scene
        )
        self._save(record)
        self._publish(remote=None, error=None)
        self._black_fade_done = False
        try:
            self.streamo.start_closing(record.operation_id)
        except (ConnectionError, OSError, TimeoutError, ValueError) as error:
            message = (
                f'Closing request outcome is uncertain: {error}. '
                'Recording and room mix are unchanged; check the closing status.'
            )
            self._publish(error=message)
            self._start_observer()
            return models.ActionResult(ok=False, message=message)
        try:
            self._save(record.model_copy(update={'phase': 'credits-running'}))
        except OSError as error:
            message = f'Closing started but local progress could not be saved: {error}'
            self._publish(error=message)
            self._start_observer()
            return models.ActionResult(ok=False, message=message)
        self._start_observer()
        return models.ActionResult(
            ok=True, message='Closing credits started; recs continues recording'
        )

    def require_idle(self) -> None:
        record = self.status().record
        if record is not None and record.phase != 'teardown-music-playing':
            raise ValueError('Resolve the closing operation before changing music mode')
        if not self.stop(timeout=5):
            raise ValueError('Closing observer is still active')

    def prepare_recovery(self) -> models.ActionResult | None:
        record = self.status().record
        if record is None or record.phase == 'teardown-music-playing':
            return models.ActionResult(
                ok=False, message='No closing operation needs recovery'
            )
        if self.streamo is not None:
            remote = self.streamo.status().closing
            if remote is not None and remote.operation_id == record.operation_id:
                self._publish(remote=remote)
            if (
                remote is not None
                and remote.operation_id == record.operation_id
                and remote.state == 'running'
            ):
                return models.ActionResult(
                    ok=False, message='Closing credits are still running'
                )
        if not self.stop(timeout=5):
            return models.ActionResult(
                ok=False, message='Closing observer is still active'
            )
        return None

    def finish(self) -> models.ActionResult:
        if (rejected := self.prepare_recovery()) is not None:
            return rejected
        try:
            return self._finish()
        except (OSError, TimeoutError, ValueError, sounddevice.PortAudioError) as error:
            self._fail(str(error))
            return models.ActionResult(ok=False, message=str(error))

    def clear(self) -> None:
        # Preserve recovery evidence if deletion fails after a successful transition.
        if self.path is not None:
            self.path.unlink(missing_ok=True)
        with self._lock:
            self._snapshot = ClosingSnapshot()
        self._black_fade_done = False

    def stop(self, timeout: float | None = None) -> bool:
        self._stop.set()
        thread = self._thread
        if thread is not None and threading.current_thread() is not thread:
            thread.join(timeout=timeout)
            return not thread.is_alive()
        return True

    def _publish(self, **values: object) -> None:
        with self._lock:
            self._snapshot = self._snapshot.model_copy(update=values)

    def _save(self, record: ClosingRecord) -> None:
        if self.path is not None:
            with atomic_output(self.path) as temporary:
                temporary.write_text(record.model_dump_json())
        self._publish(record=record)

    def _load(self) -> None:
        if self.path is None:
            return
        try:
            record = ClosingRecord.model_validate_json(self.path.read_text())
        except FileNotFoundError:
            return
        except (OSError, ValidationError) as error:
            self._publish(error=f'Closing state cannot be read: {error}')
            return
        self._publish(record=record)
        if record.phase == 'finalizing':
            message = (
                'showCo restarted during finalization; inspect recs and mixer state'
            )
            record = record.model_copy(update={'phase': 'failed', 'error': message})
            try:
                self._save(record)
            except OSError as error:
                message += f'; closing failure could not be saved: {error}'
                record = record.model_copy(update={'error': message})
                LOGGER.error('Closing failed: %s', message)
        self._publish(
            record=record,
            error=record.error,
        )

    def _start_observer(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._observe, name='showco-closing-credits', daemon=True
        )
        self._thread.start()

    def _observe(self) -> None:
        while not self._stop.wait(0.1):
            record = self.status().record
            if record is None:
                return
            assert self.streamo is not None
            remote = self.streamo.status().closing
            if remote is None or remote.operation_id != record.operation_id:
                self._publish(
                    error='Closing status is unavailable or has a different '
                    'operation ID; recording and room music are unchanged'
                )
                continue
            self._publish(remote=remote)
            try:
                if remote.state == 'failed':
                    self._fail(remote.error or 'streamO closing failed')
                    return
                if (
                    remote.state == 'running'
                    and remote.black_started_at is not None
                    and not self._black_fade_done
                ):
                    self._save(record.model_copy(update={'phase': 'black-interval'}))
                    remaining = max(
                        0.0,
                        min(2.0, (remote.black_at or time.time()) + 2 - time.time()),
                    )
                    self.routing.fade_main(0.0, remaining)
                    self._black_fade_done = True
                if remote.state == 'completed':
                    if not self._black_fade_done:
                        LOGGER.warning(
                            'Missed the closing black interval; broadcast is stopped. '
                            'Silencing the room mix before completing teardown.'
                        )
                    self._finish()
                    return
            except (
                OSError,
                TimeoutError,
                ValueError,
                sounddevice.PortAudioError,
            ) as error:
                self._fail(str(error))
                return

    def _finish(self) -> models.ActionResult:
        record = self.status().record
        if record is None:
            return models.ActionResult(ok=False, message='No closing operation exists')
        self._save(record.model_copy(update={'phase': 'finalizing'}))
        self.routing.fade_main(0.0, 0)
        paused = self.recs.pause_recording()
        if isinstance(paused, models.ActionResult) and not paused.ok:
            raise ValueError(paused.message)
        result = self.recs.action('stop_playback')
        if not result.ok:
            raise ValueError(result.message)
        self.routing.isolate_instruments(record.room_scene)
        if self.teardown_audio:
            self.player.start(self.teardown_audio, 0)
            self.routing.enable()
            self.routing.fade_main(record.room_scene.master_fader, self.fade_seconds)
        else:
            self.player.stop(0)
            self.routing.disable()
        self._save(
            record.model_copy(update={'phase': 'teardown-music-playing', 'error': None})
        )
        self.save_phase('teardown')
        self._publish(error=None)
        return models.ActionResult(
            ok=True,
            message='Broadcast stopped; recs paused; teardown music is playing'
            if self.teardown_audio
            else 'Broadcast stopped; recs paused; no teardown audio configured',
        )

    def _fail(self, message: str) -> None:
        record = self.status().record
        if record is None:
            return
        for name, action in [
            ('stop music player', lambda: self.player.stop(0)),
            ('mute music returns', self.routing.disable),
            *(
                [
                    (
                        'restore room mix',
                        lambda: self.routing.restore_room(record.room_scene),
                    )
                ]
                if record.phase in {'black-interval', 'finalizing'}
                else []
            ),
        ]:
            try:
                action()
            except (
                OSError,
                TimeoutError,
                ValueError,
                sounddevice.PortAudioError,
            ) as error:
                message += f'; {name} failed: {error}'
        failed = record.model_copy(update={'phase': 'failed', 'error': message})
        try:
            self._save(failed)
        except OSError as error:
            message += f'; closing failure could not be saved: {error}'
            failed = failed.model_copy(update={'error': message})
        # Keep the remote completion/failure evidence intact on local failure.
        self._publish(record=failed, error=message)
        LOGGER.error('Closing failed: %s', message)
