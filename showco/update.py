from __future__ import annotations

import os
import shlex
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess, TimeoutExpired
from typing import Annotated, TextIO

import tyro
from pydantic import BaseModel, Field
from reccy import subprocess

from . import machine_role, services
from .provision import config, provision

RunCommand = Callable[
    [Sequence[str]],
    CompletedProcess[str],
]


class Program(BaseModel, frozen=True):
    name: str
    directory: Path
    service_names: list[str]


class UpdateOptions(BaseModel, frozen=True):
    repositories: Annotated[list[str], tyro.conf.Positional] = Field(
        default_factory=list
    )
    host: str | None = None
    target_machine: bool = False


class StepResult(BaseModel, frozen=True):
    program: str
    step: str
    command: list[str]
    returncode: int
    output: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def main(argv: list[str] | None = None) -> int:
    options = tyro.cli(
        UpdateOptions,
        args=sys.argv[1:] if argv is None else argv,
        description="Push development repositories and update the target machine",
    )
    selected = selected_repositories(options.repositories)
    if (
        options.target_machine
        or machine_role.machine_role() == machine_role.TARGET_ROLE
    ):
        return update_target(selected)
    return update_from_provisioning_machine(selected, host=options.host)


def update_from_provisioning_machine(
    selected: list[str],
    *,
    host: str | None = None,
    code_dir: Path | None = None,
    run_command: RunCommand | None = None,
    output: TextIO = sys.stdout,
) -> int:
    code_dir = code_dir or provision.local_code_dir()
    run_command = run_command or run_command_with_timeout
    provision_config = provisioning_config()
    programs = programs_for_repositories(selected, code_dir)
    results = []
    for program in programs:
        print(f"Pushing {program.name} in {program.directory}", file=output)
        output.flush()
        result = push_program(program, run_command)
        results.append(result)
        print_result(result, output)
        output.flush()
        if not result.ok:
            return 1

    target_host = host or provision_config.network.host
    ssh_target = f"{provision_config.network.user}@{target_host}"
    command = remote_update_command(selected)
    print(f"Updating target {ssh_target}: {command}", file=output)
    output.flush()
    results.append(
        run_uncaptured_step(
            "target",
            "update",
            provision.ssh_command(provision_config, ssh_target, command),
        )
    )
    print_results(results, output)
    return 0 if all(r.ok for r in results) else 1


def update_target(
    selected: list[str],
    *,
    code_dir: Path | None = None,
    run_command: RunCommand | None = None,
    output: TextIO = sys.stdout,
) -> int:
    code_dir = code_dir or Path.home() / "code"
    run_command = run_command or run_command_with_timeout
    programs = programs_for_repositories(selected, code_dir)
    if "showco" in selected:
        return update_target_with_showco(programs, run_command, output)
    service_names = selected_service_names(programs)
    results = [run_service_step(n, "stop", run_command) for n in service_names]
    for program in programs:
        print(f"Pulling {program.name} in {program.directory}", file=output)
        results.extend(update_program_on_target(program, run_command))

    results.extend(run_service_step(n, "start", run_command) for n in service_names)
    print_results(results, output)
    return 0 if all(r.ok for r in results) else 1


def update_target_with_showco(
    programs: list[Program],
    run_command: RunCommand,
    output: TextIO,
) -> int:
    showco = program_named(programs, "showco")
    other_programs = [p for p in programs if p.name != "showco"]
    results = [run_service_step("showco", "stop", run_command)]
    print(f"Pulling {showco.name} in {showco.directory}", file=output)
    results.extend(update_program_on_target(showco, run_command))
    for program in other_programs:
        service_names = [n for n in program.service_names if n != "showco"]
        results.extend(run_service_step(n, "stop", run_command) for n in service_names)
        print(f"Pulling {program.name} in {program.directory}", file=output)
        results.extend(update_program_on_target(program, run_command))
        results.extend(run_service_step(n, "start", run_command) for n in service_names)
    results.append(run_service_step("showco", "start", run_command))
    print_results(results, output)
    return 0 if all(r.ok for r in results) else 1


def update_program_on_target(
    program: Program, run_command: RunCommand
) -> list[StepResult]:
    results = []
    clean = clean_worktree_step(program, run_command)
    results.append(clean)
    if not clean.ok:
        return results
    before = run_step(
        program.name,
        "current commit",
        ["git", "-C", str(program.directory), "rev-parse", "HEAD"],
        run_command,
    )
    results.append(before)
    if not before.ok:
        return results
    commit = before.output.strip()
    pull = run_step(
        program.name,
        "pull",
        ["git", "-C", str(program.directory), "pull", "--ff-only"],
        run_command,
    )
    results.append(pull)
    if not pull.ok:
        results.append(
            run_step(
                program.name,
                "reset",
                ["git", "-C", str(program.directory), "reset", "--hard", commit],
                run_command,
            )
        )
        return results
    after = run_step(
        program.name,
        "new commit",
        ["git", "-C", str(program.directory), "rev-parse", "HEAD"],
        run_command,
    )
    results.append(after)
    return results


def program_named(programs: list[Program], name: str) -> Program:
    for program in programs:
        if program.name == name:
            return program
    sys.exit(f"ERROR: update target {name} is required")


def selected_repositories(arguments: list[str]) -> list[str]:
    if not arguments:
        return list(REPOSITORY_NAMES)
    invalid = [a for a in arguments if a not in REPOSITORY_NAMES]
    if invalid:
        sys.exit(
            "ERROR: unknown update target(s): "
            + ", ".join(invalid)
            + f"\nExpected one of: {', '.join(REPOSITORY_NAMES)}"
        )
    result = []
    for a in arguments:
        if a not in result:
            result.append(a)
    return result


def programs_for_repositories(selected: list[str], code_dir: Path) -> list[Program]:
    return [
        Program(
            name=n,
            directory=code_dir / n,
            service_names=SERVICES_BY_REPOSITORY[n],
        )
        for n in selected
    ]


def selected_service_names(programs: list[Program]) -> list[str]:
    result = []
    for program in programs:
        for service_name in program.service_names:
            if service_name not in result:
                result.append(service_name)
    return result


def push_program(program: Program, run_command: RunCommand) -> StepResult:
    clean = clean_worktree_step(program, run_command)
    if not clean.ok:
        return clean
    upstream = run_step(
        program.name,
        "upstream",
        [
            "git",
            "-C",
            str(program.directory),
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
        ],
        run_command,
    )
    if not upstream.ok:
        return upstream
    remote, _, branch = upstream.output.strip().partition("/")
    if not remote or not branch:
        return StepResult(
            program=program.name,
            step="push",
            command=[],
            returncode=2,
            output=f"bad upstream {upstream.output.strip()}",
        )
    return run_step(
        program.name,
        "push",
        ["git", "-C", str(program.directory), "push", remote, f"HEAD:{branch}"],
        run_command,
    )


def clean_worktree_step(program: Program, run_command: RunCommand) -> StepResult:
    result = run_step(
        program.name,
        "clean worktree",
        ["git", "-C", str(program.directory), "status", "--porcelain"],
        run_command,
    )
    if not result.ok:
        return result
    tracked_changes = tracked_worktree_changes(result.output)
    if not tracked_changes:
        return StepResult(
            program=program.name,
            step="clean worktree",
            command=result.command,
            returncode=0,
            output="",
        )
    return StepResult(
        program=program.name,
        step="clean worktree",
        command=result.command,
        returncode=1,
        output="repository has uncommitted changes:\n" + tracked_changes,
    )


def tracked_worktree_changes(status_output: str) -> str:
    return "\n".join(
        line for line in status_output.splitlines() if not line.startswith("??")
    )


def provisioning_config() -> config.Config:
    values = config.merge_values(
        config.read_toml(provision.PROVISION_DIR / "config.toml"),
        config.read_toml(provision.PROVISION_DIR / "secrets.toml"),
    )
    return config.config_from_values(values)


def remote_update_command(selected: list[str]) -> str:
    arguments = shlex.join(selected)
    return (
        f'cd "$HOME/code/showco" && uv run showco update --target-machine {arguments}'
    ).rstrip()


def run_service_step(
    service_name: str, step: str, run_command: RunCommand
) -> StepResult:
    spec = services.SERVICES[service_name]
    command = ["systemctl", "--user", step, spec.systemd_unit]
    try:
        controller = services.service_controller(
            spec, runner=service_runner(run_command)
        )
        if step == "stop":
            result = controller.stop()
        elif step == "start":
            result = controller.start()
        else:
            return StepResult(
                program=service_name,
                step=step,
                command=command,
                returncode=2,
                output=f"unsupported service step {step}",
            )
    except FileNotFoundError as e:
        return StepResult(
            program=service_name,
            step=step,
            command=command,
            returncode=127,
            output=str(e),
        )
    except CalledProcessError as e:
        return StepResult(
            program=service_name,
            step=step,
            command=list(e.cmd),
            returncode=e.returncode,
            output=f"{e.stdout or ''}{e.stderr or ''}",
        )
    except TimeoutExpired as e:
        return StepResult(
            program=service_name,
            step=step,
            command=command,
            returncode=124,
            output=timeout_output(e),
        )
    return StepResult(
        program=service_name,
        step=step,
        command=command,
        returncode=0,
        output=result.details,
    )


def run_step(
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
            output=timeout_output(e),
        )
    return StepResult(
        program=program,
        step=step,
        command=command,
        returncode=completed.returncode,
        output=f"{completed.stdout}{completed.stderr}",
    )


def run_uncaptured_step(program: str, step: str, command: list[str]) -> StepResult:
    try:
        completed = subprocess.run(command, check=False, text=True)
    except FileNotFoundError as e:
        return StepResult(
            program=program,
            step=step,
            command=command,
            returncode=127,
            output=str(e),
        )
    return StepResult(
        program=program,
        step=step,
        command=command,
        returncode=completed.returncode,
        output="",
    )


def run_command_with_timeout(command: Sequence[str]) -> CompletedProcess[str]:
    env = dict(os.environ)
    if command and command[0] == "git":
        env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        command,
        capture_output=True,
        check=False,
        env=env,
        text=True,
        timeout=timeout(command),
    )


def service_runner(
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


def timeout(command: Sequence[str]) -> float:
    if command and command[0] in ("git", "ssh"):
        return 120.0
    return 30.0


def timeout_output(error: TimeoutExpired) -> str:
    output = error.output or ""
    stderr = error.stderr or ""
    return f"command timed out after {error.timeout} seconds\n{output}{stderr}"


def print_results(results: list[StepResult], output: TextIO) -> None:
    for result in results:
        print_result(result, output)


def print_result(result: StepResult, output: TextIO) -> None:
    status = "ok" if result.ok else "failed"
    print(f"{result.program} {result.step}: {status}", file=output)
    if result.output.strip():
        print(result.output.rstrip(), file=output)


REPOSITORY_NAMES = ["reccy", "recs", "showco", "twitcho"]
SERVICES_BY_REPOSITORY = {
    "reccy": ["recs", "showco"],
    "recs": ["recs"],
    "showco": ["showco"],
    "twitcho": ["showco"],
}
