from __future__ import annotations

import sys
import tomllib
from enum import StrEnum, auto
from pathlib import Path
from typing import TextIO

from pydantic import BaseModel
from tqdm import tqdm

from ..provision import config, provision, ssh
from . import update


class PublicationState(BaseModel, frozen=True):
    program: update.Program
    remote: str
    branch: str
    upstream_commit: str
    rewritten: bool = False


class DependencyRefresh(StrEnum):
    FAILED = auto()
    UNCHANGED = auto()
    UPDATED = auto()


def update_from_provisioning_machine(
    selected: list[str],
    *,
    host: str | None = None,
    root: Path | None = None,
    local_root: Path | None = None,
    target_config: config.Config | None = None,
    run_command: update.RunCommand | None = None,
    output: TextIO = sys.stdout,
    autosquash: int = 50,
    clear_settings: bool = True,
) -> int:
    run_command = run_command or update.run_command_with_timeout
    provision_config = target_config or update.provisioning_config()
    selected = update.expand_repository_selection(selected)
    local_directory = local_root or provision.local_checkout_directory()
    if not prepare_local_repositories(
        selected,
        local_directory,
        run_command,
        output,
        autosquash=autosquash,
    ):
        return 1
    if not refresh_local_dependencies(selected, local_directory, run_command, output):
        return 1

    with update.progress_bar(1, output) as progress:
        target_host = host or provision_config.network.host
        ssh_target = f"{provision_config.network.user}@{target_host}"
        command = update.remote_update_command(
            selected,
            root or provision_config.paths.root,
            clear_settings=clear_settings,
        )
        progress.set_description_str(f"Updating {ssh_target}")
        target_result = update.run_remote_step(
            "target",
            "update",
            ssh.ssh_command(provision_config, ssh_target, command),
        )
        progress.update()
    if not target_result.ok:
        update.report_failure(target_result, output)
        return 1
    return 0


def prepare_local_repositories(
    selected: list[str],
    root: Path,
    run_command: update.RunCommand,
    output: TextIO,
    *,
    autosquash: int = 50,
) -> bool:
    programs = update.programs_for_repositories(selected, root)
    if not update.check_main_branches(programs, run_command, output):
        return False
    clean_results = [update.clean_worktree_step(p, run_command) for p in programs]
    if failures := [r for r in clean_results if not r.ok]:
        update.report_failures(failures, output)
        return False
    states = []
    for program in programs:
        state = publication_state(program, run_command)
        if isinstance(state, update.StepResult):
            update.report_failure(state, output)
            return False
        states.append(state)
    if autosquash:
        rewritten_states = autosquash_publications(
            states, autosquash, run_command, output
        )
        if rewritten_states is None:
            return False
        states = rewritten_states
    with update.progress_bar(len(programs), output) as progress:
        for state in states:
            progress.set_description_str(f"Pushing {state.program.name}")
            result = push_program(state, run_command, output)
            progress.update()
            if not result.ok:
                update.report_failure(result, output)
                return False
    return True


def refresh_local_dependencies(
    selected: list[str], root: Path, run_command: update.RunCommand, output: TextIO
) -> bool:
    programs = update.programs_for_repositories(selected, root)
    updated: list[str] = []
    unchanged: list[str] = []
    skipped: list[str] = []
    with update.progress_bar(len(programs), output) as progress:
        for program in programs:
            progress.set_description_str(f"Synchronizing {program.name}")
            dependencies = INTERNAL_DEPENDENCIES.get(program.name)
            if not dependencies:
                skipped.append(program.name)
                progress.update()
                continue
            result = refresh_program_dependencies(
                program, dependencies, run_command, output
            )
            progress.update()
            if result == DependencyRefresh.FAILED:
                return False
            (updated if result == DependencyRefresh.UPDATED else unchanged).append(
                program.name
            )
    outcomes: list[str] = []
    if updated:
        outcomes.append(f"updated {', '.join(updated)}")
    if unchanged:
        outcomes.append(f"unchanged {', '.join(unchanged)}")
    if skipped:
        outcomes.append(f"no internal dependencies {', '.join(skipped)}")
    tqdm.write(f"Dependency synchronization: {'; '.join(outcomes)}.", file=output)
    return True


def refresh_program_dependencies(
    program: update.Program,
    dependencies: list[str],
    run_command: update.RunCommand,
    output: TextIO,
) -> DependencyRefresh:
    before_sources = locked_dependency_sources(program, dependencies)
    lock = update.run_step(
        program.name,
        "refresh dependencies",
        [
            "uv",
            "lock",
            "--directory",
            str(program.directory),
            *(a for n in dependencies for a in ("--upgrade-package", n)),
        ],
        run_command,
    )
    if not lock.ok:
        update.report_failure(lock, output)
        restore_generated_lockfile(program, run_command, output)
        return DependencyRefresh.FAILED
    if locked_dependency_sources(program, dependencies) == before_sources:
        if not restore_generated_lockfile(program, run_command, output):
            return DependencyRefresh.FAILED
    status, _ = lockfile_status_step(program, run_command)
    if not status.ok:
        update.report_failure(status, output)
        restore_generated_lockfile(program, run_command, output)
        return DependencyRefresh.FAILED
    verification_commands = [
        (
            "check lockfile",
            ["uv", "lock", "--check", "--directory", str(program.directory)],
        ),
        (
            "test",
            [
                "uv",
                "run",
                "--locked",
                "--directory",
                str(program.directory),
                "pytest",
            ],
        ),
    ]
    for step, command in verification_commands:
        result = update.run_step(program.name, step, command, run_command)
        if not result.ok:
            update.report_failure(result, output)
            restore_generated_lockfile(program, run_command, output)
            return DependencyRefresh.FAILED
    final_status, final_changed = lockfile_status_step(program, run_command)
    if not final_status.ok:
        update.report_failure(final_status, output)
        restore_generated_lockfile(program, run_command, output)
        return DependencyRefresh.FAILED
    if not final_changed:
        return DependencyRefresh.UNCHANGED
    stage = update.run_step(
        program.name,
        "stage lockfile",
        ["git", "-C", str(program.directory), "add", "--", "uv.lock"],
        run_command,
    )
    if not stage.ok:
        update.report_failure(stage, output)
        restore_generated_lockfile(program, run_command, output)
        return DependencyRefresh.FAILED
    commit = update.run_step(
        program.name,
        "commit dependencies",
        [
            "git",
            "-C",
            str(program.directory),
            "commit",
            "-m",
            "Update internal dependencies",
        ],
        run_command,
    )
    if not commit.ok:
        update.report_failure(commit, output)
        restore_generated_lockfile(program, run_command, output)
        return DependencyRefresh.FAILED
    state = publication_state(program, run_command)
    if isinstance(state, update.StepResult):
        update.report_failure(state, output)
        return DependencyRefresh.FAILED
    push = normal_push_step(program, state.remote, state.branch, run_command)
    if not push.ok:
        update.report_failure(push, output)
        return DependencyRefresh.FAILED
    return DependencyRefresh.UPDATED


def lockfile_status_step(
    program: update.Program, run_command: update.RunCommand
) -> tuple[update.StepResult, bool]:
    status = update.run_step(
        program.name,
        "dependency changed paths",
        ["git", "-C", str(program.directory), "status", "--porcelain"],
        run_command,
    )
    if not status.ok:
        return status, False
    tracked = [s for s in status.output.splitlines() if not s.startswith("??")]
    invalid = [s for s in tracked if s[3:] != "uv.lock"]
    if invalid:
        return (
            update.StepResult(
                program=program.name,
                step="dependency changed paths",
                command=status.command,
                returncode=1,
                output="dependency refresh changed unexpected paths:\n"
                + "\n".join(invalid),
            ),
            False,
        )
    return status.model_copy(update={"output": ""}), bool(tracked)


def locked_dependency_sources(
    program: update.Program, dependencies: list[str]
) -> dict[str, str]:
    data = tomllib.loads((program.directory / "uv.lock").read_text())
    packages = data.get("package", [])
    if not isinstance(packages, list):
        return {}
    result = {}
    for package in packages:
        if not isinstance(package, dict):
            continue
        name = package.get("name")
        source = package.get("source")
        if (
            isinstance(name, str)
            and name in dependencies
            and isinstance(source, dict)
            and isinstance(git := source.get("git"), str)
        ):
            result[name] = git
    return result


def restore_generated_lockfile(
    program: update.Program, run_command: update.RunCommand, output: TextIO
) -> bool:
    result = update.run_step(
        program.name,
        "restore generated lockfile",
        [
            "git",
            "-C",
            str(program.directory),
            "restore",
            "--staged",
            "--worktree",
            "--",
            "uv.lock",
        ],
        run_command,
    )
    if not result.ok:
        update.report_failure(result, output)
    return result.ok


def publication_state(
    program: update.Program, run_command: update.RunCommand
) -> PublicationState | update.StepResult:
    upstream = update.run_step(
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
        return update.StepResult(
            program=program.name,
            step="push",
            command=[],
            returncode=2,
            output=f"bad upstream {upstream.output.strip()}",
        )
    commit = update.run_step(
        program.name,
        "upstream commit",
        ["git", "-C", str(program.directory), "rev-parse", "@{upstream}"],
        run_command,
    )
    if not commit.ok:
        return commit
    if not (upstream_commit := commit.output.strip()):
        return update.StepResult(
            program=program.name,
            step="upstream commit",
            command=commit.command,
            returncode=2,
            output="upstream commit is empty",
        )
    return PublicationState(
        program=program,
        remote=remote,
        branch=branch,
        upstream_commit=upstream_commit,
    )


def push_program(
    state: PublicationState,
    run_command: update.RunCommand,
    output: TextIO | None = None,
) -> update.StepResult:
    program = state.program
    local_commit = update.run_step(
        program.name,
        "local commit",
        ["git", "-C", str(program.directory), "rev-parse", "HEAD"],
        run_command,
    )
    if not local_commit.ok:
        return local_commit
    if not state.rewritten and local_commit.output.strip() == state.upstream_commit:
        return update.StepResult(
            program=program.name,
            step="push",
            command=local_commit.command,
            returncode=0,
            output="already published",
        )
    push = normal_push_step(program, state.remote, state.branch, run_command)
    if push.ok or not state.rewritten:
        return push
    if output:
        tqdm.write(f"{program.name} regular push rejected", file=output)
        tqdm.write(
            f"{program.name} pre-autosquash upstream commit: {state.upstream_commit}",
            file=output,
        )
    force_push = update.run_step(
        program.name,
        "push --force-with-lease",
        [
            "git",
            "-C",
            str(program.directory),
            "push",
            f"--force-with-lease=refs/heads/{state.branch}:{state.upstream_commit}",
            state.remote,
            f"HEAD:{state.branch}",
        ],
        run_command,
    )
    if force_push.ok:
        if output:
            tqdm.write(f"{program.name} push --force-with-lease: ok", file=output)
        return force_push
    return update.StepResult(
        program=program.name,
        step=force_push.step,
        command=force_push.command,
        returncode=force_push.returncode,
        output=(
            f"regular push failed:\n{push.output.rstrip()}\n"
            f"force push failed:\n{force_push.output.rstrip()}"
        ),
    )


def normal_push_step(
    program: update.Program,
    remote: str,
    branch: str,
    run_command: update.RunCommand,
) -> update.StepResult:
    return update.run_step(
        program.name,
        "push",
        [
            "git",
            "-C",
            str(program.directory),
            "push",
            remote,
            f"HEAD:{branch}",
        ],
        run_command,
    )


def autosquash_publications(
    states: list[PublicationState],
    limit: int,
    run_command: update.RunCommand,
    output: TextIO,
) -> list[PublicationState] | None:
    result_states = []
    for state in states:
        result = autosquash_program(state.program, limit, run_command)
        if result is None:
            result_states.append(state)
            continue
        if not result.ok:
            update.report_failure(result, output)
            return None
        result_states.append(state.model_copy(update={"rewritten": True}))
    return result_states


def autosquash_program(
    program: update.Program, limit: int, run_command: update.RunCommand
) -> update.StepResult | None:
    recent_commits = update.run_step(
        program.name,
        "recent commits",
        [
            "git",
            "-C",
            str(program.directory),
            "log",
            "-n",
            str(limit),
            "--format=%H%x00%s%x00",
            "HEAD",
        ],
        run_command,
    )
    if not recent_commits.ok:
        return recent_commits
    if missing_targets := missing_fixup_targets(recent_commits.output):
        return update.StepResult(
            program=program.name,
            step="validate fixup targets",
            command=recent_commits.command,
            returncode=1,
            output=(
                "fixup target is not in the selected autosquash history: "
                + ", ".join(missing_targets)
                + "\nNo rebase was started."
            ),
        )
    if (fixup_commit := oldest_fixup_commit(recent_commits.output)) is None:
        return None
    parent = update.run_step(
        program.name,
        "fixup parent",
        ["git", "-C", str(program.directory), "rev-parse", f"{fixup_commit}^"],
        run_command,
    )
    if not parent.ok:
        return parent
    return update.run_step(
        program.name,
        "autosquash",
        [
            "git",
            "-C",
            str(program.directory),
            "rebase",
            "--interactive",
            "--autosquash",
            parent.output.strip(),
        ],
        run_command,
    )


def oldest_fixup_commit(output: str) -> str | None:
    fixups = [
        commit
        for commit, subject in log_commits(output)
        if subject.startswith("fixup! ")
    ]
    return fixups[-1] if fixups else None


def missing_fixup_targets(output: str) -> list[str]:
    subjects = {subject for _, subject in log_commits(output)}
    return [
        subject.removeprefix("fixup! ")
        for _, subject in log_commits(output)
        if subject.startswith("fixup! ")
        and subject.removeprefix("fixup! ") not in subjects
    ]


def log_commits(output: str) -> list[tuple[str, str]]:
    values = output.split("\0")
    return [
        (commit.strip(), subject.strip())
        for commit, subject in zip(values[::2], values[1::2], strict=False)
    ]


INTERNAL_DEPENDENCIES = {
    "recs": ["reccy"],
    "streamo": ["reccy"],
    "lyte": ["reccy"],
    "showco": ["reccy", "recs"],
}
