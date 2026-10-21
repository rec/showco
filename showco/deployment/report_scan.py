"""Read-only report collector, also sent to the target's Python over SSH stdin."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field


class Session(BaseModel, frozen=True):
    path: str
    started_at: datetime
    ended_at: datetime
    open: bool = False
    metadata_available: bool = True
    audio_files: int = 0
    captured_seconds: float = 0.0
    total_bytes: int = 0
    midi_messages: int = 0
    tracks: dict[str, float] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)


class Report(BaseModel, frozen=True):
    sessions: list[Session]
    broadcast_seconds: float | None = None
    issues: dict[str, int] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    cpu_peak_percent: float | None = None
    minimum_free_bytes: int | None = None


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError(f'timestamp lacks a timezone: {value}')
    return result.astimezone(UTC)


def select_sessions(
    sessions: list[Session], gap_hours: float, older: int = 0
) -> list[Session]:
    """Select a qualifying show after grouping sessions by their end-to-start gaps."""
    if not math.isfinite(gap_hours) or gap_hours <= 0:
        raise ValueError('gap hours must be finite and greater than zero')
    groups: list[list[Session]] = []
    end: datetime | None = None
    for session in sorted(sessions, key=lambda s: s.started_at):
        if end is None or session.started_at - end >= timedelta(hours=gap_hours):
            groups.append([])
        groups[-1].append(session)
        end = max(end, session.ended_at) if end else session.ended_at
    qualifying = [
        g
        for g in groups
        if sum(s.captured_seconds for s in g if s.audio_files > 0 and s.total_bytes > 0)
        > 60
    ]
    return qualifying[-1 - older] if 0 <= older < len(qualifying) else []


def safe_message(message: str) -> str:
    message = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', message)
    message = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', message)
    message = re.sub(r'\b\w+://\S+', '[URL redacted]', message)
    message = re.sub(
        r'(?i)((?:password|access_token|stream_key|client_secret)\s*[=:]\s*)\S+',
        r'\1[redacted]',
        message,
    )
    return ' '.join(message.split())[:400]


def log_evidence(
    paths: list[Path], start: datetime, end: datetime
) -> tuple[float | None, dict[str, int]]:
    issues: Counter[str] = Counter()
    for path in paths:
        with path.open(errors='replace') as source:
            for line in source:
                parts = line.split(maxsplit=2)
                if len(parts) < 3:
                    continue
                try:
                    when = timestamp(parts[0])
                except ValueError:
                    continue
                if not start <= when <= end:
                    continue
                if parts[1] in ('ERROR', 'CRITICAL', 'WARNING'):
                    message = safe_message(parts[2])
                    # Repeated audio losses differ only in their frame count.
                    message = re.sub(r'Dropped \d+ frames', 'Dropped frames', message)
                    issues[f'{path.parent.name}: {message}'] += 1
    # Progress counters lack attempt IDs, so even increasing values may span
    # separate encoders. They cannot establish a broadcast duration lower bound.
    return None, dict(issues)


def recording_roots(home: Path, limitations: list[str]) -> list[Path]:
    from recs.cfg import settings
    from recs.cfg.cfg import Cfg
    from recs.recording import recording_paths

    cfg = settings.load(Cfg(), daemon=True).cfg
    mounts = [home, *recording_paths.mounted_record_disks()]
    default = Path(cfg.general.default_record_directory)
    roots = [default] if default.is_absolute() else [p / default for p in mounts]
    if cfg.directory.output_directory:
        roots.append(recording_paths.recovery_root(cfg.directory.output_directory))
    # The live snapshot also finds sessions whose output was changed at runtime.
    from reccy.protocol import rpc
    from recs.daemon import paths

    try:
        snapshot = rpc.Client(
            paths.external_control_endpoint(), role='showco-report', timeout=3
        ).call('status_snapshot')
        if isinstance(snapshot, dict):
            for key in ('record_path', 'session_directory', 'previous_record_path'):
                if isinstance(value := snapshot.get(key), str) and value:
                    path = Path(value)
                    roots.append(path.parent if path.suffix else path)
    except (OSError, ValueError, TimeoutError) as error:
        limitations.append(f'Live recs status unavailable: {safe_message(str(error))}')
    return sorted({p.resolve() for p in roots if p.is_dir()})


def recording_sessions(roots: list[Path], limitations: list[str]) -> list[Session]:
    from recs.recording import legacy, session_browser, session_quality, session_record
    from recs.recording.files import asset_content, asset_path
    from recs.recording.read import read_recording
    from ufor.recording import AudioStream

    directories: set[Path] = set()

    def unreadable(error: OSError) -> None:
        limitations.append(f'Cannot scan recordings: {safe_message(str(error))}')

    for root in roots:
        for directory, _, files in os.walk(root, onerror=unreadable):
            if 'recording.toml' in files or 'session-record.jsonl' in files:
                directories.add(Path(directory).resolve())
    sessions: list[Session] = []
    for path in sorted(directories):
        try:
            summary = session_browser.summarize(path)
            if summary is None:
                journal_path = path / 'session-record.jsonl'
                if not journal_path.is_file():
                    limitations.append(f'Unreadable recording document: {path}')
                    continue
                journal = session_record.read(journal_path)
                sessions.append(
                    Session(
                        path=str(path),
                        started_at=timestamp(journal.started_at),
                        ended_at=timestamp(journal.ended_at)
                        if journal.ended_at
                        else max(
                            [
                                timestamp(journal.started_at),
                                *(timestamp(e.timestamp) for e in journal.events),
                                *(timestamp(f.timestamp) for f in journal.files),
                            ]
                        ),
                        open=journal.ended_at is None,
                        metadata_available=False,
                        issues=[
                            'Recording document unavailable; audio totals unknown',
                            *journal.errors,
                            *journal.warnings,
                        ],
                    )
                )
                continue
            quality = session_quality.inspect(path)
            document = read_recording(path / 'recording.toml')
            ended_at = (
                timestamp(summary.ended_at)
                if summary.ended_at
                else timestamp(summary.started_at)
                + timedelta(seconds=summary.duration or 0)
            )
            media_issues: list[str] = []
            if summary.ended_at is None and document.body.journal is not None:
                try:
                    asset = next(
                        a for a in document.assets if a.name == document.body.journal
                    )
                    journal_path = path / asset_path(asset)
                    journal = (
                        legacy.read(journal_path)
                        if asset.encoding == 'recs-session-v3'
                        else session_record.read(journal_path)
                    )
                    ended_at = max(
                        [
                            ended_at,
                            *(timestamp(e.timestamp) for e in journal.events),
                            *(timestamp(f.timestamp) for f in journal.files),
                        ]
                    )
                except (OSError, ValueError):
                    media_issues.append('Open session end unknown: journal unreadable')
            audio_assets = {
                f.asset
                for s in document.body.streams
                if isinstance(s, AudioStream)
                for f in [*s.fragments, *s.unmapped_fragments]
            }
            media_assets = {
                f.asset for s in document.body.streams for f in s.fragments
            } | audio_assets
            media_bytes = 0
            audio_files = 0
            for asset in document.assets:
                if asset.name not in media_assets:
                    continue
                try:
                    size = (path / asset_path(asset)).stat().st_size
                    media_bytes += size
                    audio_files += asset.name in audio_assets
                    if size != asset_content(asset).byte_length:
                        media_issues.append(f'Media size changed: {asset.name}')
                except (OSError, ValueError):
                    media_issues.append(f'Media missing or unreadable: {asset.name}')
            tracks: dict[str, float] = {}
            for track in quality.tracks:
                name = (
                    f'{track.source_name or track.source_id}: '
                    f'{track.track_name or track.stream}'
                )
                tracks[name] = tracks.get(name, 0.0) + track.captured_seconds
            sessions.append(
                Session(
                    path=str(path),
                    started_at=timestamp(summary.started_at),
                    ended_at=ended_at,
                    open=summary.state != 'sealed',
                    audio_files=audio_files,
                    captured_seconds=max(
                        (t.captured_seconds for t in quality.tracks), default=0.0
                    ),
                    total_bytes=media_bytes,
                    midi_messages=summary.midi_messages,
                    tracks=tracks,
                    issues=[
                        *media_issues,
                        *summary.warnings,
                        *(
                            f'{f.kind}: {f.evidence}'
                            for f in quality.findings
                            if f.severity in ('warning', 'error')
                        ),
                    ],
                )
            )
        except (OSError, ValueError) as error:
            limitations.append(f'Cannot read {path}: {safe_message(str(error))}')
    return sessions


def collect(gap_hours: float, home: Path | None = None, older: int = 0) -> Report:
    home = home or Path.home()
    limitations = [
        'Track durations include pre-roll/tails, not exact audible activity. '
        'Overlapping tracks are not added as show duration.',
        'Recording durations come from metadata, without decoding all media. '
        'Unmounted disks are not searched.',
        'Histories have finite retention; issue observations can overlap '
        'between sources.',
        'Broadcast duration is unknown: retained encoder progress lacks reliable '
        'attempt identifiers and does not prove Twitch delivery.',
        'Shows qualify with more than 60 seconds of captured audio, using the '
        'longest captured track per session and summing across grouped sessions. '
        'Sessions without saved audio files do not contribute.',
    ]
    sessions = select_sessions(
        recording_sessions(recording_roots(home, limitations), limitations),
        gap_hours,
        older,
    )
    if not sessions:
        return Report(
            sessions=[],
            limitations=[
                *limitations,
                f'No qualifying show at history offset {older}.',
            ],
        )
    start = min(s.started_at for s in sessions)
    end = max(s.ended_at for s in sessions)
    state = home / '.local/state'
    logs = [
        p
        for n in ('recs', 'streamo', 'showco', 'lyte')
        for p in (state / n).glob(f'{n}.log*')
        if p.is_file()
    ]
    issues: Counter[str] = Counter(safe_message(i) for s in sessions for i in s.issues)
    try:
        broadcast, log_issues = log_evidence(logs, start, end)
        issues.update(log_issues)
    except OSError as error:
        broadcast = None
        limitations.append(f'Log scan incomplete: {safe_message(str(error))}')
    try:
        journal = subprocess.run(
            [
                'journalctl',
                '--user',
                '--no-pager',
                '-o',
                'json',
                '-n',
                '5000',
                '--since',
                start.strftime('%Y-%m-%d %H:%M:%S UTC'),
                '--until',
                end.strftime('%Y-%m-%d %H:%M:%S UTC'),
                *(
                    a
                    for n in ('recs', 'streamo', 'showco', 'lyte')
                    for a in ('-u', f'{n}.service')
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if journal.returncode:
            limitations.append('User service journal unavailable.')
        else:
            lines = journal.stdout.splitlines()
            if len(lines) >= 5000:
                limitations.append(
                    'Service journal limited to the latest 5000 entries.'
                )
            for line in lines:
                entry = json.loads(line)
                if str(entry.get('PRIORITY', '6')) in ('0', '1', '2', '3', '4'):
                    issues[
                        'systemd: ' + safe_message(str(entry.get('MESSAGE', '')))
                    ] += 1
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        limitations.append(f'Service journal unavailable: {safe_message(str(error))}')
    peak: float | None = None
    free: int | None = None
    for path in (state / 'showco/monitoring').glob('*.jsonl'):
        try:
            with path.open() as source:
                for line in source:
                    record = json.loads(line)
                    # Include only aggregates wholly within the report window.
                    if not (
                        start
                        <= timestamp(record['started_at'])
                        <= timestamp(record['ended_at'])
                        <= end
                    ):
                        continue
                    if (value := record.get('cpu_peak_percent')) is not None:
                        peak = max(peak or 0, float(value))
                    if (value := record.get('disk_minimum_free_bytes')) is not None:
                        free = min(free, int(value)) if free is not None else int(value)
                    if record.get('disk_pause_occurred'):
                        issues['Recording paused because disk space was low'] += 1
                    issues.update(
                        safe_message(str(v)) for v in record.get('errors', {}).values()
                    )
        except (OSError, ValueError, KeyError, TypeError) as error:
            limitations.append(
                f'Monitoring history incomplete: {safe_message(str(error))}'
            )
    incidents = state / 'showco/incidents.json'
    if incidents.exists():
        try:
            for incident in json.loads(incidents.read_text()).get('incidents', []):
                if start <= timestamp(incident['timestamp']) <= end:
                    message = str(incident['message'])
                    if any(
                        w in message.lower()
                        for w in ('error', 'fail', 'offline', 'lost', 'disconnect')
                    ):
                        issues['showCo: ' + safe_message(message)] += 1
        except (OSError, ValueError, KeyError, TypeError) as error:
            limitations.append(
                f'Incident history incomplete: {safe_message(str(error))}'
            )
    return Report(
        sessions=sessions,
        broadcast_seconds=broadcast,
        issues=dict(issues),
        limitations=limitations,
        cpu_peak_percent=peak,
        minimum_free_bytes=free,
    )


if __name__ == '__main__':
    try:
        print(collect(float(sys.argv[1]), older=int(sys.argv[2])).model_dump_json())
    except (ImportError, OSError, ValueError) as error:
        sys.exit(f'ERROR: cannot collect show report: {safe_message(str(error))}')
