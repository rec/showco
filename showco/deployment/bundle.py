from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkdtemp

import tyro
from pydantic import BaseModel

from . import machine_role

STATE_DIRECTORY = Path.home() / '.local/state'
CONFIG_DIRECTORY = Path.home() / '.config/showco'
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_BUNDLE_BYTES = 16 * 1024 * 1024


class BundleOptions(BaseModel, frozen=True):
    output_directory: Path = Path.home() / '.local/state/showco/bundles'


def main(argv: list[str] | None = None) -> int:
    machine_role.require_target_machine('showco bundle')
    options = tyro.cli(BundleOptions, args=sys.argv[1:] if argv is None else argv)
    destination = create_bundle(options.output_directory)
    print(destination)
    return 0


def create_bundle(
    output_directory: Path,
    *,
    state_directory: Path = STATE_DIRECTORY,
    config_directory: Path = CONFIG_DIRECTORY,
    now: datetime | None = None,
) -> Path:
    now = now or datetime.now(timezone.utc)
    output_directory.mkdir(parents=True, exist_ok=True)
    destination = Path(
        mkdtemp(prefix=now.strftime('%Y%m%dT%H%M%SZ-'), dir=output_directory)
    )
    copied: list[str] = []
    truncated: list[str] = []
    omitted: list[str] = []
    remaining = MAX_BUNDLE_BYTES
    for source in sources(state_directory, config_directory):
        relative = str(bundle_path(source, state_directory, config_directory))
        if remaining == 0:
            omitted.append(relative)
            continue
        size = source.stat().st_size
        limit = min(size, MAX_FILE_BYTES, remaining)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open('rb') as original, target.open('wb') as copy:
            if size > limit and source.suffix in {'.log', '.jsonl'}:
                original.seek(-limit, 2)
            copy.write(original.read(limit))
        remaining -= limit
        copied.append(relative)
        if size > limit:
            truncated.append(relative)
    manifest = {
        'created_at': now.isoformat(),
        'files': copied,
        'truncated': truncated,
        'omitted': omitted,
    }
    (destination / 'bundle.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return destination


def bundle_path(source: Path, state_directory: Path, config_directory: Path) -> Path:
    if source.is_relative_to(state_directory):
        return Path('state') / source.relative_to(state_directory)
    if source.is_relative_to(config_directory):
        return Path('config') / source.relative_to(config_directory)
    return Path('recordings') / source.name


def sources(state_directory: Path, config_directory: Path) -> list[Path]:
    result = [
        path
        for path in [config_directory / 'config.toml', config_directory / 'mixers.toml']
        if path.is_file()
    ]
    if (status := recording_status(state_directory / 'recs/status.json')) is not None:
        result.append(status)
    result.extend(
        path
        for service in ['showco', 'recs', 'lyte', 'streamo']
        if (path := state_directory / service / f'{service}.log').is_file()
    )
    result.extend(sorted((state_directory / 'showco/monitoring').glob('*.jsonl')))
    result.extend(
        path
        for path in [
            state_directory / 'recs/status.json',
            state_directory / 'showco/incidents.json',
            state_directory / 'showco/recovery.json',
        ]
        if path.is_file()
    )
    return result


def recording_status(path: Path) -> Path | None:
    if not path.is_file():
        return None
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        value = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    record_path = value.get('record_path') if isinstance(value, dict) else None
    candidate = Path(record_path) if isinstance(record_path, str) else None
    return candidate if candidate is not None and candidate.is_file() else None
