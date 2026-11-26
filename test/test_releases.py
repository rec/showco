from collections.abc import Sequence
from io import StringIO
from pathlib import Path
from subprocess import CompletedProcess
from unittest import mock

import pytest
from pydantic import RootModel
from reccy.configuration.settings import save, write_text_atomically

from showco.deployment import deploy, local_update, releases, update


def project(root: Path, name: str, pins: dict[str, str]) -> None:
    directory = root / name
    directory.mkdir()
    origins = {
        n: {'git': f'https://github.com/rec/{n}.git', 'branch': 'main'} for n in pins
    }
    save(
        directory / 'pyproject.toml',
        RootModel(
            {
                'project': {'name': name, 'version': '1.2.3'},
                'tool': {'uv': {'sources': origins}},
            }
        ),
    )
    write_text_atomically(
        directory / 'uv.lock',
        ''.join(
            f'[[package]]\nname = "{n}"\n'
            f'source = {{ git = "https://github.com/rec/{n}.git?branch=main#{r}" }}\n'
            for n, r in pins.items()
        ),
    )


def test_release_plan_propagates_new_and_pending_releases_in_dependency_order(
    tmp_path: Path,
) -> None:
    project(tmp_path, 'reccy', {})
    project(tmp_path, 'dvice', {'reccy': 'new-reccy-release'})
    project(
        tmp_path, 'recs', {'reccy': 'old-reccy-release', 'dvice': 'new-dvice-release'}
    )
    project(tmp_path, 'streamo', {'recs': 'old-recs-release'})
    project(tmp_path, 'showco', {'streamo': 'old-streamo-release'})

    def git(command: Sequence[str]) -> CompletedProcess[str]:
        name = Path(command[2]).name
        subject = 'Fix recent recording bug' if name == 'streamo' else 'Release v1.2.3'
        return CompletedProcess(command, 0, f'new-{name}-release\0{subject}\n', '')

    plans = releases.plan_releases(['showco'], tmp_path, git)
    assert [(p.program.name, p.next_version) for p in plans] == [
        ('reccy', None),
        ('dvice', None),
        ('recs', '1.3.0'),
        ('streamo', '1.3.0'),
        ('showco', '1.3.0'),
    ]


def test_release_only_publishes_and_stops_before_deployment() -> None:
    operations = mock.Mock()
    with (
        mock.patch.object(deploy.machine_role, 'require_provisioning_machine'),
        mock.patch.object(deploy.provision, 'resolved_config') as resolve,
        mock.patch.object(
            local_update, 'prepare_local_repositories', return_value=True
        ) as prepare,
        mock.patch.object(releases, 'plan_releases', return_value=[]) as plan,
        mock.patch.object(releases, 'publish', return_value=True) as publish,
    ):
        operations.attach_mock(prepare, 'prepare')
        operations.attach_mock(plan, 'plan')
        operations.attach_mock(publish, 'publish')
        assert deploy.main(['--release-only']) == 0
    assert [c[0] for c in operations.mock_calls] == ['prepare', 'plan', 'publish']
    resolve.assert_not_called()


def test_release_dry_prints_versions_without_preparing_repos_or_pi(
    capsys: pytest.CaptureFixture[str],
) -> None:
    planned = releases.ReleasePlan(
        program=update.Program(name='reccy', directory=Path('reccy'), service_names=[]),
        head='version',
        version='1.2.3',
        next_version='1.3.0',
    )
    with (
        mock.patch.object(deploy.machine_role, 'require_provisioning_machine'),
        mock.patch.object(deploy.provision, 'resolved_config') as resolve,
        mock.patch.object(local_update, 'prepare_local_repositories') as prepare,
        mock.patch.object(releases, 'plan_releases', return_value=[planned]),
        mock.patch.object(releases, 'publish') as publish,
    ):
        assert deploy.main(['--release-dry']) == 0
    assert capsys.readouterr().out == 'reccy: 1.2.3 -> 1.3.0\n'
    prepare.assert_not_called()
    publish.assert_not_called()
    resolve.assert_not_called()


def test_release_continues_to_deployment_after_publishing() -> None:
    options = deploy.provision.DeployOptions(repositories=['showco'], release=True)
    with (
        mock.patch.object(deploy.machine_role, 'require_provisioning_machine'),
        mock.patch.object(deploy, 'prepare_releases', return_value=0) as prepare,
        mock.patch.object(deploy.provision, 'resolved_config') as resolve,
        mock.patch.object(
            local_update, 'update_from_provisioning_machine', return_value=0
        ) as install,
    ):
        assert deploy.run(options) == 0
    prepare.assert_called_once_with(options, ['showco'])
    assert install.call_args.kwargs['target_config'] is resolve.return_value
    assert install.call_args.kwargs['repositories_prepared'] is True


def test_release_options_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit, match='choose one of --release'):
        deploy.main(['--release-only', '--release-dry'])


def test_release_stops_after_publisher_failure(tmp_path: Path) -> None:
    program = update.Program(name='reccy', directory=tmp_path, service_names=[])
    first = releases.ReleasePlan(
        program=program, head='old', version='1.0.0', next_version='1.1.0'
    )
    output = StringIO()
    with (
        mock.patch.object(releases, 'which', return_value='/tools/release.py'),
        mock.patch.object(releases, 'git_output', return_value=''),
        mock.patch.object(
            releases, 'run', return_value=CompletedProcess([], 3)
        ) as publisher,
    ):
        assert not releases.publish([first, first], tmp_path, mock.Mock(), output)
    publisher.assert_called_once_with(
        ['/tools/release.py', 'minor', '--publish'], cwd=tmp_path, check=False
    )
    assert 'reccy release failed (exit 3)' in output.getvalue()


def test_release_checks_all_candidate_worktrees_before_publication(
    tmp_path: Path,
) -> None:
    plans = [
        releases.ReleasePlan(
            program=update.Program(name=n, directory=tmp_path / n, service_names=[]),
            head='old',
            version='1.0.0',
            next_version='1.1.0',
        )
        for n in ('reccy', 'showco')
    ]
    with (
        mock.patch.object(releases, 'which', return_value='/tools/release.py'),
        mock.patch.object(releases, 'git_output', side_effect=['', '?? local-media/']),
        mock.patch.object(releases, 'run') as publisher,
        pytest.raises(ValueError, match='showco: .* including untracked files'),
    ):
        releases.publish(plans, tmp_path, mock.Mock(), StringIO())
    publisher.assert_not_called()
