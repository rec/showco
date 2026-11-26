from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field
from reccy.configuration import settings
from reccy.runtime import logging

from . import models

LOGGER = logging.get_logger(__name__)
RECS_FAULT_MESSAGES = 8


class IncidentHistory(BaseModel, frozen=True):
    previous: dict[str, str] | None = None
    incidents: list[models.Incident] = Field(default_factory=list)
    faults: list[models.ActiveFault] = Field(default_factory=list)


class IncidentTimeline:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.storage_error: str | None = None
        self.load_error: str | None = None
        self.previous: dict[str, str] | None = None
        self.incidents: list[models.Incident] = []
        self.faults: list[models.ActiveFault] = []
        self.pending_save = False
        if path is not None:
            try:
                history = IncidentHistory.model_validate(settings.read(path))
                self.previous = history.previous
                self.incidents = history.incidents[:100]
                self.faults = history.faults[:100]
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as error:
                self.load_error = f'Cannot read incident history: {error}'
                self.storage_error = self.load_error

    def observe(
        self, status: models.ShowStatus, *, persist: bool = True
    ) -> list[models.Incident]:
        current = states(status)
        now = datetime.now().astimezone()
        previous_faults = {f.name: f for f in self.faults}
        faults = fault_details(status)
        self.faults = []
        for name, message, next_action in faults[:100]:
            previous = previous_faults.get(name)
            if previous is not None and previous.message == message:
                fault = previous.model_copy(update={'next_action': next_action})
            else:
                fault = models.ActiveFault(
                    name=name, started_at=now, message=message, next_action=next_action
                )
                LOGGER.error('%s: %s', name, message)
                self.incidents.insert(
                    0, models.Incident(timestamp=now, message=f'{name}: {message}')
                )
            self.faults.append(fault)
        for name in previous_faults.keys() - {f.name for f in self.faults}:
            message = (
                'no errors in the current session'
                if name == 'recs recording errors'
                else 'recovered'
            )
            LOGGER.info('%s: %s', name, message)
            self.incidents.insert(
                0, models.Incident(timestamp=now, message=f'{name}: {message}')
            )
        if self.previous is not None:
            for name, value in current.items():
                if name in previous_faults or any(f.name == name for f in self.faults):
                    continue
                if self.previous.get(name) != value:
                    previous = self.previous.get(name, 'unavailable')
                    self.incidents.insert(
                        0,
                        models.Incident(
                            timestamp=now,
                            message=f'{name}: {previous} to {value}',
                        ),
                    )
        self.incidents = self.incidents[:100]
        self.pending_save |= (
            self.previous != current or list(previous_faults.values()) != self.faults
        )
        self.previous = current
        if persist and self.pending_save:
            try:
                self.save()
            except OSError as error:
                self.storage_error = f'Cannot save incident history: {error}'
        return list(self.incidents)

    def acknowledge(self, name: str, started_at: str, *, persist: bool = True) -> None:
        started = datetime.fromisoformat(started_at)
        for index, fault in enumerate(self.faults):
            if fault.name == name and fault.started_at == started:
                self.faults[index] = fault.model_copy(update={'acknowledged': True})
                self.pending_save = True
                try:
                    if persist:
                        self.save()
                except OSError:
                    self.faults[index] = fault
                    raise
                return
        raise ValueError('This fault is no longer current; refresh status')

    def record(self, message: str, *, persist: bool = True) -> None:
        self.incidents = [
            models.Incident(timestamp=datetime.now().astimezone(), message=message),
            *self.incidents[:99],
        ]
        self.pending_save = True
        if persist:
            self.save()

    def history(self) -> IncidentHistory:
        return IncidentHistory(
            previous=self.previous,
            incidents=list(self.incidents),
            faults=list(self.faults),
        )

    def write(self, history: IncidentHistory) -> None:
        if self.path is not None:
            settings.write_json_model(self.path, history, write_empty_dicts=True)

    def save(self) -> None:
        self.write(self.history())
        self.pending_save = False
        self.storage_error = self.load_error


def fault_details(status: models.ShowStatus) -> list[tuple[str, str, str]]:
    faults = [
        (
            c.name,
            c.message,
            'Check the external Wi-Fi network and saved connection profile.'
            if c.name == 'External Wi-Fi'
            else 'Open Health to inspect the service and recording disk.',
        )
        for c in status.readiness.checks
        if not c.ok
    ]
    if status.recs.errors:
        messages = list(dict.fromkeys(e.message for e in reversed(status.recs.errors)))
        detail = '. '.join(messages[:RECS_FAULT_MESSAGES])
        if len(messages) > RECS_FAULT_MESSAGES:
            detail += f'. {len(messages) - RECS_FAULT_MESSAGES} more on Health'
        faults.insert(
            0,
            (
                'recs recording errors',
                detail,
                'Check the affected inputs and storage. Open Health for details.',
            ),
        )
    if (
        status.recs.service.state == 'connected'
        and status.recs.snapshot_available
        and not status.recs.paused
        and not status.recording_progress.ok
    ):
        faults.append(
            (
                'Audio writes',
                status.recording_progress.message
                + '; silence filtering may pause file growth',
                'Check input signal and recording progress on Health; '
                'do not restart solely because files stopped growing.',
            )
        )
    music = status.music
    if music.closing_warning:
        faults.append(
            (
                'Closing state',
                music.closing_warning,
                'Check storage before restarting showCo.',
            )
        )
    if (
        music.mode == 'fault'
        or music.state == 'failed'
        or (music.closing is not None and music.closing.state == 'failed')
    ):
        faults.append(
            (
                'Music',
                music.error
                or (music.closing.error if music.closing else None)
                or 'Music or closing sequence failed',
                'Open Actions to inspect music and explicitly recover '
                'the show transition.',
            )
        )
    return faults


def states(status: models.ShowStatus) -> dict[str, str]:
    disk = status.recs.disk
    recording = (
        'unknown'
        if status.recs.service.state != 'connected'
        or not status.recs.snapshot_available
        else 'paused'
        if status.recs.paused
        else 'enabled'
    )
    disk_state = (
        'unavailable'
        if disk is None
        else 'paused'
        if disk.paused_for_disk_space
        else 'alert'
        if disk.alert_active
        else 'ready'
    )
    return {
        'recs': status.recs.service.state,
        'Recording': recording,
        'Recording disk': disk_state,
        'lyte': status.lyte.service.state,
        'streamO': status.streamo.service.state,
        **{mixer.name: mixer.state for mixer in status.mixers},
    }
