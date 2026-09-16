import hashlib
import json
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from . import models


class CheckResult(BaseModel, frozen=True):
    state: str
    evidence: str
    checked_at: datetime


class SoundcheckState(BaseModel, frozen=True):
    scope: str = ''
    destination: str = ''
    expected_inputs: list[str] = Field(default_factory=list)
    results: dict[str, CheckResult] = Field(default_factory=dict)
    recording_baseline: float | None = None
    recording_started: datetime | None = None


class Soundcheck:
    def __init__(
        self, path: Path | None = None, identity: Callable[[str], str] | None = None
    ) -> None:
        self.path = path
        self.identity = identity or mount_identity
        self.state = SoundcheckState()
        self.pending_save = False
        if path is not None:
            try:
                self.state = SoundcheckState.model_validate_json(path.read_text())
            except FileNotFoundError:
                pass

    def save(self, state: SoundcheckState) -> None:
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(state.model_dump_json())
            temporary.replace(self.path)
        self.state = state
        self.pending_save = False

    def observe(
        self, status: models.ShowStatus, configuration: str, now: datetime | None = None
    ) -> None:
        now = now or datetime.now().astimezone()
        disk = status.recs.disk
        scope = hashlib.sha256(
            json.dumps(
                [
                    now.date().isoformat(),
                    configuration,
                    self.identity(disk.path) if disk is not None else '',
                    disk.path if disk is not None else '',
                    [
                        [c.device, c.channels, c.name, c.on]
                        for c in status.recs.channels
                    ],
                    status.recs.service.state,
                    status.lyte.service.state,
                    status.streamo.service.state,
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        if self.state.scope and self.state.scope != scope:
            self.invalidate(
                'Date, device, service, or configuration changed; repeat checks'
            )
        if self.state.scope != scope:
            self.save(
                self.state.model_copy(
                    update={'scope': scope, 'destination': disk.path if disk else ''}
                )
            )
        elif self.pending_save:
            self.save(self.state)

    def invalidate(self, reason: str) -> None:
        invalid = self.state.model_copy(
            update={
                'results': {
                    k: v.model_copy(update={'state': 'invalid', 'evidence': reason})
                    for k, v in self.state.results.items()
                },
                'recording_baseline': None,
                'recording_started': None,
            }
        )
        try:
            self.save(invalid)
        except OSError:
            self.state = invalid
            self.pending_save = True
            raise

    def begin(self, expected: list[str]) -> None:
        if not expected:
            raise ValueError('Select the expected inputs before beginning soundcheck')
        self.save(
            self.state.model_copy(
                update={
                    'expected_inputs': expected,
                    'results': {},
                    'recording_baseline': None,
                    'recording_started': None,
                }
            )
        )

    def record_start(self, status: models.ShowStatus) -> None:
        self.save(
            self.state.model_copy(
                update={
                    'recording_baseline': status.recs.recorded_seconds,
                    'recording_started': datetime.now().astimezone(),
                    'results': {
                        k: v
                        for k, v in self.state.results.items()
                        if k not in {'recording', 'playback'}
                    },
                }
            )
        )

    def check(
        self,
        step: str,
        status: models.ShowStatus,
        confirmation: bool = False,
        skip: bool = False,
        note: str = '',
    ) -> models.ActionResult:
        if step not in CHECKS:
            raise ValueError('Unknown soundcheck step')
        if not self.state.expected_inputs:
            raise ValueError('Begin soundcheck with the expected inputs first')
        passed = False
        evidence = ''
        if skip:
            if not note.strip():
                raise ValueError('Give a reason for skipping this check')
            evidence = 'Skipped: ' + note.strip()[:500]
        elif step == 'disk':
            disk = status.recs.disk
            passed = bool(
                confirmation
                and status.recs.service.fresh
                and disk
                and self.identity(disk.path)
                and disk.free_bytes > 0
                and not disk.alert_active
                and not disk.paused_for_disk_space
            )
            evidence = (
                f'Operator confirmed disk {disk.path}; free bytes {disk.free_bytes}'
                if passed and disk
                else 'Confirm the intended disk and capacity; '
                'destination or mount identity may be unavailable'
            )
        elif step == 'inputs':
            channels = {input_key(c): c for c in status.recs.channels}
            failed = [
                k
                for k in self.state.expected_inputs
                if k not in channels
                or not channels[k].on
                or channels[k].state not in {'present', 'healthy'}
            ]
            passed = status.recs.service.fresh and not failed
            evidence = (
                'Measured signal without clipping on all expected recording inputs'
                if passed
                else f'Check signal, recording, or connection: {", ".join(failed)}'
            )
        elif step == 'recording':
            baseline, current = (
                self.state.recording_baseline,
                status.recs.recorded_seconds,
            )
            passed = bool(
                status.recs.service.fresh
                and baseline is not None
                and current is not None
                and current >= baseline + 1
            )
            evidence = (
                f'Reported recorded audio: {baseline} to {current} seconds; '
                'this does not prove playback'
            )
        elif step == 'playback':
            recording = self.state.results.get('recording')
            passed = bool(
                confirmation
                and recording
                and recording.state == 'passed'
                and note.strip()
            )
            evidence = (
                'Operator heard the just-recorded sample: ' + note.strip()[:500]
                if passed
                else 'Check sample writes, select that sample on Playback, '
                'then confirm what you heard'
            )
        elif step == 'lights':
            passed = confirmation and status.lyte.service.fresh
            evidence = (
                'Operator saw the expected light-test output'
                if passed
                else 'Run Test lights explicitly and inspect output; '
                'disabled lighting may be skipped'
            )
        elif step == 'stream':
            passed = (
                confirmation
                and status.streamo.service.fresh
                and status.streamo.ffmpeg_alive
            )
            evidence = (
                'Operator confirmed receiving the live stream externally'
                if passed
                else 'Confirm the stream externally; connection alone is insufficient'
            )
        result = CheckResult(
            state='skipped' if skip else 'passed' if passed else 'failed',
            evidence=evidence,
            checked_at=datetime.now().astimezone(),
        )
        self.save(
            self.state.model_copy(
                update={'results': self.state.results | {step: result}}
            )
        )
        return models.ActionResult(ok=passed or skip, message=evidence)


def input_key(channel: models.ChannelLevel) -> str:
    return f'{channel.device}: {",".join(str(n) for n in channel.channels)}'


def mount_identity(path: str) -> str:
    try:
        target = Path(path).resolve(strict=True)
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        mounts = Path('/proc/self/mountinfo').read_text().splitlines()
    except OSError:
        return ''
    matches = []
    for line in mounts:
        fields = line.split()
        mount = Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4]))
        if target.is_relative_to(mount):
            matches.append((len(mount.parts), fields[0]))
    return f'{boot}:{max(matches)[1]}' if matches else ''


CHECKS = ['disk', 'inputs', 'recording', 'playback', 'lights', 'stream']
