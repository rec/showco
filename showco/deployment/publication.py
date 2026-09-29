from __future__ import annotations

from typing import TextIO

from pydantic import BaseModel
from tqdm import tqdm

from . import update


class PublicationState(BaseModel, frozen=True):
    program: update.Program
    remote: str
    branch: str
    upstream_commit: str
    rewritten: bool = False


def publication_state(
    program: update.Program, run_command: update.RunCommand
) -> PublicationState | update.StepResult:
    upstream = update.run_step(
        program.name,
        'upstream',
        [
            'git',
            '-C',
            str(program.directory),
            'rev-parse',
            '--abbrev-ref',
            '--symbolic-full-name',
            '@{upstream}',
        ],
        run_command,
    )
    if not upstream.ok:
        return upstream
    remote, _, branch = upstream.output.strip().partition('/')
    if not remote or not branch:
        return update.StepResult(
            program=program.name,
            step='push',
            command=[],
            returncode=2,
            output=f'bad upstream {upstream.output.strip()}',
        )
    commit = update.run_step(
        program.name,
        'upstream commit',
        ['git', '-C', str(program.directory), 'rev-parse', '@{upstream}'],
        run_command,
    )
    if not commit.ok:
        return commit
    if not (upstream_commit := commit.output.strip()):
        return update.StepResult(
            program=program.name,
            step='upstream commit',
            command=commit.command,
            returncode=2,
            output='upstream commit is empty',
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
        'local commit',
        ['git', '-C', str(program.directory), 'rev-parse', 'HEAD'],
        run_command,
    )
    if not local_commit.ok:
        return local_commit
    if not state.rewritten and local_commit.output.strip() == state.upstream_commit:
        return update.StepResult(
            program=program.name,
            step='push',
            command=local_commit.command,
            returncode=0,
            output='already published',
        )
    push = normal_push_step(program, state.remote, state.branch, run_command)
    if push.ok or not state.rewritten:
        return push
    if output:
        tqdm.write(f'{program.name} regular push rejected', file=output)
        tqdm.write(
            f'{program.name} pre-autosquash upstream commit: {state.upstream_commit}',
            file=output,
        )
    force_push = update.run_step(
        program.name,
        'push --force-with-lease',
        [
            'git',
            '-C',
            str(program.directory),
            'push',
            f'--force-with-lease=refs/heads/{state.branch}:{state.upstream_commit}',
            state.remote,
            f'HEAD:{state.branch}',
        ],
        run_command,
    )
    if force_push.ok:
        if output:
            tqdm.write(f'{program.name} push --force-with-lease: ok', file=output)
        return force_push
    return update.StepResult(
        program=program.name,
        step=force_push.step,
        command=force_push.command,
        returncode=force_push.returncode,
        output=(
            f'regular push failed:\n{push.output.rstrip()}\n'
            f'force push failed:\n{force_push.output.rstrip()}'
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
        'push',
        [
            'git',
            '-C',
            str(program.directory),
            'push',
            remote,
            f'HEAD:{branch}',
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
        result_states.append(state.model_copy(update={'rewritten': True}))
    return result_states


def autosquash_program(
    program: update.Program, limit: int, run_command: update.RunCommand
) -> update.StepResult | None:
    recent_commits = update.run_step(
        program.name,
        'recent commits',
        [
            'git',
            '-C',
            str(program.directory),
            'log',
            '-n',
            str(limit),
            '--format=%H%x00%s%x00',
            'HEAD',
        ],
        run_command,
    )
    if not recent_commits.ok:
        return recent_commits
    if missing_targets := missing_fixup_targets(recent_commits.output):
        return update.StepResult(
            program=program.name,
            step='validate fixup targets',
            command=recent_commits.command,
            returncode=1,
            output=(
                'fixup target is not in the selected autosquash history: '
                + ', '.join(missing_targets)
                + f'\nIncrease --autosquash (now {limit})?'
                + '\nNo rebase was started.'
            ),
        )
    if (fixup_commit := oldest_fixup_commit(recent_commits.output)) is None:
        return None
    parent = update.run_step(
        program.name,
        'fixup parent',
        ['git', '-C', str(program.directory), 'rev-parse', f'{fixup_commit}^'],
        run_command,
    )
    if not parent.ok:
        return parent
    return update.run_step(
        program.name,
        'autosquash',
        [
            'git',
            '-C',
            str(program.directory),
            'rebase',
            '--interactive',
            '--autosquash',
            parent.output.strip(),
        ],
        run_command,
    )


def oldest_fixup_commit(output: str) -> str | None:
    fixups = [
        commit
        for commit, subject in log_commits(output)
        if subject.startswith('fixup! ')
    ]
    return fixups[-1] if fixups else None


def missing_fixup_targets(output: str) -> list[str]:
    subjects = {subject for _, subject in log_commits(output)}
    return [
        subject.removeprefix('fixup! ')
        for _, subject in log_commits(output)
        if subject.startswith('fixup! ')
        and subject.removeprefix('fixup! ') not in subjects
    ]


def log_commits(output: str) -> list[tuple[str, str]]:
    values = output.split('\0')
    return [
        (commit.strip(), subject.strip())
        for commit, subject in zip(values[::2], values[1::2], strict=False)
    ]
