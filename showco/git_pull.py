from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

RunCommand = Callable[
    [Sequence[str]],
    subprocess.CompletedProcess[str],
]


@dataclass(frozen=True)
class Program:
    name: str
    directory: Path


@dataclass(frozen=True)
class StepResult:
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
        Program("recs", code_dir / "recs"),
        Program("twitcho", code_dir / "twitcho"),
        Program("showco", code_dir / "showco"),
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
    results.extend(
        [
            _run_service_step("recs", "restart", run_command),
            _run_service_step("showco", "restart", run_command),
        ]
    )

    failures = sum(not r.ok for r in results)
    for result in results:
        _print_result(result, output)
    return 0 if failures == 0 else 1


def _run_service_step(service: str, step: str, run_command: RunCommand) -> StepResult:
    return _run_step(
        service,
        step,
        ["systemctl", "--user", step, service],
        run_command,
    )


def main(argv: list[str] | None = None) -> int:
    if argv in (["-h"], ["--help"]):
        print("Usage: showco git-pull")
        return 0
    if argv:
        print("showco git-pull takes no arguments", file=sys.stderr)
        return 2
    return update_programs()


def _run_step(
    program: str, step: str, command: list[str], run_command: RunCommand
) -> StepResult:
    try:
        completed = run_command(command)
    except FileNotFoundError as e:
        return StepResult(program, step, command, 127, str(e))
    except subprocess.TimeoutExpired as e:
        return StepResult(program, step, command, 124, _timeout_output(e))
    return StepResult(
        program,
        step,
        command,
        completed.returncode,
        f"{completed.stdout}{completed.stderr}",
    )


def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
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


def _timeout(command: Sequence[str]) -> float:
    if command and command[0] == "git":
        return 120.0
    return 30.0


def _timeout_output(error: subprocess.TimeoutExpired) -> str:
    output = error.output or ""
    stderr = error.stderr or ""
    return f"command timed out after {error.timeout} seconds\n{output}{stderr}"


def _print_result(result: StepResult, output: TextIO) -> None:
    status = "ok" if result.ok else "failed"
    print(f"  {result.step}: {status}", file=output)
    if result.output.strip():
        print(result.output.rstrip(), file=output)
