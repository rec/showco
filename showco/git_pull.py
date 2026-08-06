from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess, TimeoutExpired
from typing import TextIO

from pydantic import BaseModel
from reccy import subprocess

from . import services

RunCommand = Callable[
    [Sequence[str]],
    CompletedProcess[str],
]


class Program(BaseModel, frozen=True):
    name: str
    directory: Path


class StepResult(BaseModel, frozen=True):
    program: str
    step: str
    command: list[str]
    returncode: int
    output: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def update_programs(
    *,
    code_dir: Path | None = None,
    run_command: RunCommand | None = None,
    output: TextIO = sys.stdout,
) -> int:
    code_dir = code_dir or Path.home() / "code"
    run_command = run_command or _run_command
    programs = [
        Program(name="recs", directory=code_dir / "recs"),
        Program(name="twitcho", directory=code_dir / "twitcho"),
        Program(name="showco", directory=code_dir / "showco"),
    ]
    results = [
        _run_service_step("recs", "stop", run_command),
        _run_service_step("showco", "stop", run_command),
    ]
    for program in programs:
        print(f"Pulling {program.name} in {program.directory}", file=output)
        results.append(
            _run_step(
                program.name,
                "pull",
                ["git", "-C", str(program.directory), "pull"],
                run_command,
            )
        )
    if all(r.ok for r in results):
        results.extend(
            [
                _run_service_step("recs", "start", run_command),
                _run_service_step("showco", "start", run_command),
            ]
        )

    failures = sum(not r.ok for r in results)
    for result in results:
        _print_result(result, output)
    return 0 if failures == 0 else 1


def _run_service_step(service: str, step: str, run_command: RunCommand) -> StepResult:
    spec = services.SERVICES[service]
    command = ["systemctl", "--user", step, spec.systemd_unit]
    try:
        controller = services.service_controller(
            spec, runner=_service_runner(run_command)
        )
        if step == "stop":
            result = controller.stop()
        elif step == "start":
            result = controller.start()
        else:
            return StepResult(
                program=service,
                step=step,
                command=command,
                returncode=2,
                output=f"unsupported service step {step}",
            )
    except FileNotFoundError as e:
        return StepResult(
            program=service,
            step=step,
            command=command,
            returncode=127,
            output=str(e),
        )
    except CalledProcessError as e:
        return StepResult(
            program=service,
            step=step,
            command=list(e.cmd),
            returncode=e.returncode,
            output=f"{e.stdout or ''}{e.stderr or ''}",
        )
    except TimeoutExpired as e:
        return StepResult(
            program=service,
            step=step,
            command=command,
            returncode=124,
            output=_timeout_output(e),
        )
    return StepResult(
        program=service,
        step=step,
        command=command,
        returncode=0,
        output=result.details,
    )


def main(argv: list[str] | None = None) -> int:
    if argv in (["-h"], ["--help"]):
        print("Usage: showco run git-pull")
        return 0
    if argv:
        print("showco run git-pull takes no arguments", file=sys.stderr)
        return 2
    return update_programs()


def _run_step(
    program: str, step: str, command: list[str], run_command: RunCommand
) -> StepResult:
    try:
        completed = run_command(command)
    except FileNotFoundError as e:
        return StepResult(
            program=program,
            step=step,
            command=command,
            returncode=127,
            output=str(e),
        )
    except TimeoutExpired as e:
        return StepResult(
            program=program,
            step=step,
            command=command,
            returncode=124,
            output=_timeout_output(e),
        )
    return StepResult(
        program=program,
        step=step,
        command=command,
        returncode=completed.returncode,
        output=f"{completed.stdout}{completed.stderr}",
    )


def _run_command(command: Sequence[str]) -> CompletedProcess[str]:
    env = dict(os.environ)
    if command and command[0] == "git":
        env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        command,
        capture_output=True,
        check=False,
        env=env,
        text=True,
        timeout=_timeout(command),
    )


def _service_runner(
    run_command: RunCommand,
) -> Callable[..., CompletedProcess[str]]:
    def run(
        command: list[str],
        *,
        check: bool,
        text: bool,
        capture_output: bool,
    ) -> CompletedProcess[str]:
        completed = run_command(command)
        if check and completed.returncode != 0:
            raise CalledProcessError(
                completed.returncode,
                command,
                output=completed.stdout,
                stderr=completed.stderr,
            )
        return completed

    return run


def _timeout(command: Sequence[str]) -> float:
    if command and command[0] == "git":
        return 120.0
    return 30.0


def _timeout_output(error: TimeoutExpired) -> str:
    output = error.output or ""
    stderr = error.stderr or ""
    return f"command timed out after {error.timeout} seconds\n{output}{stderr}"


def _print_result(result: StepResult, output: TextIO) -> None:
    status = "ok" if result.ok else "failed"
    print(f"  {result.step}: {status}", file=output)
    if result.output.strip():
        print(result.output.rstrip(), file=output)
