"""Fetch a read-only account of the latest show from its target."""

from __future__ import annotations

import shlex
import subprocess
import sys
from collections import Counter
from pathlib import Path

import tyro
from pydantic import BaseModel, Field, ValidationError

from ..provision import ssh
from . import machine_role, report_scan, update


class ReportOptions(BaseModel, frozen=True):
    gap_hours: float = Field(default=3, gt=0, allow_inf_nan=False)
    """Start a separate show at a recording-session gap of this many hours or more."""


def main(argv: list[str] | None = None) -> int:
    options = tyro.cli(
        ReportOptions,
        args=argv,
        description='Report the latest show from saved target evidence',
    )
    machine_role.require_provisioning_machine('showco report')
    cfg = update.provisioning_config()
    root = shlex.quote(str(cfg.paths.root / 'showco'))
    command = f'cd {root} && .venv/bin/python - {options.gap_hours}'
    print(f'Reading latest show on {cfg.ssh_target}...', file=sys.stderr)
    try:
        result = subprocess.run(
            ssh.ssh_command(cfg, cfg.ssh_target, command),
            input=Path(report_scan.__file__).read_text(),
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        sys.exit(f'ERROR: report collection failed: {error}')
    if result.returncode:
        sys.exit(
            'ERROR: report collection failed: '
            + report_scan.safe_message(result.stderr)
        )
    try:
        report = report_scan.Report.model_validate_json(result.stdout)
    except ValidationError:
        sys.exit('ERROR: target returned an invalid show report')
    print(render(report))
    return 0 if report.sessions else 1


def duration(seconds: float) -> str:
    hours, remainder = divmod(int(seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours:02}:{minutes:02}:{seconds:02}'


def render(report: report_scan.Report) -> str:
    if not report.sessions:
        return 'No latest show found.\n' + '\n'.join(
            f'- {i}' for i in report.limitations
        )
    start = min(s.started_at for s in report.sessions)
    end = max(s.ended_at for s in report.sessions)
    lines = [
        'Latest show (UTC)',
        f'{start:%Y-%m-%d %H:%M:%S} to {end:%Y-%m-%d %H:%M:%S}',
        f'{len(report.sessions)} recording sessions; '
        f'elapsed {duration((end - start).total_seconds())}',
        'Saved media (known metadata): '
        f'{sum(s.total_bytes for s in report.sessions) / 1_000_000_000:.2f} GB; '
        f'{sum(s.audio_files for s in report.sessions)} audio files; '
        f'{sum(s.midi_messages for s in report.sessions)} MIDI messages',
    ]
    if any(s.open for s in report.sessions):
        lines.append(
            'Recording still open: totals are provisional; '
            'window ends at the latest saved recording evidence.'
        )
    if any(not s.metadata_available for s in report.sessions):
        lines.append(
            'Some sessions lack recording metadata; saved media totals are incomplete.'
        )
    lines.append(
        'Broadcast output observed: '
        + (
            'unknown'
            if report.broadcast_seconds is None
            else f'at least {duration(report.broadcast_seconds)}'
        )
    )
    tracks: Counter[str] = Counter()
    for session in report.sessions:
        tracks.update(session.tracks)
    lines.extend(['', 'Saved audio by source / track:'])
    lines.extend(f'- {n}: {duration(v)}' for n, v in sorted(tracks.items()))
    if not tracks:
        lines.append('- Unknown; no placed audio evidence available.')
    if report.cpu_peak_percent is not None:
        lines.append(f'Peak CPU: {report.cpu_peak_percent:g}%')
    if report.minimum_free_bytes is not None:
        lines.append(
            f'Minimum disk free: {report.minimum_free_bytes / 1_000_000_000:.2f} GB'
        )
    lines.extend(['', 'Issues found:'])
    issues = Counter(report.issues)
    lines.extend(f'- {n} ({v} observations)' for n, v in issues.most_common(10))
    if not issues:
        lines.append('- None in the available evidence.')
    if len(issues) > 10:
        lines.append(f'- {len(issues) - 10} other issue types omitted.')
    lines.extend(
        [
            '',
            'Recording directories:',
            *(f'- {s.path}' for s in report.sessions),
            '',
            'Evidence limits:',
            *(f'- {i}' for i in report.limitations),
        ]
    )
    return '\n'.join(lines)
