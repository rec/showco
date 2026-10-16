from __future__ import annotations

import subprocess
import unittest
from collections.abc import Sequence
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from reccy.services.models import Platform, StatusResult
from update_helpers import make_config

from showco.deployment import bootstrap, update


class UpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        ensure_log = mock.patch(
            'reccy.services.controller.ServiceController._ensure_log'
        )
        ensure_log.start()
        self.addCleanup(ensure_log.stop)

    def test_target_update_configuration_does_not_require_secrets(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'config.toml').write_text(
                '[network]\nhost = "show-pi.local"\nuser = "performer"\n'
                '[stream]\nenabled = false\n'
                '[networks.internal]\nsubnet = "10.0.0.0/24"\n'
            )
            with (
                mock.patch('showco.deployment.update.provision.PROVISION_DIR', path),
                mock.patch(
                    'showco.deployment.update.machine_role.machine_role',
                    return_value='target',
                ),
            ):
                configuration = update.provisioning_config()
            self.assertEqual(configuration.ssh_target, 'performer@show-pi.local')
            self.assertFalse(configuration.stream.enabled)

    def test_development_configuration_still_requires_secrets(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'config.toml').write_text('[network]\nhost = "show-pi.local"\n')
            with (
                mock.patch('showco.deployment.update.provision.PROVISION_DIR', path),
                mock.patch(
                    'showco.deployment.update.machine_role.machine_role',
                    return_value='',
                ),
            ):
                with self.assertRaisesRegex(SystemExit, 'secrets file not found'):
                    update.provisioning_config()

    def test_remote_update_reports_target_before_ssh(self) -> None:
        output = StringIO()
        with mock.patch(
            'showco.deployment.update.run_remote_step',
            return_value=update.StepResult(
                program='target',
                step='update',
                command=['ssh'],
                returncode=0,
                output='',
            ),
        ):
            result = update.update_remote_target(
                ['recs'], target_config=make_config(), output=output
            )

        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue(), 'Updating tom@bertrand.local from GitHub\n')

    def test_streamo_install_uses_streamo_environment(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            return subprocess.CompletedProcess(command, 0, '', '')

        result = update.install_streamo_service(Path('/code'), 'tom', run_command)

        self.assertTrue(result.ok)
        self.assertEqual(
            commands,
            [
                [
                    'sh',
                    '-c',
                    'cd /code/streamo && uv run --locked streamo daemon install '
                    '--config /home/tom/.config/streamo/config.toml',
                ]
            ],
        )

    def test_showco_refresh_uses_its_installed_environment(self) -> None:
        with mock.patch(
            'showco.deployment.update.services.refresh_service_definition',
            return_value=StatusResult(installed=True, running=True),
        ) as refresh:
            result = update.start_or_refresh_service_step(
                'showco', True, Path('/srv/show projects'), make_config(), mock.Mock()
            )
        self.assertTrue(result.ok)
        self.assertEqual(
            refresh.call_args.kwargs['executable'],
            Path('/srv/show projects/showco/.venv/bin/python'),
        )

    def test_service_stop_accepts_reccy_runner_timeout(self) -> None:
        with (
            mock.patch(
                'reccy.services.controller.current_platform',
                return_value=Platform.linux,
            ),
            mock.patch(
                'showco.deployment.update.subprocess.run',
                return_value=subprocess.CompletedProcess(['systemctl'], 0, '', ''),
            ) as run,
        ):
            result = update.run_service_step(
                'recs', 'stop', update.run_command_with_timeout
            )
        self.assertTrue(result.ok)
        stop = run.call_args_list[0]
        self.assertEqual(stop.args[0], ['systemctl', '--user', 'stop', 'recs.service'])
        self.assertEqual(stop.kwargs['timeout'], 30)

    def test_service_stop_reports_failure_and_timeout_without_traceback(self) -> None:
        for failure, status, message in (
            (
                subprocess.CompletedProcess(['systemctl'], 1, '', 'stop failed'),
                1,
                'stop failed',
            ),
            (subprocess.TimeoutExpired(['systemctl'], 30), 124, 'timed out after 30'),
        ):
            with self.subTest(status=status):
                with (
                    mock.patch(
                        'reccy.services.controller.current_platform',
                        return_value=Platform.linux,
                    ),
                    mock.patch('showco.deployment.update.subprocess.run') as run,
                ):
                    if isinstance(failure, subprocess.TimeoutExpired):
                        run.side_effect = failure
                    else:
                        run.return_value = failure
                    result = update.run_service_step(
                        'recs', 'stop', update.run_command_with_timeout
                    )
                self.assertEqual(result.returncode, status)
                self.assertIn(message, result.output)

    def test_dependency_sync_has_time_to_install_and_uses_its_own_environment(
        self,
    ) -> None:
        command = ['uv', 'sync', '--locked', '--directory', '/home/tom/code/streamo']
        with (
            mock.patch.dict('os.environ', {'VIRTUAL_ENV': '/tmp/updater/.venv'}),
            mock.patch(
                'showco.deployment.update.subprocess.run',
                return_value=subprocess.CompletedProcess(command, 0, '', ''),
            ) as run,
        ):
            update.run_command_with_timeout(command)
        self.assertEqual(run.call_args.kwargs['timeout'], 600)
        self.assertNotIn('VIRTUAL_ENV', run.call_args.kwargs['env'])
        self.assertEqual(update.timeout(['git', 'fetch']), 120)
        self.assertEqual(update.timeout(['systemctl', '--user', 'stop', 'recs']), 30)

    def test_timeout_reports_subprocess_output_as_text(self) -> None:
        command = ['uv', 'sync', '--locked']
        runner = mock.Mock(
            side_effect=subprocess.TimeoutExpired(
                command, 600, output=b'Resolved packages\n', stderr=b'Downloading Qt\n'
            )
        )
        result = update.run_step('streamo', 'sync dependencies', command, runner)
        self.assertEqual(result.returncode, 124)
        self.assertEqual(
            result.output,
            'command timed out after 600 seconds\nResolved packages\nDownloading Qt\n',
        )

    def test_run_command_uses_noninteractive_editor_for_rebase(self) -> None:
        command = ['git', '-C', '/code/recs', 'rebase', '--interactive']
        with mock.patch(
            'showco.deployment.update.subprocess.run',
            return_value=subprocess.CompletedProcess(command, 0, '', ''),
        ) as run:
            update.run_command_with_timeout(command)

        environment = run.call_args.kwargs['env']
        self.assertEqual(environment['GIT_SEQUENCE_EDITOR'], ':')
        self.assertEqual(environment['GIT_EDITOR'], ':')

    def test_remote_step_reports_remote_output(self) -> None:
        command = ['ssh', 'tom@bertrand.local', 'showco deploy']
        with mock.patch(
            'showco.deployment.update.run',
            return_value=subprocess.CompletedProcess(
                command,
                1,
                'target output\n',
                'target error\n',
            ),
        ):
            result = update.run_remote_step('target', 'update', command)

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.output, 'target output\ntarget error\n')

    def test_remote_update_sends_standalone_bootstrap(self) -> None:
        command = ['ssh', 'tom@bertrand.local', 'update command']
        with mock.patch(
            'showco.deployment.update.run',
            return_value=subprocess.CompletedProcess(command, 0, '', ''),
        ) as run:
            result = update.run_remote_step('target', 'update', command)

        self.assertTrue(result.ok)
        source = run.call_args.kwargs['input']
        self.assertEqual(source, Path(bootstrap.__file__).read_text())
        self.assertEqual(run.call_args.kwargs['timeout'], 3600)

    def test_showco_revision_step_checks_running_web_ui(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            return subprocess.CompletedProcess(command, 0, '', '')

        result = update.showco_revision_step(Path('/code'), 17352, run_command)

        self.assertTrue(result.ok)
        self.assertEqual(
            commands,
            [
                [
                    'sh',
                    '-c',
                    'expected=$(git -C /code/showco rev-parse HEAD) && '
                    'curl --fail --silent --show-error --retry 5 --retry-connrefused '
                    '--retry-delay 1 http://127.0.0.1:17352/status | '
                    'grep --fixed-strings "\\"revision\\":\\"$expected\\""',
                ]
            ],
        )

    def test_selected_repositories_defaults_to_all(self) -> None:
        self.assertEqual(update.selected_repositories([]), update.REPOSITORY_NAMES)

    def test_reccy_selection_includes_all_consumers_in_dependency_order(self) -> None:
        self.assertEqual(
            update.selected_repositories(['reccy']),
            ['reccy', 'recs', 'streamo', 'lyte', 'showco'],
        )

    def test_recs_selection_includes_showco(self) -> None:
        self.assertEqual(update.selected_repositories(['recs']), ['recs', 'showco'])

    def test_disabled_streamo_has_no_service_to_restart(self) -> None:
        programs = update.programs_for_repositories(
            ['reccy', 'streamo'], Path('/code'), streamo_enabled=False
        )

        self.assertNotIn('streamo', programs[0].service_names)
        self.assertEqual(programs[1].service_names, [])

    def test_disabled_lyte_has_no_service_to_restart(self) -> None:
        programs = update.programs_for_repositories(
            ['reccy', 'lyte'], Path('/code'), lyte_enabled=False
        )

        self.assertNotIn('lyte', programs[0].service_names)
        self.assertEqual(programs[1].service_names, [])

    def test_selected_repositories_rejects_unknown_names(self) -> None:
        with self.assertRaisesRegex(SystemExit, 'unknown update target'):
            update.selected_repositories(['bogus'])
