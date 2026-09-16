from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from . import models


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
                history = IncidentHistory.model_validate_json(path.read_text())
                self.previous = history.previous
                self.incidents = history.incidents[:100]
                self.faults = history.faults[:100]
            except FileNotFoundError:
                pass
            except (OSError, ValidationError) as error:
                self.load_error = f'Cannot read incident history: {error}'
                self.storage_error = self.load_error

    def observe(self, status: models.ShowStatus) -> list[models.Incident]:
        current = states(status)
        now = datetime.now().astimezone()
        previous_faults = {f.name: f for f in self.faults}
        faults = fault_details(status)
        self.faults = [
            models.ActiveFault(
                name=n,
                started_at=previous_faults[n].started_at
                if n in previous_faults
                else now,
                message=m,
                next_action=a,
                acknowledged=previous_faults[n].acknowledged
                if n in previous_faults
                else False,
            )
            for n, m, a in faults[:100]
        ]
        for fault in self.faults:
            if fault.name not in previous_faults:
                self.incidents.insert(
                    0,
                    models.Incident(
                        timestamp=now, message=f'{fault.name}: {fault.message}'
                    ),
                )
        for name in previous_faults.keys() - {f.name for f in self.faults}:
            self.incidents.insert(
                0, models.Incident(timestamp=now, message=f'{name}: recovered')
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
        if self.pending_save:
            try:
                self.save()
            except OSError as error:
                self.storage_error = f'Cannot save incident history: {error}'
        return list(self.incidents)

    def acknowledge(self, name: str, started_at: str) -> None:
        started = datetime.fromisoformat(started_at)
        for index, fault in enumerate(self.faults):
            if fault.name == name and fault.started_at == started:
                self.faults[index] = fault.model_copy(update={'acknowledged': True})
                self.pending_save = True
                try:
                    self.save()
                except OSError:
                    self.faults[index] = fault
                    raise
                return
        raise ValueError('This fault is no longer current; refresh status')

    def save(self) -> None:
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(
                IncidentHistory(
                    previous=self.previous, incidents=self.incidents, faults=self.faults
                ).model_dump_json()
            )
            temporary.replace(self.path)
        self.pending_save = False
        self.storage_error = self.load_error


def fault_details(status: models.ShowStatus) -> list[tuple[str, str, str]]:
    faults = [
        (c.name, c.message, 'Open Health to inspect the service and recording disk.')
        for c in status.readiness.checks
        if not c.ok
    ]
    if (
        status.recs.recording
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
    return faults


def states(status: models.ShowStatus) -> dict[str, str]:
    disk = status.recs.disk
    recording = (
        'paused'
        if status.recs.paused
        else 'recording'
        if status.recs.recording
        else 'stopped'
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
