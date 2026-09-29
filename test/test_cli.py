from __future__ import annotations

import gc
import sys
import unittest
from io import StringIO
from logging import getLogger
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import tyro

from showco import cli


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.error_log = Path(directory.name) / 'showco-errors.txt'
        log_patch = patch.object(cli, 'ERROR_LOG_PATH', self.error_log)
        log_patch.start()
        self.addCleanup(log_patch.stop)

    def test_web_ui_options_accept_existing_flags(self) -> None:
        options = tyro.cli(
            cli.WebUiOptions,
            args=['--host', '0.0.0.0', '--port', '17352', '--rehearsal'],
        )

        self.assertEqual(options.host, '0.0.0.0')
        self.assertEqual(options.port, 17_352)
        self.assertTrue(options.rehearsal_mode)

    def test_dispatches_deploy_subcommand(self) -> None:
        with patch.object(cli.deploy, 'main', return_value=7) as deploy:
            self.assertEqual(cli.main(['deploy', 'recs']), 7)

        deploy.assert_called_once_with(['recs'])

    def test_failed_command_displays_and_saves_only_latest_diagnostics(self) -> None:
        terminal = StringIO()
        errors = StringIO()
        calls = 0

        def fail(arguments: list[str]) -> int:
            nonlocal calls
            calls += 1
            print(f'recs tests failed {calls}')
            print('FAILED test_audio', file=sys.stderr)
            getLogger('showco.test').error('recording stopped unexpectedly')
            return 1

        with (
            patch.object(cli.deploy, 'main', side_effect=fail),
            patch('sys.stdout', terminal),
            patch('sys.stderr', errors),
        ):
            self.assertEqual(cli.main(['deploy']), 1)
            self.assertEqual(cli.main(['deploy']), 1)

        self.assertEqual(
            terminal.getvalue(), 'recs tests failed 1\nrecs tests failed 2\n'
        )
        self.assertEqual(errors.getvalue(), 'FAILED test_audio\n' * 2)
        contents = self.error_log.read_text()
        self.assertNotIn('recs tests failed 1', contents)
        self.assertIn('recs tests failed 2', contents)
        self.assertEqual(contents.count('FAILED test_audio'), 1)
        self.assertEqual(contents.count('recording stopped unexpectedly'), 1)

    def test_success_removes_previous_error_log_before_dispatch(self) -> None:
        self.error_log.write_text('old failure')

        def succeed(arguments: list[str]) -> int:
            self.assertFalse(self.error_log.exists())
            return 0

        with patch.object(cli.deploy, 'main', side_effect=succeed):
            self.assertEqual(cli.main(['deploy']), 0)

        self.assertFalse(self.error_log.exists())

    def test_exit_message_is_saved_without_changing_exit(self) -> None:
        with patch.object(cli.deploy, 'main', side_effect=SystemExit('bad option')):
            with self.assertRaisesRegex(SystemExit, 'bad option'):
                cli.main(['deploy'])

        self.assertIn('bad option', self.error_log.read_text())

    def test_control_c_prints_short_error_and_exits_abnormally(self) -> None:
        errors = StringIO()
        with (
            patch.object(cli.deploy, 'main', side_effect=KeyboardInterrupt),
            patch('sys.stderr', errors),
        ):
            self.assertEqual(cli.main(['deploy']), 130)

        self.assertEqual(errors.getvalue(), 'Interrupted\n')
        contents = self.error_log.read_text()
        self.assertIn('Interrupted\n', contents)
        self.assertNotIn('Traceback', contents)
        self.assertNotIn('KeyboardInterrupt', contents)

    def test_web_ui_closes_server_on_control_c(self) -> None:
        with (
            patch.object(cli.gui_schema, 'configure_gui'),
            patch.object(cli.machine_role, 'require_target_machine'),
            patch.object(cli, 'load_mixer_specs', return_value=[]),
            patch.object(cli, 'make_server') as make_server,
        ):
            make_server.return_value.serve_forever.side_effect = KeyboardInterrupt
            with self.assertRaises(KeyboardInterrupt):
                cli.run_web_ui(cli.WebUiOptions())

        make_server.return_value.server_close.assert_called_once_with()

    def test_expected_deploy_failure_has_no_cleanup_error(self) -> None:
        with (
            patch.object(cli.deploy, 'main', side_effect=SystemExit('not ready')),
            patch('sys.unraisablehook') as unraisable,
        ):
            with self.assertRaisesRegex(SystemExit, 'not ready'):
                cli.main(['deploy'])
            gc.collect()

        unraisable.assert_not_called()

    def test_unhandled_exception_is_saved_without_changing_exit(self) -> None:
        with patch.object(cli.deploy, 'main', side_effect=ValueError('broken state')):
            with self.assertRaisesRegex(ValueError, 'broken state'):
                cli.main(['deploy'])

        self.assertIn('ValueError: broken state', self.error_log.read_text())

    def test_dispatches_deploy_without_a_subcommand(self) -> None:
        with patch.object(cli.deploy, 'main', return_value=7) as deploy:
            self.assertEqual(cli.main([]), 7)

        deploy.assert_called_once_with([])

    def test_dispatches_push_flag_without_deploy_subcommand(self) -> None:
        with patch.object(cli.deploy, 'main', return_value=7) as deploy:
            self.assertEqual(cli.main(['--push', 'recs']), 7)

        deploy.assert_called_once_with(['--push', 'recs'])

    def test_dispatches_sync_flag_without_deploy_subcommand(self) -> None:
        with patch.object(cli.deploy, 'main', return_value=7) as deploy:
            self.assertEqual(cli.main(['--sync', 'reccy']), 7)

        deploy.assert_called_once_with(['--sync', 'reccy'])

    def test_dispatches_deploy_options_before_local_mode_flag(self) -> None:
        arguments = ['--autosquash', '0', '--push', 'recs']
        with patch.object(cli.deploy, 'main', return_value=7) as deploy:
            self.assertEqual(cli.main(arguments), 7)

        deploy.assert_called_once_with(arguments)

    def test_dispatches_prepare_card_subcommand(self) -> None:
        with patch.object(cli.card, 'main', return_value=7) as prepare_card:
            self.assertEqual(cli.main(['prepare-card', '--boot', '/Volumes/bootfs']), 7)

        prepare_card.assert_called_once_with(['--boot', '/Volumes/bootfs'])

    def test_dispatches_cable_test_subcommand(self) -> None:
        with patch.object(cli.cable_test, 'main', return_value=7) as cable_test:
            self.assertEqual(cli.main(['cable-test', '9-14', '1-6']), 7)

        cable_test.assert_called_once_with(['9-14', '1-6'])

    def test_dispatches_streamo_subcommand(self) -> None:
        with (
            patch.object(cli.machine_role, 'require_target_machine'),
            patch.object(cli.auth, 'main', return_value=7) as streamo,
        ):
            self.assertEqual(cli.main(['streamo', '--help']), 7)

        streamo.assert_called_once_with(['--help'])

    def test_dispatches_logs_subcommand(self) -> None:
        with patch.object(cli.logs, 'main', return_value=7) as logs:
            self.assertEqual(cli.main(['logs', '--lines=50', 'recs']), 7)

        logs.assert_called_once_with(['--lines=50', 'recs'])

    def test_dispatches_python_subcommand(self) -> None:
        with patch.object(cli.python, 'main', return_value=7) as python:
            self.assertEqual(cli.main(['python', 'print(1)']), 7)

        python.assert_called_once_with(['print(1)'])

    def test_rejects_unknown_subcommand(self) -> None:
        self.assertEqual(cli.main(['unknown']), 2)

    def test_rejects_removed_subcommands(self) -> None:
        self.assertEqual(cli.main(['go']), 2)
        self.assertEqual(cli.main(['update']), 2)
        self.assertEqual(cli.main(['provision']), 2)


if __name__ == '__main__':
    unittest.main()
