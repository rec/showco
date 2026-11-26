import hashlib
import json
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field
from reccy.configuration import settings

from . import models, workflow_storage


class CheckResult(BaseModel, frozen=True):
    state: str
    evidence: str
    checked_at: datetime


class SoundcheckState(BaseModel, frozen=True):
    scope: str = ''
    destination: str = ''
    expected_inputs: list[str] = Field(default_factory=list)
    results: dict[str, CheckResult] = Field(default_factory=dict)
    recording_baselines: dict[str, float] = Field(default_factory=dict)
    recording_session: str | None = None
    recording_clocks: dict[str, str] = Field(default_factory=dict)
    playback_pause: bool = False
    recording_started: datetime | None = None


class Soundcheck:
    def __init__(
        self, path: Path | None = None, identity: Callable[[str], str] | None = None
    ) -> None:
        self.path = path
        self.identity = identity or mount_identity
        self.state = SoundcheckState()
        self.pending_save = False
        self.load_error: str | None = None
        if path is not None:
            try:
                self.state = SoundcheckState.model_validate(settings.read(path))
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as error:
                self.load_error = workflow_storage.quarantine(path, error)

    def save(self, state: SoundcheckState) -> None:
        if self.path is not None:
            settings.write_json_model(self.path, state)
        self.state = state
        self.pending_save = False

    def observe(
        self, status: models.ShowStatus, configuration: str, now: datetime | None = None
    ) -> None:
        disk = status.recs.disk
        scope = self.scope(status, configuration, now)
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

    def scope(
        self, status: models.ShowStatus, configuration: str, now: datetime | None = None
    ) -> str:
        now = now or datetime.now().astimezone()
        disk = status.recs.disk
        return hashlib.sha256(
            json.dumps(
                [
                    now.date().isoformat(),
                    configuration,
                    self.identity(disk.path) if disk is not None else '',
                    disk.path if disk is not None else '',
                    [[c.device, c.channels, c.name] for c in status.recs.channels],
                    status.recs.service.state,
                    status.recs.session_directory,
                    status.recs.instance_token,
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()

    def invalidate(self, reason: str) -> None:
        invalid = self.state.model_copy(
            update={
                'results': {
                    k: v.model_copy(update={'state': 'invalid', 'evidence': reason})
                    for k, v in self.state.results.items()
                },
                'recording_baselines': {},
                'recording_session': None,
                'recording_clocks': {},
                'playback_pause': False,
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
                    'recording_baselines': {},
                    'recording_session': None,
                    'recording_clocks': {},
                    'playback_pause': False,
                    'recording_started': None,
                }
            )
        )

    def record_start(
        self, status: models.ShowStatus, capture_clocks: dict[str, str] | None = None
    ) -> None:
        if (
            status.recs.service.state != 'connected'
            or not status.recs.session_directory
        ):
            raise ValueError('A current recording session is required for the sample')
        baseline = {
            input_key(c): c.recorded_seconds
            for c in status.recs.channels
            if c.recorded_seconds is not None
        }
        if not set(self.state.expected_inputs) <= baseline.keys():
            raise ValueError('Recorded duration is unavailable for an expected input')
        expected = self.expected_sources(status.recs)
        if capture_clocks is not None and (
            not expected or not expected <= capture_clocks.keys()
        ):
            raise ValueError('Capture identity is unavailable for an expected input')
        self.save(
            self.state.model_copy(
                update={
                    'recording_baselines': baseline,
                    'recording_session': status.recs.session_directory,
                    'playback_pause': False,
                    'recording_clocks': {
                        k: v for k, v in (capture_clocks or {}).items() if k in expected
                    },
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
        capture_clocks: dict[str, str] | None = None,
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
                and status.recs.service.state == 'connected'
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
            passed = status.recs.service.state == 'connected' and not failed
            evidence = (
                'Measured signal without clipping on all expected recording inputs'
                if passed
                else f'Check signal, recording, or connection: {", ".join(failed)}'
            )
        elif step == 'recording':
            baseline = self.state.recording_baselines
            current = {
                input_key(c): c.recorded_seconds
                for c in status.recs.channels
                if c.recorded_seconds is not None
            }
            missing = [
                k
                for k in self.state.expected_inputs
                if k not in baseline or k not in current or current[k] < baseline[k] + 1
            ]
            passed = bool(
                status.recs.service.state == 'connected'
                and self.state.recording_session
                and self.state.recording_session == status.recs.session_directory
                and not missing
                and all(
                    (capture_clocks or {}).get(k) == v
                    for k, v in self.state.recording_clocks.items()
                )
            )
            evidence = (
                'At least one second was saved for every expected recording input'
                if passed
                else 'Make and check a sample for every expected input in this session'
                + (': ' + ', '.join(missing) if missing else '')
            )
        elif step == 'playback':
            recording = self.state.results.get('recording')
            passed = bool(
                confirmation
                and recording
                and recording.state == 'passed'
                and self.state.recording_session == status.recs.session_directory
                and note.strip()
            )
            evidence = (
                'Operator heard the just-recorded sample: ' + note.strip()[:500]
                if passed
                else 'Check sample writes, select that sample on Playback, '
                'then confirm what you heard'
            )
        elif step == 'lights':
            passed = confirmation and status.lyte.service.state == 'connected'
            evidence = (
                'Operator saw the expected light-test output'
                if passed
                else 'Run Test lights explicitly and inspect output; '
                'disabled lighting may be skipped'
            )
        elif step == 'stream':
            passed = (
                confirmation
                and status.streamo.service.state == 'connected'
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

    def expected_sources(self, status: models.RecsStatus) -> set[str]:
        return {
            c.device
            for c in status.channels
            if input_key(c) in self.state.expected_inputs
        }

    def allow_playback_pause(
        self, status: models.ShowStatus, capture_clocks: dict[str, str]
    ) -> None:
        recording = self.state.results.get('recording')
        if (
            not status.recs.paused
            and self.state.recording_session == status.recs.session_directory
            and recording is not None
            and recording.state == 'passed'
            and self.state.recording_clocks
            and all(
                capture_clocks.get(k) == v
                for k, v in self.state.recording_clocks.items()
            )
        ):
            self.save(self.state.model_copy(update={'playback_pause': True}))


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
