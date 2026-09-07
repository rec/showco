from __future__ import annotations

import os
import shlex
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess, TimeoutExpired
from typing import TextIO

from pydantic import BaseModel
from reccy.runtime import subprocess
from tqdm import tqdm

from . import recs, repositories, revision, services
from .provision import config, provision, script, ssh

RunCommand = Callable[
    [Sequence[str]],
    CompletedProcess[str],
]


class Program(BaseModel, frozen=True):
    name: str
    directory: Path
    service_names: list[str]


class StepResult(BaseModel, frozen=True):
    program: str
    step: str
    command: list[str]
    returncode: int
    output: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def update_remote_target(
    selected: list[str],
    *,
    host: str | None = None,
    root: Path | None = None,
    target_config: config.Config | None = None,
    output: TextIO = sys.stdout,
    clear_settings: bool = True,
) -> int:
    provision_config = target_config or provisioning_config()
    target_host = host or provision_config.network.host
    ssh_target = f"{provision_config.network.user}@{target_host}"
    command = remote_update_command(
        selected,
        root or provision_config.paths.root,
        skip_worktree_check=True,
        clear_settings=clear_settings,
    )
    tqdm.write(f"Updating {ssh_target} from GitHub", file=output)
    with progress_bar(1, output) as progress:
        progress.set_description_str(f"Updating {ssh_target} from GitHub")
        result = run_remote_step(
            "target",
            "update",
            ssh.ssh_command(provision_config, ssh_target, command),
        )
        progress.update()
    if not result.ok:
        report_failure(result, output)
        return 1
    return 0


def update_target(
    selected: list[str],
    *,
    root: Path | None = None,
    run_command: RunCommand | None = None,
    output: TextIO = sys.stdout,
    clear_settings: bool = False,
) -> int:
    provision_config = provisioning_config()
    root = root or provision_config.paths.root
    run_command = run_command or run_command_with_timeout
    if clear_settings:
        print("Clearing saved Recs settings.", file=output)
        clear_settings_result = clear_recs_settings_step(
            provision_config.network.user, run_command
        )
        if not clear_settings_result.ok:
            report_failures([clear_settings_result], output)
            return 1
    programs = programs_for_repositories(
        selected,
        root,
        provision_config.twitch.enabled,
        provision_config.lyte.enabled,
    )
    if not check_main_branches(programs, run_command, output):
        return 1
    refresh_definitions = any(p.name == "reccy" for p in programs)
    with progress_bar(len(programs), output) as progress:
        if "showco" in selected:
            return update_target_with_showco(
                programs,
                provision_config.network.web_port,
                run_command,
                output,
                progress,
                refresh_definitions,
            )
        service_names = selected_service_names(programs)
        results = [run_service_step(n, "stop", run_command) for n in service_names]
        for program in programs:
            progress.set_description_str(f"Updating {program.name}")
            results.extend(update_program_on_target(program, run_command))
            progress.update()

        results.extend(
            start_or_refresh_service_step(
                n,
                refresh_definitions,
                root,
                provision_config,
                run_command,
            )
            for n in service_names
        )
        if "showco" in service_names:
            results.append(
                showco_revision_step(
                    root, provision_config.network.web_port, run_command
                )
            )
        if "recs" in service_names:
            results.append(recs_status_changes_step(run_command))
    report_failures(results, output)
    return 0 if all(r.ok for r in results) else 1


def update_target_with_showco(
    programs: list[Program],
    web_port: int,
    run_command: RunCommand,
    output: TextIO,
    progress: tqdm,
    refresh_definitions: bool,
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
        results.extend(
            start_or_refresh_service_step(
                n,
                refresh_definitions,
                showco.directory.parent,
                provisioning_config(),
                run_command,
            )
            for n in service_names
        )
        progress.update()
    results.append(
        start_or_refresh_service_step(
            "showco",
            refresh_definitions,
            showco.directory.parent,
            provisioning_config(),
            run_command,
        )
    )
    results.append(showco_revision_step(showco.directory.parent, web_port, run_command))
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
            ["uv", "sync", "--locked", "--directory", str(program.directory)],
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


def clear_recs_settings_step(user: str, run_command: RunCommand) -> StepResult:
    return run_step(
        "recs",
        "clear saved settings",
        ["rm", "-f", str(Path("/home") / user / ".config/recs/settings.json")],
        run_command,
    )


def showco_revision_step(
    root: Path, web_port: int, run_command: RunCommand
) -> StepResult:
    command = revision.showco_revision_command(root, web_port, retry=True)
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
    return expand_repository_selection(arguments)


def expand_repository_selection(selected: list[str]) -> list[str]:
    expanded = {
        name
        for selected_name in selected
        for name in DOWNSTREAM_REPOSITORIES[selected_name]
    }
    return [n for n in REPOSITORY_NAMES if n in expanded]


def programs_for_repositories(
    selected: list[str],
    root: Path,
    twitcho_enabled: bool = True,
    lyte_enabled: bool = True,
) -> list[Program]:
    return [
        Program(
            name=n,
            directory=root / n,
            service_names=[
                s
                for s in SERVICES_BY_REPOSITORY[n]
                if (twitcho_enabled or s != "twitcho") and (lyte_enabled or s != "lyte")
            ],
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
    values = config.load_values(
        provision.PROVISION_DIR / "config.toml",
        provision.PROVISION_DIR / "secrets.toml",
    )
    return config.config_from_values(values)


def remote_update_command(
    selected: list[str],
    root: Path,
    *,
    skip_worktree_check: bool = False,
    clear_settings: bool = True,
) -> str:
    arguments_list = ["--target-machine", "--root", str(root)]
    if not clear_settings:
        arguments_list.append("--no-clear-settings")
    arguments = shlex.join([*arguments_list, *selected])
    showco_directory = shlex.quote(str(root / "showco"))
    dependency_directories = shlex.join(
        [str(root / name) for name in ["reccy", "recs", "twitcho", "lyte"]]
    )
    worktree_check = ""
    if not skip_worktree_check:
        worktree_check = (
            "status=$(git status --porcelain --untracked-files=no) && "
            'if [ -n "$status" ]; then '
            'printf "%s\\n" "showco target worktree has tracked changes" >&2; '
            'printf "%s\\n" "$status" >&2; exit 1; fi && '
        )
    return (
        f"cd {showco_directory} && "
        f"{worktree_check}"
        'upstream=$(git rev-parse --abbrev-ref --symbolic-full-name "@{upstream}") && '
        "remote=${upstream%%/*} && branch=${upstream#*/} && "
        'git fetch "$remote" "+refs/heads/$branch:refs/remotes/$remote/$branch" && '
        'git reset --hard "$remote/$branch" && '
        f"for directory in {dependency_directories}; do "
        'cd "$directory" && '
        'upstream=$(git rev-parse --abbrev-ref --symbolic-full-name "@{upstream}") && '
        "remote=${upstream%%/*} && branch=${upstream#*/} && "
        'git fetch "$remote" "+refs/heads/$branch:refs/remotes/$remote/$branch" && '
        'git reset --hard "$remote/$branch" || exit 1; done && '
        'git config --global url."https://github.com/".insteadOf '
        '"ssh://git@github.com/" && '
        'PATH="$HOME/.local/bin:$PATH" uv sync --locked --directory '
        f"{showco_directory} && "
        f"cd {showco_directory} && "
        'PATH="$HOME/.local/bin:$PATH" '
        f"uv run --locked showco go {arguments}"
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
        elif step == "refresh":
            result = services.refresh_service_definition(
                service_name, runner=service_runner(run_command)
            )
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


def start_or_refresh_service_step(
    service_name: str,
    refresh_definitions: bool,
    root: Path,
    provision_config: config.Config,
    run_command: RunCommand,
) -> StepResult:
    if refresh_definitions:
        if service_name == "recs":
            return install_recs_service(root, provision_config, run_command)
        if service_name == "lyte":
            return install_lyte_service(
                root, provision_config.lyte.daemon_config, run_command
            )
        if service_name == "twitcho":
            return install_twitcho_service(
                root, provision_config.network.user, run_command
            )
    return run_service_step(
        service_name, "refresh" if refresh_definitions else "start", run_command
    )


def install_lyte_service(
    root: Path, daemon_config: Path, run_command: RunCommand
) -> StepResult:
    directory = root / "lyte"
    config_path = directory / daemon_config
    command = (
        f"cd {shlex.quote(str(directory))} && "
        "uv run --locked lyte daemon install "
        f"--config {shlex.quote(str(config_path))}"
    )
    return run_step("lyte", "install service", ["sh", "-c", command], run_command)


def install_recs_service(
    root: Path, provision_config: config.Config, run_command: RunCommand
) -> StepResult:
    directory = root / "recs"
    arguments = ["uv", "run", "--locked", "recs", "daemon", "install"]
    for name in script.unique_selectors(
        n for mixer in provision_config.mixers for n in mixer.audio_device_names
    ):
        arguments.extend(["--include", name])
    for name in script.unique_selectors(
        n for mixer in provision_config.mixers for n in mixer.midi_input_names
    ):
        arguments.extend(["--midi-include", name])
    if any(mixer.osc for mixer in provision_config.mixers):
        arguments.extend(
            [
                "--osc-nodes",
                str(
                    Path("/home")
                    / provision_config.network.user
                    / ".config/recs/mixers.toml"
                ),
            ]
        )
    command = f"cd {shlex.quote(str(directory))} && {shlex.join(arguments)}"
    return run_step("recs", "install service", ["sh", "-c", command], run_command)


def install_twitcho_service(
    root: Path, user: str, run_command: RunCommand
) -> StepResult:
    directory = root / "twitcho"
    config_path = Path("/home") / user / ".config/twitcho/config.json"
    command = (
        f"cd {shlex.quote(str(directory))} && "
        "uv run --locked twitcho daemon install "
        f"--config {shlex.quote(str(config_path))}"
    )
    return run_step("twitcho", "install service", ["sh", "-c", command], run_command)


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
        if "rebase" in command:
            env["GIT_SEQUENCE_EDITOR"] = ":"
            env["GIT_EDITOR"] = ":"
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


REPOSITORY_NAMES = repositories.REPOSITORY_NAMES
DOWNSTREAM_REPOSITORIES = {
    "reccy": ["reccy", "recs", "twitcho", "lyte", "showco"],
    "recs": ["recs", "showco"],
    "twitcho": ["twitcho"],
    "lyte": ["lyte"],
    "showco": ["showco"],
}
SERVICES_BY_REPOSITORY = {
    "reccy": ["recs", "showco", "twitcho", "lyte"],
    "recs": ["recs"],
    "showco": ["showco"],
    "twitcho": ["twitcho"],
    "lyte": ["lyte"],
}
