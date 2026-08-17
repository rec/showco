from __future__ import annotations

import shlex
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from subprocess import CompletedProcess
from typing import Annotated, TextIO

import tyro
from pydantic import BaseModel

from . import machine_role, update
from .provision import config, provision

RunCommand = Callable[[Sequence[str]], CompletedProcess[str]]


class PythonOptions(BaseModel, frozen=True):
    code: Annotated[str, tyro.conf.Positional]


def main(argv: list[str] | None = None) -> int:
    options = tyro.cli(
        PythonOptions,
        args=sys.argv[1:] if argv is None else argv,
        description="Run Python source in the Showco target environment",
    )
    return run_python(options)


def run_python(
    options: PythonOptions,
    *,
    target_config: config.Config | None = None,
    run_command: RunCommand | None = None,
    output: TextIO = sys.stdout,
) -> int:
    machine_role.require_provisioning_machine("showco python")
    provision_config = target_config or update.provisioning_config()
    ssh_target = f"{provision_config.network.user}@{provision_config.network.host}"
    command = remote_python_command(options.code, provision_config.paths.root)
    completed = (run_command or update.run_command_with_timeout)(
        provision.ssh_command(provision_config, ssh_target, command)
    )
    output.write(completed.stdout)
    output.write(completed.stderr)
    return completed.returncode


def remote_python_command(code: str, root: Path) -> str:
    return (
        f"cd {shlex.quote(str(root / 'showco'))} && "
        f".venv/bin/python -c {shlex.quote(code)}"
    )
