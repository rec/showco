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

from ..provision import config, provision, script, ssh
from ..runtime import recs, revision, services
from . import repositories

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
    clear_settings: bool = False,
) -> int:
    provision_config = target_config or provisioning_config()
    target_host = host or provision_config.network.host
    ssh_target = f'{provision_config.network.user}@{target_host}'
    command = remote_update_command(
        selected,
        root or provision_config.paths.root,
        clear_settings=clear_settings,
    )
    tqdm.write(f'Updating {ssh_target} from GitHub', file=output)
    with progress_bar(1, output) as progress:
        progress.set_description_str(f'Updating {ssh_target} from GitHub')
        result = run_remote_step(
            'target',
            'update',
            ssh.ssh_command(provision_config, ssh_target, command),
        )
        progress.update()
    if not result.ok:
        report_failure(result, output)
        return 1
    return 0


def recs_status_changes_step(run_command: RunCommand) -> StepResult:
    result = run_step(
        'recs',
        'status is advancing',
        ['sh', '-c', recs.status_changes_command()],
        run_command,
    )
    if result.ok:
        return result
    return result.model_copy(
        update={'output': recs.status_failure_summary(result.output)}
    )


def clear_recs_settings_step(user: str, run_command: RunCommand) -> StepResult:
    return run_step(
        'recs',
        'clear saved settings',
        ['rm', '-f', str(Path('/home') / user / '.config/recs/settings.json')],
        run_command,
    )


def showco_revision_step(
    root: Path, web_port: int, run_command: RunCommand
) -> StepResult:
    command = revision.showco_revision_command(root, web_port, retry=True)
    return run_step('showco', 'web UI revision', ['sh', '-c', command], run_command)


def selected_repositories(arguments: list[str]) -> list[str]:
    if not arguments:
        return list(REPOSITORY_NAMES)
    invalid = [a for a in arguments if a not in REPOSITORY_NAMES]
    if invalid:
        sys.exit(
            'ERROR: unknown update target(s): '
            + ', '.join(invalid)
            + f'\nExpected one of: {", ".join(REPOSITORY_NAMES)}'
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
    streamo_enabled: bool = True,
    lyte_enabled: bool = True,
) -> list[Program]:
    return [
        Program(
            name=n,
            directory=root / n,
            service_names=[
                s
                for s in SERVICES_BY_REPOSITORY[n]
                if (streamo_enabled or s != 'streamo') and (lyte_enabled or s != 'lyte')
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
        'main branch',
        ['git', '-C', str(program.directory), 'branch', '--show-current'],
        run_command,
    )
    if not result.ok:
        return result
    branch = result.output.strip()
    if branch == 'main':
        return StepResult(
            program=program.name,
            step='main branch',
            command=result.command,
            returncode=0,
            output='',
        )
    return StepResult(
        program=program.name,
        step='main branch',
        command=result.command,
        returncode=1,
        output=f'repository is on {branch or "a detached HEAD"}, expected main',
    )


def clean_worktree_step(program: Program, run_command: RunCommand) -> StepResult:
    result = run_step(
        program.name,
        'clean worktree',
        ['git', '-C', str(program.directory), 'status', '--porcelain'],
        run_command,
    )
    if not result.ok:
        return result
    tracked_changes = tracked_worktree_changes(result.output)
    if not tracked_changes:
        return StepResult(
            program=program.name,
            step='clean worktree',
            command=result.command,
            returncode=0,
            output='',
        )
    return StepResult(
        program=program.name,
        step='clean worktree',
        command=result.command,
        returncode=1,
        output='repository has uncommitted changes:\n' + tracked_changes,
    )


def tracked_worktree_changes(status_output: str) -> str:
    return '\n'.join(
        line for line in status_output.splitlines() if not line.startswith('??')
    )


def provisioning_config() -> config.Config:
    values = config.load_values(
        provision.PROVISION_DIR / 'config.toml',
        provision.PROVISION_DIR / 'secrets.toml',
    )
    return config.config_from_values(values)


def remote_update_command(
    selected: list[str],
    root: Path,
    *,
    clear_settings: bool = False,
) -> str:
    arguments = ['--target-machine', '--root', str(root)]
    if clear_settings:
        arguments.append('--clear-settings')
    return (
        f'cd {shlex.quote(str(root / "showco"))} && '
        'PATH="$HOME/.local/bin:$PATH" '
        f'uv run --no-sync showco go {shlex.join([*arguments, *selected])}'
    )


def run_service_step(
    service_name: str, step: str, run_command: RunCommand
) -> StepResult:
    spec = services.SERVICES[service_name]
    command = ['systemctl', '--user', step, spec.systemd_unit]
    try:
        controller = services.service_controller(
            spec, runner=service_runner(run_command)
        )
        if step == 'stop':
            result = controller.stop()
        elif step == 'start':
            result = controller.start()
        elif step == 'refresh':
            result = services.refresh_service_definition(
                service_name, runner=service_runner(run_command)
            )
        else:
            return StepResult(
                program=service_name,
                step=step,
                command=command,
                returncode=2,
                output=f'unsupported service step {step}',
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
            output=f'{e.stdout or ""}{e.stderr or ""}',
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
        if service_name == 'recs':
            return install_recs_service(root, provision_config, run_command)
        if service_name == 'lyte':
            return install_lyte_service(
                root, provision_config.lyte.installation_config, run_command
            )
        if service_name == 'streamo':
            return install_streamo_service(
                root, provision_config.network.user, run_command
            )
    return run_service_step(
        service_name, 'refresh' if refresh_definitions else 'start', run_command
    )


def install_lyte_service(
    root: Path, installation_config: Path, run_command: RunCommand
) -> StepResult:
    directory = root / 'lyte'
    config_path = directory / installation_config
    command = (
        f'cd {shlex.quote(str(directory))} && '
        'uv run --locked lyte installation install '
        f'{shlex.quote(str(config_path))}'
    )
    return run_step('lyte', 'install service', ['sh', '-c', command], run_command)


def install_recs_service(
    root: Path, provision_config: config.Config, run_command: RunCommand
) -> StepResult:
    directory = root / 'recs'
    arguments = ['uv', 'run', '--locked', 'recs', 'daemon', 'install']
    for name in script.unique_selectors(
        n for mixer in provision_config.mixers for n in mixer.audio_device_names
    ):
        arguments.extend(['--include', name])
    for name in script.unique_selectors(
        n for mixer in provision_config.mixers for n in mixer.midi_input_names
    ):
        arguments.extend(['--midi-include', name])
    if any(mixer.osc for mixer in provision_config.mixers):
        arguments.extend(
            [
                '--osc-nodes',
                str(
                    Path('/home')
                    / provision_config.network.user
                    / '.config/recs/mixers.toml'
                ),
            ]
        )
    command = f'cd {shlex.quote(str(directory))} && {shlex.join(arguments)}'
    return run_step('recs', 'install service', ['sh', '-c', command], run_command)


def install_streamo_service(
    root: Path, user: str, run_command: RunCommand
) -> StepResult:
    directory = root / 'streamo'
    config_path = Path('/home') / user / '.config/streamo/config.toml'
    command = (
        f'cd {shlex.quote(str(directory))} && '
        'uv run --locked streamo daemon install '
        f'--config {shlex.quote(str(config_path))}'
    )
    return run_step('streamo', 'install service', ['sh', '-c', command], run_command)


def run_step(
    program: str, step: str, command: list[str], run_command: RunCommand
) -> StepResult:
    try:
        completed = run_command(command)
    except OSError as e:
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
        output=f'{completed.stdout}{completed.stderr}',
    )


def run_remote_step(program: str, step: str, command: list[str]) -> StepResult:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=3600 if step == 'update' else timeout(command),
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
        output=f'{completed.stdout}{completed.stderr}',
    )


def run_command_with_timeout(command: Sequence[str]) -> CompletedProcess[str]:
    env = dict(os.environ)
    if command and command[0] == 'git':
        env['GIT_TERMINAL_PROMPT'] = '0'
        if 'rebase' in command:
            env['GIT_SEQUENCE_EDITOR'] = ':'
            env['GIT_EDITOR'] = ':'
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
    if command and command[0] in ('git', 'ssh', 'uv'):
        return 120.0
    return 30.0


def timeout_output(error: TimeoutExpired) -> str:
    output = error.output or ''
    stderr = error.stderr or ''
    return f'command timed out after {error.timeout} seconds\n{output}{stderr}'


def progress_bar(total: int, output: TextIO) -> tqdm:
    return tqdm(
        total=total,
        desc='Updating',
        unit='repository',
        file=output,
        disable=not output.isatty(),
    )


def report_failures(results: list[StepResult], output: TextIO) -> None:
    for result in results:
        report_failure(result, output)


def report_failure(result: StepResult, output: TextIO) -> None:
    if result.ok:
        return
    tqdm.write(f'{result.program} {result.step}: failed', file=output)
    if result.output.strip():
        tqdm.write(result.output.rstrip(), file=output)


REPOSITORY_NAMES = repositories.REPOSITORY_NAMES
DOWNSTREAM_REPOSITORIES = {
    'reccy': ['reccy', 'recs', 'streamo', 'lyte', 'showco'],
    'recs': ['recs', 'showco'],
    'streamo': ['streamo'],
    'lyte': ['lyte'],
    'showco': ['showco'],
}
SERVICES_BY_REPOSITORY = {
    'reccy': ['recs', 'showco', 'streamo', 'lyte'],
    'recs': ['recs'],
    'showco': ['showco'],
    'streamo': ['streamo'],
    'lyte': ['lyte'],
}
