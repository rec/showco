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
from tqdm import tqdm

from . import machine_role, recs, services
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
    root: Path | None = None
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
        if options.root is None:
            result = update_target(selected)
        else:
            result = update_target(selected, root=options.root)
    else:
        if options.root is None:
            result = update_from_provisioning_machine(selected, host=options.host)
        else:
            result = update_from_provisioning_machine(
                selected, host=options.host, root=options.root
            )
    tqdm.write("Success!" if result == 0 else "ERROR: update failed")
    return result


def update_from_provisioning_machine(
    selected: list[str],
    *,
    host: str | None = None,
    root: Path | None = None,
    local_root: Path | None = None,
    target_config: config.Config | None = None,
    run_command: RunCommand | None = None,
    output: TextIO = sys.stdout,
) -> int:
    run_command = run_command or run_command_with_timeout
    provision_config = target_config or provisioning_config()
    programs = programs_for_repositories(
        selected, local_root or provision.local_checkout_directory()
    )
    if not check_main_branches(programs, run_command, output):
        return 1
    with progress_bar(len(programs) + 1, output) as progress:
        for program in programs:
            progress.set_description_str(f"Pushing {program.name}")
            result = push_program(program, run_command, output)
            progress.update()
            if not result.ok:
                report_failure(result, output)
                return 1

        target_host = host or provision_config.network.host
        ssh_target = f"{provision_config.network.user}@{target_host}"
        command = remote_update_command(selected, root or provision_config.paths.root)
        progress.set_description_str(f"Updating {ssh_target}")
        target_result = run_remote_step(
            "target",
            "update",
            provision.ssh_command(provision_config, ssh_target, command),
        )
        if rejected_update_arguments(target_result):
            target_result = run_remote_step(
                "target",
                "legacy update",
                provision.ssh_command(
                    provision_config,
                    ssh_target,
                    legacy_remote_update_command(root or provision_config.paths.root),
                ),
            )
            if target_result.ok:
                target_result = run_remote_step(
                    "target",
                    "update",
                    provision.ssh_command(provision_config, ssh_target, command),
                )
        progress.update()
    if not target_result.ok:
        report_failure(target_result, output)
        return 1
    return 0


def update_target(
    selected: list[str],
    *,
    root: Path | None = None,
    run_command: RunCommand | None = None,
    output: TextIO = sys.stdout,
) -> int:
    root = root or provisioning_config().paths.root
    run_command = run_command or run_command_with_timeout
    programs = programs_for_repositories(selected, root)
    if not check_main_branches(programs, run_command, output):
        return 1
    with progress_bar(len(programs), output) as progress:
        if "showco" in selected:
            return update_target_with_showco(programs, run_command, output, progress)
        service_names = selected_service_names(programs)
        results = [run_service_step(n, "stop", run_command) for n in service_names]
        for program in programs:
            progress.set_description_str(f"Updating {program.name}")
            results.extend(update_program_on_target(program, run_command))
            progress.update()

        results.extend(run_service_step(n, "start", run_command) for n in service_names)
        if "showco" in service_names:
            results.append(showco_revision_step(root, run_command))
        if "recs" in service_names:
            results.append(recs_status_changes_step(run_command))
    report_failures(results, output)
    return 0 if all(r.ok for r in results) else 1


def update_target_with_showco(
    programs: list[Program],
    run_command: RunCommand,
    output: TextIO,
    progress: tqdm,
) -> int:
    showco = program_named(programs, "showco")
    other_programs = [p for p in programs if p.name != "showco"]
    results = [run_service_step("showco", "stop", run_command)]
    progress.set_description_str(f"Updating {showco.name}")
    results.extend(update_program_on_target(showco, run_command))
    progress.update()
    for program in other_programs:
        service_names = [n for n in program.service_names if n != "showco"]
        results.extend(run_service_step(n, "stop", run_command) for n in service_names)
        progress.set_description_str(f"Updating {program.name}")
        results.extend(update_program_on_target(program, run_command))
        results.extend(run_service_step(n, "start", run_command) for n in service_names)
        progress.update()
    results.append(run_service_step("showco", "start", run_command))
    results.append(showco_revision_step(showco.directory.parent, run_command))
    if "recs" in selected_service_names(programs):
        results.append(recs_status_changes_step(run_command))
    report_failures(results, output)
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
    if not pull.ok:
        upstream = run_step(
            program.name,
            "upstream commit",
            ["git", "-C", str(program.directory), "rev-parse", "@{upstream}"],
            run_command,
        )
        results.append(upstream)
        upstream_commit = upstream.output.strip()
        if upstream.ok and upstream_commit and upstream_commit != commit:
            reset = run_step(
                program.name,
                "reset to upstream",
                [
                    "git",
                    "-C",
                    str(program.directory),
                    "reset",
                    "--hard",
                    upstream_commit,
                ],
                run_command,
            )
            results.append(reset)
            if reset.ok:
                pull = reset
            else:
                results.append(pull)
        else:
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
    else:
        results.append(pull)
    after = run_step(
        program.name,
        "new commit",
        ["git", "-C", str(program.directory), "rev-parse", "HEAD"],
        run_command,
    )
    results.append(after)
    if after.ok and after.output.strip() != commit:
        dependencies = run_step(
            program.name,
            "sync dependencies",
            ["uv", "sync", "--frozen", "--directory", str(program.directory)],
            run_command,
        )
        results.append(dependencies)
        if not dependencies.ok:
            results.append(
                run_step(
                    program.name,
                    "reset",
                    ["git", "-C", str(program.directory), "reset", "--hard", commit],
                    run_command,
                )
            )
    return results


def recs_status_changes_step(run_command: RunCommand) -> StepResult:
    result = run_step(
        "recs",
        "status is advancing",
        ["sh", "-c", recs.status_changes_command()],
        run_command,
    )
    if result.ok:
        return result
    return result.model_copy(
        update={"output": recs.status_failure_summary(result.output)}
    )


def showco_revision_step(root: Path, run_command: RunCommand) -> StepResult:
    showco_directory = shlex.quote(str(root / "showco"))
    command = (
        f"expected=$(git -C {showco_directory} rev-parse HEAD) && "
        "curl --fail --silent --show-error --retry 5 --retry-connrefused "
        "--retry-delay 1 http://127.0.0.1:17352/status | "
        'grep --fixed-strings "\\"revision\\":\\"$expected\\""'
    )
    return run_step("showco", "web UI revision", ["sh", "-c", command], run_command)


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


def programs_for_repositories(selected: list[str], root: Path) -> list[Program]:
    return [
        Program(
            name=n,
            directory=root / n,
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


def push_program(
    program: Program, run_command: RunCommand, output: TextIO | None = None
) -> StepResult:
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
    push = run_step(
        program.name,
        "push",
        ["git", "-C", str(program.directory), "push", remote, f"HEAD:{branch}"],
        run_command,
    )
    if push.ok:
        return push
    fetch = run_step(
        program.name,
        "fetch upstream",
        [
            "git",
            "-C",
            str(program.directory),
            "fetch",
            remote,
            f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}",
        ],
        run_command,
    )
    if not fetch.ok:
        return StepResult(
            program=program.name,
            step=fetch.step,
            command=fetch.command,
            returncode=fetch.returncode,
            output=(
                f"regular push failed:\n{push.output.rstrip()}\n"
                f"could not fetch current upstream commit:\n{fetch.output}"
            ),
        )
    upstream_commit = run_step(
        program.name,
        "upstream commit",
        [
            "git",
            "-C",
            str(program.directory),
            "log",
            "-1",
            "--format=%H%n%s",
            f"{remote}/{branch}",
        ],
        run_command,
    )
    if not upstream_commit.ok:
        return StepResult(
            program=program.name,
            step=upstream_commit.step,
            command=upstream_commit.command,
            returncode=upstream_commit.returncode,
            output=(
                f"regular push failed:\n{push.output.rstrip()}\n"
                f"could not read current upstream commit:\n{upstream_commit.output}"
            ),
        )
    upstream_sha, _, _ = upstream_commit.output.partition("\n")
    if not upstream_sha:
        return StepResult(
            program=program.name,
            step=upstream_commit.step,
            command=upstream_commit.command,
            returncode=2,
            output=(
                f"regular push failed:\n{push.output.rstrip()}\n"
                "current upstream commit is empty"
            ),
        )
    if output:
        tqdm.write(f"{program.name} regular push rejected", file=output)
        tqdm.write(f"{program.name} current upstream commit:", file=output)
        tqdm.write(upstream_commit.output.rstrip(), file=output)
    force_push = run_step(
        program.name,
        "push --force-with-lease",
        [
            "git",
            "-C",
            str(program.directory),
            "push",
            f"--force-with-lease=refs/heads/{branch}:{upstream_sha}",
            remote,
            f"HEAD:{branch}",
        ],
        run_command,
    )
    if force_push.ok:
        if output:
            tqdm.write(f"{program.name} push --force-with-lease: ok", file=output)
        return force_push
    return StepResult(
        program=program.name,
        step=force_push.step,
        command=force_push.command,
        returncode=force_push.returncode,
        output=(
            f"regular push failed:\n{push.output.rstrip()}\n"
            f"force push failed:\n{force_push.output.rstrip()}"
        ),
    )


def check_main_branches(
    programs: list[Program], run_command: RunCommand, output: TextIO
) -> bool:
    results = [main_branch_step(p, run_command) for p in programs]
    failures = [r for r in results if not r.ok]
    if not failures:
        return True
    report_failures(failures, output)
    return False


def main_branch_step(program: Program, run_command: RunCommand) -> StepResult:
    result = run_step(
        program.name,
        "main branch",
        ["git", "-C", str(program.directory), "branch", "--show-current"],
        run_command,
    )
    if not result.ok:
        return result
    branch = result.output.strip()
    if branch == "main":
        return StepResult(
            program=program.name,
            step="main branch",
            command=result.command,
            returncode=0,
            output="",
        )
    return StepResult(
        program=program.name,
        step="main branch",
        command=result.command,
        returncode=1,
        output=f"repository is on {branch or 'a detached HEAD'}, expected main",
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


def remote_update_command(selected: list[str], root: Path) -> str:
    arguments = shlex.join(["--target-machine", "--root", str(root), *selected])
    showco_directory = shlex.quote(str(root / "showco"))
    return (
        f"cd {showco_directory} && "
        'if [ -n "$(git status --porcelain --untracked-files=no)" ]; then '
        'echo "showco target worktree has tracked changes" >&2; exit 1; fi && '
        'upstream=$(git rev-parse --abbrev-ref --symbolic-full-name "@{upstream}") && '
        "remote=${upstream%%/*} && branch=${upstream#*/} && "
        'git fetch "$remote" "+refs/heads/$branch:refs/remotes/$remote/$branch" && '
        'git reset --hard "$remote/$branch" && '
        'PATH="$HOME/.local/bin:$PATH" uv sync --frozen --directory '
        f"{showco_directory} && "
        'PATH="$HOME/.local/bin:$PATH" '
        f"uv run showco update {arguments}"
    ).rstrip()


def legacy_remote_update_command(root: Path) -> str:
    return (
        f'cd {shlex.quote(str(root / "showco"))} && PATH="$HOME/.local/bin:$PATH" '
        "uv run showco update"
    )


def rejected_update_arguments(result: StepResult) -> bool:
    return not result.ok and "Unrecognized options:" in result.output


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


def run_remote_step(program: str, step: str, command: list[str]) -> StepResult:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout(command),
        )
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
    if command and command[0] in ("git", "ssh", "uv"):
        return 120.0
    return 30.0


def timeout_output(error: TimeoutExpired) -> str:
    output = error.output or ""
    stderr = error.stderr or ""
    return f"command timed out after {error.timeout} seconds\n{output}{stderr}"


def progress_bar(total: int, output: TextIO) -> tqdm:
    return tqdm(
        total=total,
        desc="Updating",
        unit="repository",
        file=output,
        disable=not output.isatty(),
    )


def report_failures(results: list[StepResult], output: TextIO) -> None:
    for result in results:
        report_failure(result, output)


def report_failure(result: StepResult, output: TextIO) -> None:
    if result.ok:
        return
    tqdm.write(f"{result.program} {result.step}: failed", file=output)
    if result.output.strip():
        tqdm.write(result.output.rstrip(), file=output)


REPOSITORY_NAMES = ["reccy", "recs", "showco", "twitcho", "lyte"]
SERVICES_BY_REPOSITORY = {
    "reccy": ["recs", "showco", "lyte-midi"],
    "recs": ["recs"],
    "showco": ["showco"],
    "twitcho": ["showco"],
    "lyte": ["lyte-midi"],
}
