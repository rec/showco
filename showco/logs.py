from __future__ import annotations

import shlex
import subprocess
import sys
from collections.abc import Callable, Sequence
from subprocess import CompletedProcess
from typing import Annotated, TextIO

import tyro
from pydantic import BaseModel, Field

from . import machine_role, update
from .provision import config, provision

SERVICE_NAMES = ("recs", "twitcho", "lyte")

RunCommand = Callable[[Sequence[str]], CompletedProcess[str]]


class LogsOptions(BaseModel, frozen=True):
    services: Annotated[list[str], tyro.conf.Positional] = Field(default_factory=list)
    lines: int = 50
    host: str | None = None


def main(argv: list[str] | None = None) -> int:
    options = tyro.cli(
        LogsOptions,
        args=sys.argv[1:] if argv is None else argv,
        description="Fetch user service logs from the Showco target machine",
    )
    return fetch_logs(options)


def fetch_logs(
    options: LogsOptions,
    *,
    target_config: config.Config | None = None,
    run_command: RunCommand | None = None,
    output: TextIO = sys.stdout,
) -> int:
    machine_role.require_provisioning_machine("showco logs")
    if options.lines < 1:
        sys.exit("ERROR: --lines must be at least 1")
    services = selected_services(options.services)
    provision_config = target_config or update.provisioning_config()
    target_host = options.host or provision_config.network.host
    ssh_target = f"{provision_config.network.user}@{target_host}"
    command = remote_logs_command(services, options.lines)
    runner = run_command or run_command_with_timeout
    completed = runner(provision.ssh_command(provision_config, ssh_target, command))
    output.write(completed.stdout)
    output.write(completed.stderr)
    return completed.returncode


def selected_services(arguments: list[str]) -> list[str]:
    if not arguments:
        return list(SERVICE_NAMES)
    invalid = [a for a in arguments if a not in SERVICE_NAMES]
    if invalid:
        sys.exit(
            "ERROR: unknown log target(s): "
            + ", ".join(invalid)
            + f"\nExpected one of: {', '.join(SERVICE_NAMES)}"
        )
    result: list[str] = []
    for service in arguments:
        if service not in result:
            result.append(service)
    return result


def remote_logs_command(services: list[str], lines: int) -> str:
    units = " ".join(
        f"--unit {shlex.quote(f'{service}.service')}" for service in services
    )
    return provision.user_session_command(
        f"journalctl --user --no-pager --lines={lines} {units}"
    )


def run_command_with_timeout(command: Sequence[str]) -> CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            capture_output=True,
            check=False,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired as e:
        output = f"{e.output or ''}{e.stderr or ''}"
        return CompletedProcess(command, 124, output, "command timed out after 120s\n")
