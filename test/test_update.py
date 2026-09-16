from __future__ import annotations

import subprocess
import unittest
from collections.abc import Sequence
from io import StringIO
from pathlib import Path
from unittest import mock

from update_helpers import make_config

from showco.deployment import update


class UpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        ensure_log = mock.patch(
            'reccy.services.controller.ServiceController._ensure_log'
        )
        ensure_log.start()
        self.addCleanup(ensure_log.stop)

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
        command = ['ssh', 'tom@bertrand.local', 'showco go']
        with mock.patch(
            'showco.deployment.update.subprocess.run',
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
