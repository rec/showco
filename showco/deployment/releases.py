"""Plan releases for the same dependency graph used for deployment."""

import re
from pathlib import Path
from shutil import which
from subprocess import run
from typing import TextIO

from pydantic import BaseModel

from ..provision.config import read_toml
from . import local_update, update


class ReleasePlan(BaseModel, frozen=True):
    program: update.Program
    head: str
    version: str
    next_version: str | None = None


def plan_releases(
    selected: list[str],
    root: Path,
    run_command: update.RunCommand,
) -> list[ReleasePlan]:
    plans: dict[str, ReleasePlan] = {}
    for program in local_update.dependency_programs(selected, root):
        version = project_version(program.directory)
        head = git_output(program, ['log', '-1', '--format=%H%x00%s'], run_command)
        revision, separator, subject = head.partition('\0')
        if not revision or not separator:
            raise ValueError(f'{program.name}: could not read the latest commit')
        next_version = None
        if subject != f'Release v{version}' or has_new_dependency_release(
            program, plans
        ):
            major, minor, _ = map(int, version.split('.'))
            next_version = f'{major}.{minor + 1}.0'
        plans[program.name] = ReleasePlan(
            program=program,
            head=revision,
            version=version,
            next_version=next_version,
        )
    return list(plans.values())


def preview(plans: list[ReleasePlan], output: TextIO) -> None:
    for item in plans:
        proposed = item.next_version or 'unchanged'
        print(f'{item.program.name}: {item.version} -> {proposed}', file=output)


def publish(
    plans: list[ReleasePlan], root: Path, run_command: update.RunCommand, output: TextIO
) -> bool:
    candidates = [p for p in plans if p.next_version is not None]
    if not candidates:
        print('No projects need a release.', file=output)
        return True
    if (script := which('release.py')) is None:
        raise ValueError('release.py must be available on PATH')
    for item in candidates:
        if git_output(item.program, ['status', '--porcelain'], run_command):
            raise ValueError(
                f'{item.program.name}: release.py requires a clean working tree '
                'including untracked files'
            )
    for item in candidates:
        program = item.program
        dependencies = local_update.github_source_packages(program)
        if dependencies:
            refresh = local_update.dependency_refresh_needed(
                program, dependencies, root, run_command
            )
            if isinstance(refresh, update.StepResult):
                update.report_failure(refresh, output)
                return False
            if refresh:
                result = local_update.refresh_program_dependencies(
                    program, dependencies, run_command, output
                )
                if isinstance(result, update.StepResult):
                    update.report_failure(result, output)
                    return False
                if result == local_update.DependencyRefresh.FAILED:
                    return False
        require_current_dependency_pins(program, root, run_command)
        print(
            f'Releasing {program.name} {item.version} -> {item.next_version}',
            file=output,
            flush=True,
        )
        result = run([script, 'minor', '--publish'], cwd=program.directory, check=False)
        if result.returncode:
            print(
                f'ERROR: {program.name} release failed (exit {result.returncode})',
                file=output,
            )
            return False
        if project_version(program.directory) != item.next_version:
            raise ValueError(
                f'{program.name}: release.py did not set version {item.next_version}'
            )
    return True


def project_version(directory: Path) -> str:
    project = read_toml(directory / 'pyproject.toml').get('project')
    if not isinstance(project, dict):
        raise ValueError(f'{directory}: pyproject.toml is missing its project table')
    version = project.get('version')
    if 'version' in project.get('dynamic', []):
        cargo = read_toml(directory / 'Cargo.toml').get('package')
        version = cargo.get('version') if isinstance(cargo, dict) else None
    if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError(f'{directory}: expected a major.minor.patch project version')
    return version


def has_new_dependency_release(
    program: update.Program, plans: dict[str, ReleasePlan]
) -> bool:
    sources = local_update.github_sources(program)
    if any(
        n is not None and plans[n].next_version is not None for n in sources.values()
    ):
        return True
    if not any(n is not None for n in sources.values()):
        return False
    locked = local_update.locked_dependency_sources(program, list(sources))
    return any(
        n is not None
        and local_update.locked_git_revision(locked.get(p, '')) != plans[n].head
        for p, n in sources.items()
    )


def require_current_dependency_pins(
    program: update.Program, root: Path, run_command: update.RunCommand
) -> None:
    sources = local_update.github_sources(program)
    if not any(n is not None for n in sources.values()):
        return
    locked = local_update.locked_dependency_sources(program, list(sources))
    for package, project in sources.items():
        if project is None:
            continue
        dependency = local_update.dependency_program(project, root)
        head = git_output(dependency, ['rev-parse', 'HEAD'], run_command)
        if local_update.locked_git_revision(locked.get(package, '')) != head:
            raise ValueError(
                f'{program.name}: uv.lock does not pin the current {project} release'
            )


def git_output(
    program: update.Program, arguments: list[str], run_command: update.RunCommand
) -> str:
    result = update.run_step(
        program.name,
        'release preflight',
        ['git', '-C', str(program.directory), *arguments],
        run_command,
    )
    if not result.ok:
        raise ValueError(f'{program.name}: {result.output.strip()}')
    return result.output.strip()
