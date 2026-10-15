from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import tyro

from showco.deployment import deploy, update
from showco.provision import provision, ssh


class DeployTests(unittest.TestCase):
    def setUp(self) -> None:
        scan = mock.patch(
            'showco.deployment.card.attached_imager_cards', return_value=[]
        )
        self.scan_cards = scan.start()
        self.addCleanup(scan.stop)

    def test_attached_card_is_prepared_before_waiting_and_provisioning(self) -> None:
        for previous_boot_id in [None, 'previous-boot']:
            with self.subTest(previous_boot_id=previous_boot_id):
                self.scan_cards.return_value = [Path('/dev/disk4')]
                configuration = mock.Mock(ssh_target='tom@pi.local')
                configuration.network.user = 'tom'
                calls = mock.Mock()
                with (
                    mock.patch(
                        'showco.provision.provision.resolved_config',
                        return_value=configuration,
                    ),
                    mock.patch(
                        'showco.provision.ssh.ssh_boot_id',
                        return_value=previous_boot_id,
                    ),
                    mock.patch(
                        'showco.deployment.card.prepare_selected_card'
                    ) as prepare,
                    mock.patch('showco.provision.ssh.wait_for_ssh') as wait,
                    mock.patch('showco.provision.ssh.wait_for_rebooted_ssh') as reboot,
                    mock.patch('showco.provision.ssh.run_ssh') as cloud_init,
                    mock.patch(
                        'showco.provision.provision.run', return_value=0
                    ) as provision_target,
                    mock.patch(
                        'showco.provision.remote.applied_provisioning_fingerprint'
                    ) as applied,
                ):
                    calls.attach_mock(prepare, 'prepare')
                    calls.attach_mock(wait, 'wait')
                    calls.attach_mock(reboot, 'reboot')
                    calls.attach_mock(cloud_init, 'cloud_init')
                    calls.attach_mock(provision_target, 'provision')
                    options = self.options()
                    self.assertEqual(deploy.run(options), 0)
                self.assertEqual(
                    calls.mock_calls,
                    [
                        mock.call.prepare(Path('/dev/disk4'), 'tom'),
                        mock.call.reboot(configuration, previous_boot_id)
                        if previous_boot_id
                        else mock.call.wait(
                            configuration, timeout_seconds=ssh.REBOOT_WAIT_SECONDS
                        ),
                        mock.call.cloud_init(
                            configuration,
                            'cloud-init status --wait --long',
                            timeout_seconds=ssh.REBOOT_WAIT_SECONDS,
                        ),
                        mock.call.provision(options, provision_config=configuration),
                    ],
                )
                applied.assert_not_called()

    def test_multiple_cards_stop_before_preparation_or_network_access(self) -> None:
        self.scan_cards.return_value = [Path('/dev/disk4'), Path('/dev/disk5')]
        with (
            mock.patch('showco.provision.provision.resolved_config'),
            mock.patch('showco.deployment.card.prepare_selected_card') as prepare,
            mock.patch('showco.provision.ssh.ssh_boot_id') as boot_id,
            self.assertRaisesRegex(SystemExit, 'multiple Imager-written'),
        ):
            deploy.run(self.options())
        prepare.assert_not_called()
        boot_id.assert_not_called()

    def test_card_cancellation_boot_timeout_or_cloud_init_failure_stops_provisioning(
        self,
    ) -> None:
        self.scan_cards.return_value = [Path('/dev/disk4')]
        for failed_step in ['prepare_selected_card', 'wait_for_ssh', 'run_ssh']:
            with (
                self.subTest(failed_step=failed_step),
                mock.patch('showco.provision.provision.resolved_config'),
                mock.patch('showco.provision.ssh.ssh_boot_id', return_value=None),
                mock.patch('showco.deployment.card.prepare_selected_card') as prepare,
                mock.patch('showco.provision.ssh.wait_for_ssh') as wait,
                mock.patch('showco.provision.ssh.run_ssh') as cloud_init,
                mock.patch('showco.provision.provision.run') as provision_target,
            ):
                steps = {
                    'prepare_selected_card': prepare,
                    'wait_for_ssh': wait,
                    'run_ssh': cloud_init,
                }
                steps[failed_step].side_effect = SystemExit('stopped')
                with self.assertRaisesRegex(SystemExit, 'stopped'):
                    deploy.run(self.options())
                provision_target.assert_not_called()
                if failed_step != 'run_ssh':
                    cloud_init.assert_not_called()
                if failed_step == 'prepare_selected_card':
                    wait.assert_not_called()

    def test_explicit_updates_do_not_scan_attached_cards(self) -> None:
        with (
            mock.patch('showco.provision.provision.resolved_config'),
            mock.patch(
                'showco.deployment.local_update.prepare_local_repositories',
                return_value=True,
            ),
            mock.patch(
                'showco.deployment.local_update.refresh_local_dependencies',
                return_value=True,
            ),
            mock.patch(
                'showco.deployment.local_update.update_from_provisioning_machine',
                return_value=0,
            ),
            mock.patch('showco.deployment.update.update_remote_target', return_value=0),
            mock.patch('showco.deployment.target_update.update_target', return_value=0),
        ):
            for values in [
                {'sync': True},
                {'push': True},
                {'remote': True},
                {'repositories': ['recs']},
                {'target_machine': True},
            ]:
                with self.subTest(values=values):
                    self.assertEqual(deploy.run(self.options(**values)), 0)
        self.scan_cards.assert_not_called()

    def test_audio_paths_check_provisioning_configuration_even_with_autosquash(
        self,
    ) -> None:
        provision_config = mock.Mock(ssh_target='tom@t.local')
        with (
            mock.patch(
                'showco.provision.provision.resolved_config',
                return_value=provision_config,
            ),
            mock.patch(
                'showco.provision.state.provisioning_fingerprint', return_value='new'
            ),
            mock.patch(
                'showco.provision.remote.applied_provisioning_fingerprint',
                return_value='old',
            ),
            mock.patch(
                'showco.provision.provision.run', return_value=0
            ) as provision_target,
            mock.patch(
                'showco.deployment.local_update.update_from_provisioning_machine'
            ) as update_target,
        ):
            options = self.options(audio=[Path('/mnt/audio')], autosquash=100)
            self.assertEqual(deploy.run(options), 0)
        provision_target.assert_called_once_with(
            options, provision_config=provision_config
        )
        update_target.assert_not_called()

    def options(
        self, *, upgrade: bool = False, **kwargs: object
    ) -> provision.DeployOptions:
        return provision.DeployOptions(
            config_path=Path('config.toml'),
            secrets=Path('secrets.toml'),
            upgrade=upgrade,
            **kwargs,
        )

    def test_main_prints_completion_after_success(self) -> None:
        options = self.options()
        with (
            mock.patch('showco.deployment.deploy.tyro.cli', return_value=options),
            mock.patch('showco.deployment.deploy.run', return_value=0),
            mock.patch('builtins.print') as print_message,
        ):
            result = deploy.main([])

        self.assertEqual(result, 0)
        print_message.assert_called_once_with('Successfully completed')

    def test_main_does_not_print_completion_after_failure(self) -> None:
        options = self.options()
        with (
            mock.patch('showco.deployment.deploy.tyro.cli', return_value=options),
            mock.patch('showco.deployment.deploy.run', return_value=1),
            mock.patch('builtins.print') as print_message,
        ):
            result = deploy.main([])

        self.assertEqual(result, 1)
        print_message.assert_not_called()

    def test_matching_configuration_updates_target(self) -> None:
        provision_config = mock.Mock(ssh_target='tom@bertrand.local')
        with (
            mock.patch(
                'showco.provision.provision.resolved_config',
                return_value=provision_config,
            ),
            mock.patch(
                'showco.provision.state.provisioning_fingerprint',
                return_value='fingerprint',
            ),
            mock.patch(
                'showco.provision.remote.applied_provisioning_fingerprint',
                return_value='fingerprint',
            ),
            mock.patch(
                'showco.deployment.local_update.update_from_provisioning_machine',
                return_value=0,
            ) as update_target,
            mock.patch('showco.provision.provision.run') as provision_target,
        ):
            result = deploy.run(self.options())

        self.assertEqual(result, 0)
        update_target.assert_called_once()
        self.assertEqual(update_target.call_args.args[0], update.REPOSITORY_NAMES)
        self.assertIs(update_target.call_args.kwargs['target_config'], provision_config)
        provision_target.assert_not_called()

    def test_missing_applied_state_provisions_target(self) -> None:
        provision_config = mock.Mock(ssh_target='tom@bertrand.local')
        with (
            mock.patch(
                'showco.provision.provision.resolved_config',
                return_value=provision_config,
            ),
            mock.patch(
                'showco.provision.state.provisioning_fingerprint',
                return_value='fingerprint',
            ),
            mock.patch(
                'showco.provision.remote.applied_provisioning_fingerprint',
                return_value=None,
            ),
            mock.patch(
                'showco.provision.provision.run', return_value=0
            ) as provision_target,
            mock.patch(
                'showco.deployment.local_update.update_from_provisioning_machine'
            ) as update_target,
        ):
            result = deploy.run(self.options())

        self.assertEqual(result, 0)
        provision_target.assert_called_once_with(
            self.options(), provision_config=provision_config
        )
        update_target.assert_not_called()

    def test_upgrade_option_forces_provisioning(self) -> None:
        provision_config = mock.Mock(ssh_target='tom@bertrand.local')
        with (
            mock.patch(
                'showco.provision.provision.resolved_config',
                return_value=provision_config,
            ),
            mock.patch(
                'showco.provision.remote.applied_provisioning_fingerprint'
            ) as applied,
            mock.patch(
                'showco.provision.provision.run', return_value=0
            ) as provision_target,
        ):
            result = deploy.run(self.options(upgrade=True))

        self.assertEqual(result, 0)
        applied.assert_not_called()
        provision_target.assert_called_once_with(
            self.options(upgrade=True), provision_config=provision_config
        )

    def test_selected_repositories_update_without_provisioning(self) -> None:
        provision_config = mock.Mock(ssh_target='tom@bertrand.local')
        options = self.options(repositories=['recs'])
        with (
            mock.patch(
                'showco.provision.provision.resolved_config',
                return_value=provision_config,
            ),
            mock.patch(
                'showco.deployment.local_update.update_from_provisioning_machine',
                return_value=0,
            ) as update_target,
            mock.patch('showco.provision.provision.run') as provision_target,
        ):
            result = deploy.run(options)

        self.assertEqual(result, 0)
        update_target.assert_called_once_with(
            ['recs', 'showco'],
            host=None,
            root=None,
            target_config=provision_config,
            output=mock.ANY,
            autosquash=50,
            clear_settings=False,
        )
        provision_target.assert_not_called()

    def test_push_prepares_local_repositories_only(self) -> None:
        options = self.options(push=True, repositories=['recs'])
        with (
            mock.patch(
                'showco.provision.provision.local_checkout_directory',
                return_value=Path('/code'),
            ),
            mock.patch(
                'showco.deployment.local_update.prepare_local_repositories',
                return_value=True,
            ) as prepare,
            mock.patch(
                'showco.deployment.local_update.refresh_local_dependencies'
            ) as refresh,
            mock.patch('showco.provision.provision.resolved_config') as resolved,
        ):
            result = deploy.run(options)

        self.assertEqual(result, 0)
        prepare.assert_called_once_with(
            ['recs', 'showco'],
            Path('/code'),
            update.run_command_with_timeout,
            mock.ANY,
            autosquash=50,
        )
        refresh.assert_not_called()
        resolved.assert_not_called()

    def test_normal_update_respects_disabled_autosquash(self) -> None:
        with (
            mock.patch('showco.provision.provision.resolved_config'),
            mock.patch(
                'showco.deployment.local_update.update_from_provisioning_machine',
                return_value=0,
            ) as update_target,
        ):
            self.assertEqual(
                deploy.run(self.options(repositories=['recs'], autosquash=0)), 0
            )
        self.assertEqual(update_target.call_args.kwargs['autosquash'], 0)

    def test_sync_prepares_and_refreshes_local_repositories(self) -> None:
        options = self.options(sync=True, repositories=['recs'], autosquash=0)
        with (
            mock.patch(
                'showco.provision.provision.local_checkout_directory',
                return_value=Path('/code'),
            ),
            mock.patch(
                'showco.deployment.local_update.prepare_local_repositories',
                return_value=True,
            ) as prepare,
            mock.patch(
                'showco.deployment.local_update.refresh_local_dependencies',
                return_value=True,
            ) as refresh,
            mock.patch('showco.provision.provision.resolved_config') as resolved,
        ):
            result = deploy.run(options)

        self.assertEqual(result, 0)
        prepare.assert_called_once_with(
            ['recs', 'showco'],
            Path('/code'),
            update.run_command_with_timeout,
            mock.ANY,
            autosquash=0,
        )
        refresh.assert_called_once_with(
            ['recs', 'showco'],
            Path('/code'),
            update.run_command_with_timeout,
            mock.ANY,
        )
        resolved.assert_not_called()

    def test_push_and_sync_cannot_be_combined(self) -> None:
        with self.assertRaisesRegex(SystemExit, 'cannot be combined'):
            deploy.run(self.options(push=True, sync=True))

    def test_remote_and_sync_cannot_be_combined(self) -> None:
        with self.assertRaisesRegex(SystemExit, 'cannot be combined'):
            deploy.run(self.options(remote=True, sync=True))

    def test_remote_update_skips_local_repositories(self) -> None:
        provision_config = mock.Mock(ssh_target='tom@bertrand.local')
        options = self.options(remote=True, repositories=['recs'])
        with (
            mock.patch(
                'showco.provision.provision.resolved_config',
                return_value=provision_config,
            ),
            mock.patch(
                'showco.deployment.update.update_remote_target', return_value=0
            ) as remote,
            mock.patch(
                'showco.deployment.local_update.update_from_provisioning_machine'
            ) as local,
        ):
            result = deploy.run(options)

        self.assertEqual(result, 0)
        remote.assert_called_once_with(
            ['recs', 'showco'],
            host=None,
            root=None,
            target_config=provision_config,
            output=mock.ANY,
            clear_settings=False,
        )
        local.assert_not_called()

    def test_target_machine_updates_selected_repositories(self) -> None:
        with mock.patch(
            'showco.deployment.target_update.update_target', return_value=0
        ) as update_target:
            result = deploy.run(
                self.options(target_machine=True, repositories=['recs'])
            )

        self.assertEqual(result, 0)
        update_target.assert_called_once_with(
            ['recs', 'showco'], root=None, clear_settings=False
        )

    def test_clear_settings_option_is_forwarded_to_target_update(self) -> None:
        with mock.patch(
            'showco.deployment.target_update.update_target', return_value=0
        ) as update_target:
            result = deploy.run(
                self.options(
                    target_machine=True,
                    repositories=['recs'],
                    clear_settings=False,
                )
            )

        self.assertEqual(result, 0)
        update_target.assert_called_once_with(
            ['recs', 'showco'], root=None, clear_settings=False
        )

    def test_no_clear_settings_option_preserves_recs_settings(self) -> None:
        options = tyro.cli(provision.DeployOptions, args=['--no-clear-settings'])

        self.assertFalse(options.clear_settings)

    def test_upgrade_cannot_be_combined_with_update_options(self) -> None:
        with self.assertRaisesRegex(SystemExit, 'cannot be combined'):
            deploy.run(self.options(upgrade=True, repositories=['recs']))

    def test_remote_is_unavailable_on_target_machine(self) -> None:
        with self.assertRaisesRegex(SystemExit, 'unavailable on the target'):
            deploy.run(self.options(target_machine=True, remote=True))
