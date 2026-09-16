from collections.abc import Sequence
from io import StringIO
from pathlib import Path
from subprocess import CompletedProcess
from unittest import mock

import pytest

from showco.deployment import target_update, update


class Target:
    def __init__(self) -> None:
        self.revisions = {n: f'old-{n}' for n in update.REPOSITORY_NAMES}
        self.environments = dict(self.revisions)
        self.commands: list[list[str]] = []
        self.running = {'recs', 'showco', 'streamo', 'lyte'}
        self.failure = ''
        self.failed = False
        self.rollback_failure = False
        self.dirty = ''
        self.branch = 'main'

    def run(self, command: Sequence[str]) -> CompletedProcess[str]:
        command = list(command)
        self.commands.append(command)
        operation = ''
        name = ''
        stdout = ''
        if command[0] == 'git':
            name = Path(command[2]).name
            operation = command[3]
            if operation == 'branch':
                stdout = self.branch
            elif operation == 'status':
                stdout = self.dirty
            elif operation == 'rev-parse':
                stdout = (
                    self.revisions[name] if command[-1] == 'HEAD' else f'new-{name}'
                )
        elif command[:2] == ['uv', 'sync']:
            operation, name = 'sync', Path(command[-1]).name
        elif command[0] == 'systemctl':
            operation, name = command[2], command[3].removesuffix('.service')
        elif command[0] == 'sh':
            operation = 'health'
        if not self.failed and operation == self.failure:
            self.failed = True
            return CompletedProcess(command, 1, '', 'injected failure')
        if self.rollback_failure and operation == 'sync' and self.failed:
            return CompletedProcess(command, 1, '', 'recovery unavailable')
        if operation == 'reset':
            self.revisions[name] = command[-1]
        elif operation == 'sync':
            self.environments[name] = self.revisions[name]
        elif operation == 'stop':
            self.running.discard(name)
        elif operation == 'start':
            self.running.add(name)
        return CompletedProcess(command, 0, stdout, '')


@pytest.fixture
def target(monkeypatch: pytest.MonkeyPatch) -> Target:
    target = Target()
    configuration = mock.Mock(
        network=mock.Mock(user='tom', web_port=17352),
        stream=mock.Mock(enabled=True),
        lyte=mock.Mock(enabled=True),
    )
    monkeypatch.setattr(update, 'provisioning_config', lambda: configuration)

    def service(name: str, step: str, runner: update.RunCommand) -> update.StepResult:
        return update.run_step(
            name, step, ['systemctl', '--user', step, f'{name}.service'], runner
        )

    monkeypatch.setattr(update, 'run_service_step', service)
    return target


def deploy(target: Target, output: StringIO | None = None) -> int:
    return target_update.update_target(
        ['recs'],
        root=Path('/code'),
        run_command=target.run,
        output=output if output is not None else StringIO(),
    )


def test_success_updates_only_selected_repositories_and_consumers(
    target: Target,
) -> None:
    assert deploy(target) == 0
    assert target.revisions['recs'] == 'new-recs'
    assert target.revisions['showco'] == 'new-showco'
    assert target.revisions['lyte'] == 'old-lyte'
    assert target.revisions['streamo'] == 'old-streamo'
    assert target.environments == target.revisions
    assert target.running == {'recs', 'showco', 'streamo', 'lyte'}
    commands = target.commands
    first_stop = next(i for i, c in enumerate(commands) if 'stop' in c)
    last_fetch = max(i for i, c in enumerate(commands) if 'fetch' in c)
    first_reset = next(i for i, c in enumerate(commands) if 'reset' in c)
    last_stop = max(i for i, c in enumerate(commands) if 'stop' in c)
    assert last_fetch < first_stop <= last_stop < first_reset
    starts = [c[-1] for c in commands if 'start' in c]
    assert starts == ['recs.service', 'showco.service']


@pytest.mark.parametrize('failure', ['reset', 'sync', 'start', 'health'])
def test_failed_deployment_restores_all_versions_and_environments(
    target: Target, failure: str
) -> None:
    target.failure = failure
    output = StringIO()
    assert deploy(target, output) == 1
    assert target.revisions == {n: f'old-{n}' for n in update.REPOSITORY_NAMES}
    assert target.environments == target.revisions
    assert target.running == {'recs', 'showco', 'streamo', 'lyte'}
    assert 'Previous versions restored and services restarted.' in output.getvalue()


@pytest.mark.parametrize('failure', ['fetch', 'rev-parse'])
def test_preflight_failure_never_stops_services(target: Target, failure: str) -> None:
    target.failure = failure
    assert deploy(target) == 1
    assert not any('stop' in c or 'reset' in c for c in target.commands)


@pytest.mark.parametrize('dirty', [' M uv.lock', ' M module.py'])
def test_dirty_repository_does_not_interrupt_recording(
    target: Target, dirty: str
) -> None:
    target.dirty = dirty
    assert deploy(target) == 1
    assert not any(c[0] == 'systemctl' for c in target.commands)


def test_non_main_branch_does_not_interrupt_recording(target: Target) -> None:
    target.branch = 'work'
    assert deploy(target) == 1
    assert not any(c[0] == 'systemctl' for c in target.commands)


def test_untracked_files_are_preserved(target: Target) -> None:
    target.dirty = '?? recording.wav'
    assert deploy(target) == 0
    assert not any('clean' in c for c in target.commands)


def test_failed_stop_does_not_change_revisions(target: Target) -> None:
    target.failure = 'stop'
    assert deploy(target) == 1
    assert not any('reset' in c for c in target.commands)
    assert target.running == {'recs', 'showco', 'streamo', 'lyte'}


def test_failed_recovery_is_reported_and_does_not_start_broken_environments(
    target: Target,
) -> None:
    target.failure = 'sync'
    target.rollback_failure = True
    output = StringIO()
    assert deploy(target, output) == 1
    assert 'Rollback incomplete' in output.getvalue()
    assert target.running == {'lyte', 'streamo'}


def test_remote_command_does_not_mutate_checkouts_before_transaction() -> None:
    command = update.remote_update_command(
        ['recs'], Path('/srv/show projects'), clear_settings=False
    )
    assert command == (
        "cd '/srv/show projects/showco' && "
        'PATH="$HOME/.local/bin:$PATH" uv run --no-sync showco go '
        "--target-machine --root '/srv/show projects' recs"
    )


def test_explicit_settings_clear_is_restored_after_failed_update(
    target: Target, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configuration = update.provisioning_config()
    configuration.network.user = str(tmp_path)
    path = tmp_path / '.config/recs/settings.json'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"tracks": "operator settings"}\n')
    original = path.read_bytes()
    target.failure = 'sync'

    def clear(user: str, runner: update.RunCommand) -> update.StepResult:
        assert not {'recs', 'showco'} & target.running
        path.unlink()
        return update.StepResult(
            program='recs', step='clear', command=[], returncode=0, output=''
        )

    monkeypatch.setattr(update, 'clear_recs_settings_step', clear)
    assert (
        target_update.update_target(
            ['recs'],
            root=Path('/code'),
            run_command=target.run,
            output=StringIO(),
            clear_settings=True,
        )
        == 1
    )
    assert path.read_bytes() == original
    assert {'recs', 'showco'} <= target.running


def test_unrelated_update_does_not_clear_recs_settings(target: Target) -> None:
    with mock.patch.object(update, 'clear_recs_settings_step') as clear:
        assert (
            target_update.update_target(
                ['lyte'],
                root=Path('/code'),
                run_command=target.run,
                output=StringIO(),
                clear_settings=True,
            )
            == 0
        )
    clear.assert_not_called()
    assert all('/code/recs' not in c for c in target.commands)


def test_reccy_update_refreshes_enabled_services_after_sync(target: Target) -> None:
    with mock.patch.object(
        update,
        'start_or_refresh_service_step',
        side_effect=lambda name, refresh, root, configuration, runner: (
            update.run_service_step(name, 'start', runner)
        ),
    ) as restart:
        assert (
            target_update.update_target(
                ['reccy'], root=Path('/code'), run_command=target.run, output=StringIO()
            )
            == 0
        )
    assert {c.args[0] for c in restart.call_args_list} == {
        'recs',
        'streamo',
        'lyte',
        'showco',
    }
    assert all(c.args[1] for c in restart.call_args_list)
    assert target.environments == {n: f'new-{n}' for n in update.REPOSITORY_NAMES}
