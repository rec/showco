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
    directories: set[Path] = set()

    def unreadable(error: OSError) -> None:
        limitations.append(f'Cannot scan recordings: {safe_message(str(error))}')

    for root in roots:
        for directory, _, files in os.walk(root, onerror=unreadable):
            if 'session-record.jsonl' in files:
                directories.add(Path(directory).resolve())
    sessions: list[Session] = []
    for path in sorted(directories):
        try:
            sessions.append(recording_session(path))
        except (OSError, ValueError) as error:
            limitations.append(f'Cannot read {path}: {safe_message(str(error))}')
    return sessions


def recording_session(path: Path) -> Session:
    from recs.recording import session_record

    entries, issues = session_record.read_entries(path / 'session-record.jsonl')
    header = next(
        (e for e in entries if isinstance(e, session_record.SessionHeader)), None
    )
    if header is None:
        raise ValueError('Session recording has no header')
    footer = next(
        (e for e in reversed(entries) if isinstance(e, session_record.SessionFooter)),
        None,
    )
    times = [timestamp(header.started_at)]
    rates: dict[str, float] = {}
    files: dict[
        tuple[str, str], session_record.AudioFileRecord | session_record.EventFileRecord
    ] = {}
    for entry in entries:
        if isinstance(entry, session_record.ClockRecord):
            rates.update(
                {t.name: t.rate.numerator / t.rate.denominator for t in entry.timebases}
            )
        elif isinstance(entry, session_record.AudioTimelineRecord):
            if entry.sample_rate is not None:
                rates.setdefault(entry.clock_id, entry.sample_rate)
            issues.extend(
                f'{g.reason}: {entry.stream_id}, frames {g.start}-{g.end}'
                for g in entry.gaps
                if g.reason not in {'silence_suppressed', 'short_capture'}
            )
        elif isinstance(entry, session_record.SessionFooter):
            times.append(timestamp(entry.ended_at))
        elif not isinstance(entry, session_record.SessionHeader):
            times.append(timestamp(entry.timestamp))
            if isinstance(entry, session_record.WarningRecord):
                issues.append(entry.message)
            elif isinstance(entry, session_record.EventRecord):
                if entry.type == 'source_online' and entry.sample_rate is not None:
                    if entry.clock_id is not None:
                        rates.setdefault(entry.clock_id, entry.sample_rate)
                if entry.type in {
                    'error',
                    'source_offline',
                    'source_failed',
                    'source_stopped',
                    'midi_source_failed',
                    'midi_source_stopped',
                    'buffer_overflow',
                }:
                    issues.append(
                        f'{entry.type}: {entry.source or "recs"} at {entry.timestamp}'
                        + (f': {entry.reason}' if entry.reason else '')
                        + (f': {entry.value}' if isinstance(entry.value, str) else '')
                    )
            elif isinstance(
                entry, session_record.AudioFileRecord | session_record.EventFileRecord
            ):
                file_key = entry.stream_id, entry.path
                previous = files.get(file_key)
                # Replayed starts must not erase an already completed file count.
                if (
                    entry.type != 'file_started'
                    or previous is None
                    or previous.type != 'file_finished'
                ):
                    files[file_key] = entry

    audio_files = midi_messages = 0
    sizes: dict[Path, int] = {}
    captures: dict[str, dict[tuple[str, int | str, int], float]] = {}
    labels: dict[str, str] = {}
    for entry in files.values():
        if entry.type == 'file_discarded':
            continue
        media = (path / entry.path).resolve()
        try:
            if not media.is_relative_to(path):
                raise ValueError('Media path escapes session directory')
            if not media.is_file():
                raise ValueError('Media path is not a file')
            size = media.stat().st_size
        except (OSError, ValueError):
            issues.append(f'Media missing or unreadable: {entry.path}')
            continue
        sizes[media] = size
        if entry.type != 'file_finished':
            issues.append(f'Unfinished media; totals incomplete: {entry.path}')
        if isinstance(entry, session_record.EventFileRecord):
            if entry.media_type == 'midi' and entry.type == 'file_finished':
                midi_messages += entry.quantity_count or 0
            continue
        audio_files += 1
        source, track = session_record.audio_stream_parts(entry.stream_id)
        labels[entry.stream_id] = (
            f'{entry.source or source}: {entry.track_name or track}'
        )
        if entry.type != 'file_finished':
            continue
        rate = entry.sample_rate or rates.get(entry.clock_id)
        if rate is None or entry.quantity_count is None:
            issues.append(
                f'Audio duration unknown; frame count or rate missing: {entry.path}'
            )
            continue
        if size > 0:
            # Encoding variants share a clock, native end, and captured frame count.
            capture = (
                entry.clock_id,
                entry.frame_count if entry.frame_count is not None else entry.path,
                entry.quantity_count,
            )
            captures.setdefault(entry.stream_id, {})[capture] = (
                entry.quantity_count / rate
            )
    durations = {k: sum(v.values()) for k, v in captures.items()}
    tracks: dict[str, float] = {}
    for key, duration in durations.items():
        tracks[labels[key]] = tracks.get(labels[key], 0.0) + duration
    total_bytes = sum(sizes.values())
    return Session(
        path=str(path),
        started_at=times[0],
        ended_at=timestamp(footer.ended_at) if footer is not None else max(times),
        open=footer is None,
        audio_files=audio_files,
        captured_seconds=max(durations.values(), default=0.0),
        total_bytes=total_bytes,
        midi_messages=midi_messages,
        tracks=tracks,
        issues=list(dict.fromkeys(issues)),
    )


def collect(gap_hours: float, home: Path | None = None, older: int = 0) -> Report:
    home = home or Path.home()
    limitations = [
        'Track durations include pre-roll/tails, not exact audible activity. '
        'Overlapping tracks are not added as show duration.',
        'Recording durations come only from session-record.jsonl frame counts '
        'and rates; media contents and audio headers are not verified. '
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
