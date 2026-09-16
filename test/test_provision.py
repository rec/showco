from __future__ import annotations

import shlex
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import tyro
from provision_helpers import make_config, networks, values

from showco.deployment import update
from showco.provision import config, provision, remote, ssh, state, verify
from showco.provision import network as network_config


class ProvisionTests(unittest.TestCase):
    def test_go_options_accept_provisioning_and_update_flags(self) -> None:
        options = tyro.cli(
            provision.GoOptions,
            args=[
                '--host',
                'bertrand.local',
                '--root',
                '/srv/show-projects',
                '--recs-repo',
                'https://github.com/rec/recs.git',
                '--lyte-enabled',
                'True',
                '--lyte-installation-config',
                'patches/test-installation.toml',
                '--upgrade',
                '--remote',
                '--autosquash',
                '0',
                'recs',
            ],
        )

        self.assertEqual(options.host, 'bertrand.local')
        self.assertEqual(options.root, Path('/srv/show-projects'))
        self.assertEqual(options.recs_repo, 'https://github.com/rec/recs.git')
        self.assertTrue(options.lyte_enabled)
        self.assertEqual(
            options.lyte_installation_config,
            Path('patches/test-installation.toml'),
        )
        self.assertTrue(options.upgrade)
        self.assertTrue(options.remote)
        self.assertEqual(options.autosquash, 0)
        self.assertEqual(options.repositories, ['recs'])

    def test_go_options_accept_local_update_flags(self) -> None:
        push = tyro.cli(provision.GoOptions, args=['--push', 'recs'])
        sync = tyro.cli(provision.GoOptions, args=['--sync', 'reccy'])

        self.assertTrue(push.push)
        self.assertEqual(push.repositories, ['recs'])
        self.assertTrue(sync.sync)
        self.assertEqual(sync.repositories, ['reccy'])

    def test_run_finishes_after_successful_provisioning(self) -> None:
        options = provision.GoOptions(
            config_path=Path('config.toml'),
            secrets=Path('secrets.toml'),
        )

        with (
            mock.patch(
                'showco.provision.provision.config.read_toml',
                side_effect=[values(), {}],
            ),
            mock.patch(
                'showco.provision.provision.validate_config',
            ),
            mock.patch(
                'showco.deployment.local_update.prepare_local_repositories',
                return_value=True,
            ),
            mock.patch(
                'showco.deployment.local_update.refresh_local_dependencies',
                return_value=True,
            ),
            mock.patch(
                'showco.provision.remote.provision_remote',
            ) as provision_remote,
        ):
            result = provision.run(options)

        self.assertEqual(result, 0)
        self.assertFalse(provision_remote.call_args.kwargs['upgrade'])

    def test_run_prepares_local_repositories_with_update(self) -> None:
        options = provision.GoOptions(
            config_path=Path('config.toml'), secrets=Path('secrets.toml')
        )
        with (
            mock.patch(
                'showco.provision.provision.config.read_toml',
                side_effect=[values(), {}],
            ),
            mock.patch('showco.provision.provision.validate_config'),
            mock.patch(
                'showco.deployment.local_update.prepare_local_repositories',
                return_value=True,
            ) as prepare,
            mock.patch(
                'showco.deployment.local_update.refresh_local_dependencies',
                return_value=True,
            ) as refresh,
            mock.patch('showco.provision.remote.provision_remote'),
        ):
            provision.run(options)

        self.assertEqual(prepare.call_args.args[0], update.REPOSITORY_NAMES)
        self.assertEqual(refresh.call_args.args[0], update.REPOSITORY_NAMES)

    def test_run_passes_package_upgrade_to_remote_provisioning(self) -> None:
        options = provision.GoOptions(
            config_path=Path('config.toml'), secrets=Path('secrets.toml'), upgrade=True
        )
        with (
            mock.patch(
                'showco.provision.provision.config.read_toml',
                side_effect=[values(), {}],
            ),
            mock.patch('showco.provision.provision.validate_config'),
            mock.patch(
                'showco.deployment.local_update.prepare_local_repositories',
                return_value=True,
            ),
            mock.patch(
                'showco.deployment.local_update.refresh_local_dependencies',
                return_value=True,
            ),
            mock.patch('showco.provision.remote.provision_remote') as provision_remote,
        ):
            provision.run(options)

        self.assertTrue(provision_remote.call_args.kwargs['upgrade'])

    def test_network_preflight_rejects_connected_hotspot_interface(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        with (
            mock.patch(
                'showco.provision.ssh.capture_ssh',
                return_value='wlan0:wifi:connected\n',
            ),
            self.assertRaisesRegex(SystemExit, 'no unconnected Wi-Fi interface'),
        ):
            remote.preflight_network(config)

    def test_network_preflight_preserves_connected_external_wifi(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        with mock.patch(
            'showco.provision.ssh.capture_ssh',
            return_value='wlan0:wifi:disconnected\nwlan1:wifi:connected\n',
        ) as capture_ssh:
            topology = remote.preflight_network(config)

        capture_ssh.assert_called_once_with(
            config,
            'nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device status',
        )
        self.assertEqual(topology, network_config.NetworkTopology.PRIVATE)

    def test_network_preflight_reuses_existing_private_hotspot(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        with mock.patch(
            'showco.provision.ssh.capture_ssh',
            return_value=(
                'wlan0:wifi:connected:Livebox\nwlan1:wifi:connected:showco-private\n'
            ),
        ):
            topology = remote.preflight_network(config)

        self.assertEqual(topology, network_config.NetworkTopology.PRIVATE)

    def test_run_uses_key_based_ssh_command(self) -> None:
        with mock.patch('reccy.runtime.subprocess.run') as run:
            ssh.run_command(['ssh'])

        run.assert_called_once_with(
            ['ssh'],
            capture_output=False,
            check=True,
            text=True,
        )

    def test_provision_checks_worktrees_before_network_preflight(self) -> None:
        calls: list[str] = []
        config = make_config(values(networks=networks(x18=False)))

        def preflight_network(config: config.Config) -> None:
            calls.append('preflight')

        def validate_remote_worktrees(config: config.Config) -> None:
            calls.append('worktrees')

        def run_scp(config: config.Config, source: Path, remote_path: str) -> None:
            calls.append('scp')

        with (
            mock.patch('showco.provision.ssh.run_ssh'),
            mock.patch(
                'showco.provision.remote.preflight_network',
                side_effect=preflight_network,
            ),
            mock.patch(
                'showco.provision.remote.validate_remote_worktrees',
                side_effect=validate_remote_worktrees,
            ),
            mock.patch('showco.provision.ssh.run_scp', side_effect=run_scp),
            mock.patch('showco.provision.ssh.wait_for_ssh'),
            mock.patch('showco.provision.ssh.wait_for_rebooted_ssh'),
            mock.patch(
                'showco.provision.ssh.provisioning_reboot_required',
                return_value=False,
            ),
            mock.patch(
                'showco.provision.verify.verify_provisioning',
                return_value=[],
            ),
            mock.patch('showco.provision.verify.report_verification_results'),
        ):
            remote.provision_remote(
                config,
                Path('/tmp/local.sh'),
                '/tmp/remote.sh',
            )

        self.assertEqual(calls, ['worktrees', 'preflight', 'scp'])

    def test_provision_requires_passwordless_sudo_before_remote_checks(self) -> None:
        provision_config = make_config(values(networks=networks(x18=False)))
        with (
            mock.patch('showco.provision.ssh.run_ssh') as run_ssh,
            mock.patch('showco.provision.ssh.wait_for_ssh'),
            mock.patch('showco.provision.remote.validate_remote_worktrees'),
            mock.patch(
                'showco.provision.remote.preflight_network',
                return_value=network_config.NetworkTopology.PRIVATE,
            ),
            mock.patch('showco.provision.ssh.run_scp'),
            mock.patch(
                'showco.provision.ssh.provisioning_reboot_required',
                return_value=False,
            ),
            mock.patch(
                'showco.provision.verify.wait_for_provisioning_ready',
                return_value=[],
            ),
            mock.patch('showco.provision.verify.report_verification_results'),
        ):
            remote.provision_remote(
                provision_config,
                Path('/tmp/local.sh'),
                '/tmp/remote.sh',
            )

        self.assertEqual(
            run_ssh.call_args_list[0].args[1], remote.PASSWORDLESS_SUDO_COMMAND
        )

    def test_fingerprint_changes_with_configuration_or_script(self) -> None:
        provision_config = make_config(values())
        changed_config = make_config(values(network={'web_port': 10000}))

        fingerprint = state.provisioning_fingerprint(provision_config, 'script')

        self.assertNotEqual(
            fingerprint, state.provisioning_fingerprint(changed_config, 'script')
        )
        self.assertNotEqual(
            fingerprint, state.provisioning_fingerprint(provision_config, 'changed')
        )

    def test_provision_waits_for_reboot_and_reports_verification(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        result = [verify.VerificationResult(name='showco', error='')]
        with (
            mock.patch('showco.provision.ssh.run_ssh'),
            mock.patch(
                'showco.provision.remote.preflight_network',
                return_value=network_config.NetworkTopology.PRIVATE,
            ),
            mock.patch('showco.provision.remote.validate_remote_worktrees'),
            mock.patch('showco.provision.ssh.run_scp'),
            mock.patch('showco.provision.ssh.wait_for_ssh') as initial_wait,
            mock.patch('showco.provision.ssh.remove_known_host') as remove_host,
            mock.patch(
                'showco.provision.ssh.provisioning_reboot_required',
                return_value=True,
            ),
            mock.patch('showco.provision.ssh.schedule_remote_reboot') as schedule,
            mock.patch('showco.provision.ssh.wait_for_rebooted_ssh') as wait,
            mock.patch(
                'showco.provision.verify.verify_provisioning',
                return_value=result,
            ) as verification,
            mock.patch('showco.provision.verify.report_verification_results') as report,
        ):
            remote.provision_remote(
                config,
                Path('/tmp/local.sh'),
                '/tmp/remote.sh',
            )

        initial_wait.assert_called_once_with(config)
        remove_host.assert_called_once_with(config)
        wait.assert_called_once_with(config)
        schedule.assert_called_once_with(config)
        verification.assert_called_once_with(
            config, network_config.NetworkTopology.PRIVATE
        )
        report.assert_called_once_with(result)

    def test_provision_records_fingerprint_after_verification(self) -> None:
        provision_config = make_config(values(networks=networks(x18=False)))
        with (
            mock.patch('showco.provision.ssh.run_ssh'),
            mock.patch(
                'showco.provision.remote.preflight_network',
                return_value=network_config.NetworkTopology.PRIVATE,
            ),
            mock.patch('showco.provision.remote.validate_remote_worktrees'),
            mock.patch('showco.provision.ssh.run_scp'),
            mock.patch('showco.provision.ssh.wait_for_ssh'),
            mock.patch(
                'showco.provision.ssh.provisioning_reboot_required',
                return_value=False,
            ),
            mock.patch(
                'showco.provision.verify.wait_for_provisioning_ready', return_value=[]
            ),
            mock.patch('showco.provision.verify.report_verification_results'),
            mock.patch(
                'showco.provision.remote.record_applied_provisioning_fingerprint'
            ) as record,
        ):
            remote.provision_remote(
                provision_config,
                Path('/tmp/local.sh'),
                '/tmp/remote.sh',
                fingerprint='a' * 64,
            )

        record.assert_called_once_with(provision_config, 'a' * 64)

    def test_provision_does_not_wait_for_reboot_when_not_required(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        with (
            mock.patch('showco.provision.ssh.run_ssh'),
            mock.patch(
                'showco.provision.remote.preflight_network',
                return_value=network_config.NetworkTopology.PRIVATE,
            ),
            mock.patch('showco.provision.remote.validate_remote_worktrees'),
            mock.patch('showco.provision.ssh.run_scp'),
            mock.patch('showco.provision.ssh.wait_for_ssh'),
            mock.patch(
                'showco.provision.ssh.provisioning_reboot_required',
                return_value=False,
            ),
            mock.patch('showco.provision.ssh.schedule_remote_reboot') as schedule,
            mock.patch('showco.provision.ssh.wait_for_rebooted_ssh') as wait,
            mock.patch('showco.provision.verify.verify_provisioning', return_value=[]),
            mock.patch('showco.provision.verify.report_verification_results'),
        ):
            remote.provision_remote(
                config,
                Path('/tmp/local.sh'),
                '/tmp/remote.sh',
            )

        schedule.assert_not_called()
        wait.assert_not_called()

    def test_wait_for_provisioning_ready_retries_startup_checks(self) -> None:
        starting = [
            verify.VerificationResult(name='recs service is active', error='activating')
        ]
        ready = [verify.VerificationResult(name='recs service is active', error='')]
        config = make_config(values())
        with (
            mock.patch(
                'showco.provision.verify.verify_provisioning',
                side_effect=[starting, ready],
            ) as verification,
            mock.patch('showco.provision.ssh.time.sleep') as sleep,
        ):
            result = verify.wait_for_provisioning_ready(
                config,
                network_config.NetworkTopology.MIXED,
            )

        self.assertEqual(result, ready)
        self.assertEqual(verification.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_lyte_service_is_a_startup_check(self) -> None:
        self.assertIn('lyte service', verify.STARTUP_CHECK_NAMES)

    def test_streamo_health_command_uses_target_showco(self) -> None:
        command = verify.showco_streamo_health_command(Path('/code'))

        self.assertIn('cd /code/showco', command)
        self.assertIn('uv run --locked showco run streamo-health', command)

    def test_initial_wait_for_ssh_retries_until_connected(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        with (
            mock.patch(
                'showco.provision.ssh.ssh_is_reachable',
                side_effect=[False, False, True],
            ) as reachable,
            mock.patch('showco.provision.ssh.time.sleep') as sleep,
        ):
            ssh.wait_for_ssh(config)

        self.assertEqual(reachable.call_count, 3)
        sleep.assert_has_calls([mock.call(1), mock.call(1)])

    def test_changed_host_key_is_removed_during_ssh_retry(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        changed_key = subprocess.CompletedProcess(
            ['ssh'],
            255,
            '',
            'WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!',
        )
        connected = subprocess.CompletedProcess(['ssh'], 0, '', '')
        removed = subprocess.CompletedProcess(['ssh-keygen'], 0, '', '')
        with mock.patch(
            'reccy.runtime.subprocess.run',
            side_effect=[changed_key, removed, connected],
        ) as run:
            self.assertFalse(ssh.ssh_is_reachable(config))
            self.assertTrue(ssh.ssh_is_reachable(config))

        self.assertEqual(
            run.call_args_list[1].args[0],
            ['ssh-keygen', '-R', 'recs-stage.local'],
        )

    def test_config_uses_ssh_target_and_accepts_changed_host_keys(self) -> None:
        config = make_config(values(networks=networks(x18=False)))

        self.assertEqual(config.ssh_target, 'tom@recs-stage.local')
        self.assertTrue(config.accept_changed_host_key)

    def test_changed_host_key_requires_explicit_acceptance(self) -> None:
        config = make_config(
            values(networks=networks(x18=False), accept_changed_host_key=False)
        )
        changed_key = subprocess.CompletedProcess(
            ['ssh'], 255, '', 'WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!'
        )
        with mock.patch('reccy.runtime.subprocess.run', return_value=changed_key):
            with self.assertRaisesRegex(SystemExit, 'accept_changed_host_key'):
                ssh.ssh_is_reachable(config)

    def test_ssh_retry_uses_non_interactive_host_key_options(self) -> None:
        config = make_config(values(networks=networks(x18=False)))

        command = ssh.ssh_command(
            config,
            config.ssh_target,
            'true',
            connect_timeout=1,
        )

        self.assertIn('BatchMode=yes', command)
        self.assertIn('StrictHostKeyChecking=accept-new', command)
        self.assertIn('ConnectTimeout=1', command)

    def test_ssh_command_uses_short_default_connect_timeout(self) -> None:
        config = make_config(values(networks=networks(x18=False)))

        command = ssh.ssh_command(config, config.ssh_target, 'true')

        self.assertIn('ConnectTimeout=2', command)

    def test_run_scp_uses_short_connect_timeout(self) -> None:
        config = make_config(values(networks=networks(x18=False)))

        with mock.patch('reccy.runtime.subprocess.run') as run:
            ssh.run_scp(config, Path('/tmp/local.sh'), '/tmp/remote.sh')

        self.assertIn('ConnectTimeout=2', run.call_args.args[0])

    def test_run_ssh_reports_connection_failure_without_traceback(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        error = subprocess.CalledProcessError(
            255,
            ['ssh'],
            stderr='ssh: connect to host failed\n',
        )

        with (
            mock.patch('reccy.runtime.subprocess.run', side_effect=error),
            self.assertRaises(SystemExit) as exit_error,
        ):
            ssh.run_ssh(config, 'true')

        self.assertIn(
            'ERROR: SSH connection or command failed for tom@recs-stage.local.',
            str(exit_error.exception),
        )
        self.assertIn('SSH connect timeout is 2 seconds.', str(exit_error.exception))
        self.assertIn(
            'ssh said: ssh: connect to host failed', str(exit_error.exception)
        )

    def test_known_host_names_include_port_specific_host(self) -> None:
        config = make_config(
            values(networks=networks(x18=False)),
            port=2200,
        )

        self.assertEqual(
            ssh.known_host_names(config),
            ['recs-stage.local', '[recs-stage.local]:2200'],
        )

    def test_remote_worktree_command_reports_all_tracked_changes(self) -> None:
        command = shlex.split(
            remote.remote_worktree_command(Path('/srv/show-projects'))
        )[2]

        self.assertIn('for name in showco reccy recs streamo lyte', command)
        self.assertIn('git -C "$path" status --short --untracked-files=no', command)
        self.assertNotIn("sed -E '/^.. (.*\\/)?uv\\.lock$/d'", command)
        self.assertIn('printf \'%s:\\n%s\\n\' "$name" "$status"', command)
        self.assertIn("printf '%s: not a Git checkout: %s\\n'", command)
        self.assertIn('exit 1', command)

    def test_project_status_command_reports_dirty_uv_lock(self) -> None:
        command = verify.project_status_command('recs', Path('/srv/show-projects'))

        self.assertEqual(
            command,
            'git -C /srv/show-projects/recs status --short',
        )

    def test_wait_for_rebooted_ssh_waits_for_disconnect_then_connect(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        with (
            mock.patch(
                'showco.provision.ssh.ssh_is_reachable',
                side_effect=[True, False, False, True],
            ) as reachable,
            mock.patch('showco.provision.ssh.time.sleep'),
        ):
            ssh.wait_for_rebooted_ssh(config)

        self.assertEqual(reachable.call_count, 4)

    def test_verify_provisioning_checks_projects_and_user_services(self) -> None:
        config = make_config(values())
        with mock.patch(
            'reccy.runtime.subprocess.run',
            return_value=subprocess.CompletedProcess(['ssh'], 0, '', ''),
        ) as run:
            results = verify.verify_provisioning(
                config,
                network_config.NetworkTopology.MIXED,
            )

        commands = [c.args[0][-1] for c in run.call_args_list]
        self.assertFalse([r for r in results if r.error])
        self.assertIn(
            verify.project_statuses_command(Path('/srv/show-projects')),
            commands,
        )
        self.assertIn(
            'uid=$(id -u); XDG_RUNTIME_DIR=/run/user/$uid '
            'cd /srv/show-projects/showco && PATH="$HOME/.local/bin:$PATH" '
            'uv run --locked showco run service-status recs',
            commands,
        )
        self.assertIn(
            'nmcli -t -f TYPE,STATE,CONNECTION device status '
            "| grep -F -x 'wifi:connected:showco-private'",
            commands,
        )
        self.assertIn(
            'ip -4 -o address show dev br-x18 | grep -F 10.0.0.1/24',
            commands,
        )
        self.assertIn(
            'uid=$(id -u); XDG_RUNTIME_DIR=/run/user/$uid '
            'cd /srv/show-projects/showco && PATH="$HOME/.local/bin:$PATH" '
            'uv run --locked showco run service-status showco',
            commands,
        )
        self.assertTrue(
            any('status="$HOME/.local/state/recs/status.json"' in c for c in commands)
        )
        self.assertTrue(
            any('systemd-cat --identifier=showco-provisioning' in c for c in commands)
        )

    def test_missing_mixer_devices_are_notes_not_errors(self) -> None:
        config = make_config(values())
        with mock.patch(
            'reccy.runtime.subprocess.run',
            return_value=subprocess.CompletedProcess(['ssh'], 1, '', ''),
        ):
            result = verify.verify_mixer_devices(config)

        self.assertTrue(all(value.error == '' for value in result))
        self.assertEqual(result[0].name, 'X18 USB audio')
        self.assertEqual(result[0].note, 'X18/XR18 not detected')

    def test_mixer_audio_device_check_accepts_any_selector(self) -> None:
        config = make_config(values())
        with mock.patch(
            'reccy.runtime.subprocess.run',
            return_value=subprocess.CompletedProcess(['ssh'], 0, '', ''),
        ) as run:
            result = verify.verify_mixer_audio_inputs(config, 'X18', ['X18', 'XR18'])

        self.assertIn(
            'arecord -l | grep -Fi -e X18 -e XR18 >/dev/null',
            run.call_args.args[0],
        )
        self.assertEqual(result.name, 'X18 USB audio')

    def test_report_verification_results_exits_with_errors(self) -> None:
        with self.assertRaises(SystemExit):
            verify.report_verification_results(
                [verify.VerificationResult(name='showco', error='inactive')]
            )

    def test_report_verification_results_allows_notes(self) -> None:
        verify.report_verification_results(
            [
                verify.VerificationResult(
                    name='X18 USB device',
                    error='',
                    note='X18/XR18 not detected',
                )
            ]
        )
