from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import tyro
from pydantic import BaseModel

from . import machine_role

STATE_DIRECTORY = Path.home() / ".local/state"
CONFIG_DIRECTORY = Path.home() / ".config/showco"


class BundleOptions(BaseModel, frozen=True):
    output_directory: Path = Path.home() / ".local/state/showco/bundles"


def main(argv: list[str] | None = None) -> int:
    machine_role.require_target_machine("showco bundle")
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
    destination = output_directory / now.strftime("%Y%m%dT%H%M%SZ")
    destination.mkdir(parents=True)
    copied = []
    for source in sources(state_directory, config_directory):
        target = destination / bundle_path(source, state_directory, config_directory)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(str(target.relative_to(destination)))
    manifest = {"created_at": now.isoformat(), "files": copied}
    (destination / "bundle.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return destination


def bundle_path(source: Path, state_directory: Path, config_directory: Path) -> Path:
    if source.is_relative_to(state_directory):
        return Path("state") / source.relative_to(state_directory)
    if source.is_relative_to(config_directory):
        return Path("config") / source.relative_to(config_directory)
    return Path("recordings") / source.name


def sources(state_directory: Path, config_directory: Path) -> list[Path]:
    result = [
        path
        for service in ["showco", "recs", "lyte", "twitcho"]
        if (path := state_directory / service / f"{service}.log").is_file()
    ]
    result.extend(sorted((state_directory / "showco/monitoring").glob("*.jsonl")))
    result.extend(
        path
        for path in [
            state_directory / "recs/status.json",
            config_directory / "config.toml",
            config_directory / "mixers.toml",
        ]
        if path.is_file()
    )
    if (status := recording_status(state_directory / "recs/status.json")) is not None:
        result.append(status)
    return result


def recording_status(path: Path) -> Path | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    record_path = value.get("record_path") if isinstance(value, dict) else None
    candidate = Path(record_path) if isinstance(record_path, str) else None
    return candidate if candidate is not None and candidate.is_file() else None
