#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from string import hexdigits
from typing import Annotated

import tyro
from pydantic import BaseModel, Field

from . import config, remote, script, state

PROVISION_DIR = Path(__file__).resolve().parent


class GoOptions(BaseModel, frozen=True):
    config_path: Annotated[Path, tyro.conf.arg(name="config")] = (
        PROVISION_DIR / "config.toml"
    )
    secrets: Path = PROVISION_DIR / "secrets.toml"
    host: str | None = None
    user: str | None = None
    port: int | None = None
    root: Path | None = None
    reccy_repo: str | None = None
    recs_repo: str | None = None
    twitcho_repo: str | None = None
    showco_repo: str | None = None
    lyte_repo: str | None = None
    lyte_enabled: bool | None = None
    lyte_daemon_config: Path | None = None
    system: Annotated[
        bool,
        tyro.conf.arg(help="Refresh operating-system packages before provisioning"),
    ] = False
    repositories: Annotated[list[str] | None, tyro.conf.Positional] = None
    remote: bool = False
    target_machine: bool = False
    clear_settings: bool = True
    autosquash: int | None = Field(default=None, ge=0)


def resolved_config(options: GoOptions) -> config.Config:
    env = config.load_values(options.config_path, options.secrets)
    provision_config = config.config_from_values(
        env,
        host=options.host,
        user=options.user,
        port=options.port,
        root=options.root,
        reccy_repo=options.reccy_repo,
        recs_repo=options.recs_repo,
        twitcho_repo=options.twitcho_repo,
        showco_repo=options.showco_repo,
        lyte_repo=options.lyte_repo,
        lyte_enabled=options.lyte_enabled,
        lyte_daemon_config=options.lyte_daemon_config,
    )
    validate_config(provision_config)
    return provision_config


def run(options: GoOptions, *, provision_config: config.Config | None = None) -> int:
    provision_config = provision_config or resolved_config(options)
    from .. import update

    if not update.prepare_local_repositories(
        update.REPOSITORY_NAMES,
        local_checkout_directory(),
        update.run_command_with_timeout,
        sys.stdout,
    ):
        sys.exit(
            "ERROR: local repositories are not ready for Raspberry Pi provisioning"
        )
    if options.host is not None:
        persist_network_host(options.config_path, options.host)
    if options.root is not None:
        persist_paths_root(options.config_path, options.root)
    remote_script = "/tmp/showco-provision-pi.sh"
    with tempfile.NamedTemporaryFile(
        "w", delete=False, prefix="showco-provision-pi.", suffix=".sh"
    ) as fp:
        local_script = Path(fp.name)
        fp.write(script.REMOTE_SCRIPT)
    try:
        remote.provision_remote(
            provision_config,
            local_script,
            remote_script,
            system=options.system,
            fingerprint=state.provisioning_fingerprint(
                provision_config, script.REMOTE_SCRIPT
            ),
        )
    finally:
        local_script.unlink(missing_ok=True)
    print(f"Provisioned {provision_config.ssh_target}.")
    return 0


def persist_network_host(config_path: Path, host: str) -> None:
    persist_config_value(config_path, "network", "host", host)


def persist_paths_root(config_path: Path, root: Path) -> None:
    persist_config_value(config_path, "paths", "root", str(root))


def persist_config_value(config_path: Path, table: str, key: str, value: str) -> None:
    path = config_path.expanduser()
    lines = path.read_text().splitlines()
    value_line = f"{key} = {toml_string(value)}"
    table_index_value = table_index(lines, f"[{table}]")
    if table_index_value is None:
        path.write_text(f"[{table}]\n" + value_line + "\n\n" + "\n".join(lines) + "\n")
        return
    next_table = next_table_index(lines, table_index_value + 1)
    for index in range(table_index_value + 1, next_table):
        if lines[index].lstrip().startswith(key):
            lines[index] = value_line
            path.write_text("\n".join(lines) + "\n")
            return
    lines.insert(table_index_value + 1, value_line)
    path.write_text("\n".join(lines) + "\n")


def table_index(lines: list[str], table: str) -> int | None:
    for index, line in enumerate(lines):
        if line.strip() == table:
            return index
    return None


def next_table_index(lines: list[str], start: int) -> int:
    for index in range(start, len(lines)):
        line = lines[index].strip()
        if line.startswith("[") and line.endswith("]"):
            return index
    return len(lines)


def toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def validate_config(provision_config: config.Config) -> None:
    errors = config_errors(provision_config)
    if errors:
        sys.exit("ERROR: invalid provisioning configuration\n" + "\n".join(errors))


def config_errors(provision_config: config.Config) -> list[str]:
    errors = []
    external = config.external_wifi(provision_config)
    private = config.internal_wifi(provision_config)
    if not external.name or external.name == "TODO":
        errors.append("- networks.external.wifi.external.name is required")
    if not private.password or private.password == "TODO":
        errors.append("- networks.internal.wifi.private.password is required")
    elif not valid_wpa_password(private.password):
        errors.append(
            "- networks.internal.wifi.private.password must be 8-63 printable "
            "ASCII characters or 64 hexadecimal digits"
        )
    daemon_config = lyte_daemon_config_path(
        provision_config, local_checkout_directory()
    )
    if provision_config.lyte.enabled and not daemon_config.is_file():
        errors.append(f"- lyte.daemon_config does not exist: {daemon_config}")
    return errors


def valid_wpa_password(password: str) -> bool:
    if len(password) == 64:
        return all(c in hexdigits for c in password)
    return 8 <= len(password) <= 63 and password.isascii() and password.isprintable()


def lyte_daemon_config_path(provision_config: config.Config, root: Path) -> Path:
    return root / "lyte" / provision_config.lyte.daemon_config


def local_checkout_directory() -> Path:
    return PROVISION_DIR.parents[2]
