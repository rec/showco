from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from shlex import quote
from subprocess import CompletedProcess
from typing import Annotated, TextIO

import tyro
from pydantic import BaseModel, Field

from ..provision import config, ssh
from . import machine_role, update

SERVICE_NAMES = ('showco', 'recs', 'streamo', 'lyte')

RunCommand = Callable[[Sequence[str]], CompletedProcess[str]]


class LogsOptions(BaseModel, frozen=True):
    services: Annotated[list[str], tyro.conf.Positional] = Field(default_factory=list)
    lines: int = 50
    host: str | None = None


class ClearLogsOptions(BaseModel, frozen=True):
    pass


def clear_main(argv: list[str] | None = None) -> int:
    tyro.cli(
        ClearLogsOptions,
        args=sys.argv[1:] if argv is None else argv,
        description='Clear managed service log files on the showCo target',
    )
    machine_role.require_provisioning_machine('showco clear-logs')
    provision_config = update.provisioning_config()
    completed = run_command_with_timeout(
        ssh.ssh_command(
            provision_config,
            provision_config.ssh_target,
            remote_clear_logs_command(provision_config.paths.root),
        )
    )
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    options = tyro.cli(
        LogsOptions,
        args=sys.argv[1:] if argv is None else argv,
        description='Fetch user service logs from the showCo target machine',
    )
    return fetch_logs(options)


def fetch_logs(
    options: LogsOptions,
    *,
    target_config: config.Config | None = None,
    run_command: RunCommand | None = None,
    output: TextIO = sys.stdout,
) -> int:
    machine_role.require_provisioning_machine('showco logs')
    if options.lines < 1:
        sys.exit('ERROR: --lines must be at least 1')
    services = selected_services(options.services)
    provision_config = target_config or update.provisioning_config()
    target_host = options.host or provision_config.network.host
    ssh_target = f'{provision_config.network.user}@{target_host}'
    command = remote_logs_command(services, options.lines)
    runner = run_command or run_command_with_timeout
    completed = runner(ssh.ssh_command(provision_config, ssh_target, command))
    output.write(completed.stdout)
    output.write(completed.stderr)
    return completed.returncode


def selected_services(arguments: list[str]) -> list[str]:
    if not arguments:
        return list(SERVICE_NAMES)
    invalid = [a for a in arguments if a not in SERVICE_NAMES]
    if invalid:
        sys.exit(
            'ERROR: unknown log target(s): '
            + ', '.join(invalid)
            + f'\nExpected one of: {", ".join(SERVICE_NAMES)}'
        )
    result: list[str] = []
    for service in arguments:
        if service not in result:
            result.append(service)
    return result


def remote_logs_command(services: list[str], lines: int) -> str:
    paths = ' '.join(
        f'"$HOME/.local/state/{service}/{service}.log"' for service in services
    )
    return f'tail --lines={lines} {paths}'


def remote_clear_logs_command(root: Path) -> str:
    services = ' '.join(SERVICE_NAMES)
    errors = quote(str(root / 'showco/showco-errors.txt'))
    return f"""status=0
count=0
for service in {services}; do
    for path in "$HOME/.local/state/$service/$service.log" \
"$HOME/.local/state/$service/$service.log."*; do
        if [ -f "$path" ] && [ ! -L "$path" ]; then
            if : > "$path"; then
                count=$((count + 1))
            else
                status=1
            fi
        fi
    done
done
path={errors}
if [ -f "$path" ] && [ ! -L "$path" ]; then
    if : > "$path"; then
        count=$((count + 1))
    else
        status=1
    fi
fi
printf 'Cleared %s log files.\\n' "$count"
printf '%s\\n' 'Running services may write new entries immediately.' \
    'Shared systemd journal, monitoring and recording history retained.'
exit "$status"
"""


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
        output = f'{e.output or ""}{e.stderr or ""}'
        return CompletedProcess(command, 124, output, 'command timed out after 120s\n')
