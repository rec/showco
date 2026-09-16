from __future__ import annotations

import subprocess
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from provision_helpers import make_config, networks, values

from showco.provision import network as network_config
from showco.provision import remote, script


class ProvisionScriptTests(unittest.TestCase):
    def test_remote_script_is_removed_after_remote_failure(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        original_error = subprocess.CalledProcessError(1, ['ssh', 'provision'])
        cleanup_error = subprocess.CalledProcessError(1, ['ssh', 'cleanup'])
        with (
            mock.patch(
                'showco.provision.ssh.run_ssh',
                side_effect=[None, None, original_error, cleanup_error],
            ) as run_ssh,
            mock.patch('showco.provision.ssh.wait_for_ssh'),
            mock.patch(
                'showco.provision.remote.preflight_network',
                return_value=network_config.NetworkTopology.PRIVATE,
            ),
            mock.patch('showco.provision.remote.validate_remote_worktrees'),
            mock.patch('showco.provision.ssh.run_scp'),
            self.assertRaises(subprocess.CalledProcessError) as error,
        ):
            remote.provision_remote(
                config,
                Path('/tmp/local.sh'),
                '/tmp/remote.sh',
            )

        self.assertIs(error.exception, original_error)
        self.assertEqual(run_ssh.call_args_list[-1].args[1], 'rm -f /tmp/remote.sh')

    def test_generated_remote_script_has_valid_shell_syntax(self) -> None:
        subprocess.run(
            ['bash', '-n'], input=script.REMOTE_SCRIPT, text=True, check=True
        )

    def test_remote_script_installs_shared_python(self) -> None:
        self.assertIn('phase "installing Python 3.13"', script.REMOTE_SCRIPT)
        self.assertIn('bash -lc "uv python install 3.13"', script.REMOTE_SCRIPT)
        self.assertNotIn('\n    python3\n', script.REMOTE_SCRIPT)
        self.assertNotIn('\n    python3-venv\n', script.REMOTE_SCRIPT)

    def test_remote_script_installs_argon_one_software(self) -> None:
        self.assertIn('install_argon_one()', script.REMOTE_SCRIPT)
        self.assertIn('if [[ "$ARGON_ONE" == true ]]; then', script.REMOTE_SCRIPT)
        self.assertIn('phase "installing Argon ONE software"', script.REMOTE_SCRIPT)
        self.assertIn('https://download.argon40.com/argon1.sh', script.REMOTE_SCRIPT)
        self.assertIn('Argon ONE software is already installed.', script.REMOTE_SCRIPT)

    def test_remote_script_configures_external_storage_mounts(self) -> None:
        self.assertIn('exfatprogs', script.REMOTE_SCRIPT)
        self.assertIn('phase "configuring storage mounts"', script.REMOTE_SCRIPT)
        self.assertIn('lsblk -f', script.REMOTE_SCRIPT)
        self.assertIn('UUID=%s %s %s %s 0 2', script.REMOTE_SCRIPT)
        self.assertIn('fstab_mountpoint_for_uuid()', script.REMOTE_SCRIPT)
        self.assertIn('mount_target_for_disk()', script.REMOTE_SCRIPT)
        self.assertIn('target="/mnt/$name-$suffix"', script.REMOTE_SCRIPT)

    def test_remote_script_preserves_broken_checkouts_for_reruns(self) -> None:
        self.assertIn('prepare_checkout_path()', script.REMOTE_SCRIPT)
        self.assertIn('Moving non-git checkout aside', script.REMOTE_SCRIPT)
        self.assertIn('sudo mv "$path" "$backup"', script.REMOTE_SCRIPT)

    def test_remote_script_resets_target_checkout_to_main(self) -> None:
        fetch = script.REMOTE_SCRIPT.index('git -C "$path" fetch origin')
        checkout = script.REMOTE_SCRIPT.index('git -C "$path" checkout --force main')
        reset = script.REMOTE_SCRIPT.index('git -C "$path" reset --hard origin/main')

        self.assertLess(fetch, checkout)
        self.assertLess(checkout, reset)
        self.assertIn(
            '"+refs/heads/main:refs/remotes/origin/main"',
            script.REMOTE_SCRIPT,
        )
        self.assertIn(
            'git -C "$path" remote set-url origin "$url"', script.REMOTE_SCRIPT
        )
        self.assertNotIn('git -C "$path" pull --ff-only', script.REMOTE_SCRIPT)

    def test_remote_script_checks_out_configured_refname(self) -> None:
        self.assertIn('git -C "$path" fetch origin "$refname"', script.REMOTE_SCRIPT)
        self.assertIn(
            'git -C "$path" checkout --detach FETCH_HEAD', script.REMOTE_SCRIPT
        )
        self.assertIn(
            'sync_repo reccy "$RECCY_REPO" "$RECCY_REFNAME"', script.REMOTE_SCRIPT
        )
        self.assertIn(
            'sync_repo recs "$RECS_REPO" "$RECS_REFNAME"', script.REMOTE_SCRIPT
        )
        self.assertIn(
            'sync_repo lyte "$LYTE_REPO" "$LYTE_REFNAME"', script.REMOTE_SCRIPT
        )

    def test_remote_script_reinstalls_broken_uv(self) -> None:
        self.assertIn('uv --version', script.REMOTE_SCRIPT)

    def test_remote_script_checks_before_syncing_unchanged_environments(self) -> None:
        self.assertIn('uv sync --locked', script.REMOTE_SCRIPT)
        self.assertIn('uv sync --locked --check', script.REMOTE_SCRIPT)

    def test_remote_script_configures_github_urls_before_syncing(self) -> None:
        github_urls = script.REMOTE_SCRIPT.index(
            'git config --global url."https://github.com/".insteadOf'
        )
        syncing = script.REMOTE_SCRIPT.index('phase "syncing repositories"')

        self.assertLess(github_urls, syncing)

    def test_remote_script_skips_unchanged_system_and_services(self) -> None:
        self.assertIn('install_base_packages()', script.REMOTE_SCRIPT)
        self.assertIn('Base packages are already installed.', script.REMOTE_SCRIPT)
        self.assertIn('service_is_current()', script.REMOTE_SCRIPT)
        self.assertIn('record_service_state()', script.REMOTE_SCRIPT)
        self.assertIn('completed in %ss', script.REMOTE_SCRIPT)

    def test_remote_script_uses_locked_uv_run(self) -> None:
        self.assertIn('uv run --locked showco run network-config', script.REMOTE_SCRIPT)
        self.assertIn('uv run --locked recs daemon install', script.REMOTE_SCRIPT)
        self.assertIn('uv run --locked streamo daemon install', script.REMOTE_SCRIPT)
        self.assertIn('uv run --locked lyte installation install', script.REMOTE_SCRIPT)
        self.assertIn(
            'uv run --locked showco run install-service', script.REMOTE_SCRIPT
        )

    def test_remote_script_writes_provisioning_report(self) -> None:
        self.assertIn('phase "writing provisioning report"', script.REMOTE_SCRIPT)
        self.assertIn('Disks discovered:', script.REMOTE_SCRIPT)
        self.assertIn('Wi-Fi interfaces discovered:', script.REMOTE_SCRIPT)
        self.assertIn('nmcli device status', script.REMOTE_SCRIPT)
        self.assertIn('iw dev', script.REMOTE_SCRIPT)
        self.assertIn('lyte:', script.REMOTE_SCRIPT)
        self.assertIn('lyte service:', script.REMOTE_SCRIPT)
        self.assertIn('streamO:', script.REMOTE_SCRIPT)
        self.assertIn('streamo service:', script.REMOTE_SCRIPT)
        self.assertIn('PROVISIONING-REPORT.txt', script.REMOTE_SCRIPT)

    def test_remote_script_marks_target_machine(self) -> None:
        self.assertIn(
            '"/home/$SHOW_USER/.config/showco/machine-role"',
            script.REMOTE_SCRIPT,
        )
        self.assertIn("printf 'target\\n'", script.REMOTE_SCRIPT)

    def test_remote_script_configures_network(self) -> None:
        self.assertIn('phase "configuring network"', script.REMOTE_SCRIPT)
        self.assertIn('configure_network()', script.REMOTE_SCRIPT)
        self.assertIn('uv run --locked showco run network-config', script.REMOTE_SCRIPT)
        self.assertIn('write_toml_string host "$SHOWCO_HOST"', script.REMOTE_SCRIPT)
        self.assertIn("printf '\\n[git.reccy]\\n'", script.REMOTE_SCRIPT)
        self.assertIn("printf '\\n[git.lyte]\\n'", script.REMOTE_SCRIPT)
        self.assertIn('Skipping network configuration', script.REMOTE_SCRIPT)

    def test_remote_script_installs_showco_service_through_showco(self) -> None:
        self.assertIn(
            'uv run --locked showco run install-service', script.REMOTE_SCRIPT
        )
        self.assertNotIn('tee "$service_file"', script.REMOTE_SCRIPT)
        self.assertNotIn('ExecStart=$command', script.REMOTE_SCRIPT)

    def test_remote_script_restarts_changed_services(self) -> None:
        self.assertIn('user_systemctl restart recs.service', script.REMOTE_SCRIPT)
        self.assertIn('user_systemctl restart showco.service', script.REMOTE_SCRIPT)
        self.assertIn('user_systemctl restart streamo.service', script.REMOTE_SCRIPT)
        self.assertIn('user_systemctl restart lyte.service', script.REMOTE_SCRIPT)

    def test_remote_script_reboots_only_when_the_system_requires_it(self) -> None:
        network = script.REMOTE_SCRIPT.index('phase "configuring network"')
        reboot = script.REMOTE_SCRIPT.index('phase "rebooting"')

        self.assertIn(
            'sudo touch /run/showco-provision-reboot-required',
            script.REMOTE_SCRIPT,
        )
        self.assertNotIn(
            'sudo systemd-run --on-active=2s /usr/bin/systemctl reboot',
            script.REMOTE_SCRIPT,
        )
        self.assertNotIn('NETWORK_CHANGED', script.REMOTE_SCRIPT)
        self.assertIn(
            'if [[ -f /var/run/reboot-required ]]; then', script.REMOTE_SCRIPT
        )
        self.assertLess(
            script.REMOTE_SCRIPT.index(
                'sudo rm -f /run/showco-provision-reboot-required'
            ),
            network,
        )
        self.assertLess(network, reboot)

    def test_remote_script_configures_locale_before_package_updates(self) -> None:
        main = script.REMOTE_SCRIPT.rindex('main() {')
        locale = script.REMOTE_SCRIPT.index('phase "configuring locale"', main)
        journal = script.REMOTE_SCRIPT.index(
            'phase "configuring persistent journal"', main
        )
        install = script.REMOTE_SCRIPT.index(
            'install_base_packages "${packages[@]}"', main
        )

        self.assertLess(locale, journal)
        self.assertIn('sudo apt-get update', script.REMOTE_SCRIPT)
        self.assertIn('sudo apt-get upgrade -y', script.REMOTE_SCRIPT)
        self.assertIn(
            'if [[ "$UPGRADE_PACKAGES" == true ]]; then', script.REMOTE_SCRIPT
        )
        self.assertNotIn('|| -z "$installed_hash"', script.REMOTE_SCRIPT)
        self.assertLess(journal, install)

    def test_remote_script_configures_persistent_journal(self) -> None:
        self.assertIn('Storage=persistent', script.REMOTE_SCRIPT)
        self.assertIn('/var/log/journal', script.REMOTE_SCRIPT)
        self.assertIn('systemctl restart systemd-journald', script.REMOTE_SCRIPT)

    def test_remote_script_exports_locale_before_package_updates(self) -> None:
        main = script.REMOTE_SCRIPT.rindex('main() {')
        locale = script.REMOTE_SCRIPT.index('configure_locale', main)
        install = script.REMOTE_SCRIPT.index(
            'install_base_packages "${packages[@]}"', main
        )
        self.assertIn('unset LC_ALL', script.REMOTE_SCRIPT)
        self.assertLess(locale, install)

    def test_remote_command_passes_network_config(self) -> None:
        config = make_config(
            values(
                networks=networks(
                    x18=False,
                    internal_wifi={'password': 'private password'},
                    external_wifi={
                        'name': 'Venue',
                        'password': 'venue password',
                    },
                ),
            ),
        )

        command = script.remote_command(config, '/tmp/provision.sh')

        self.assertIn('SHOWCO_HOST=recs-stage.local', command)
        self.assertIn('ROOT=/srv/show-projects', command)
        self.assertIn("RECCY_REFNAME=''", command)
        self.assertIn('EXTERNAL_WIFI_SSID=Venue', command)
        self.assertIn("EXTERNAL_WIFI_PASSWORD='venue password'", command)
        self.assertIn("PRIVATE_WIFI_PASSWORD='private password'", command)
        self.assertIn('X18=false', command)
        self.assertIn("RECS_REFNAME=''", command)

    def test_remote_command_passes_package_upgrade_request(self) -> None:
        default = script.remote_command(
            make_config(values(networks=networks(x18=False))),
            '/tmp/provision.sh',
        )
        command = script.remote_command(
            make_config(values(networks=networks(x18=False))),
            '/tmp/provision.sh',
            upgrade=True,
        )

        self.assertIn('UPGRADE_PACKAGES=false', default)
        self.assertIn('UPGRADE_PACKAGES=true', command)

    def test_remote_command_passes_argon_one_configuration(self) -> None:
        enabled = script.remote_command(make_config(values()), '/tmp/provision.sh')
        disabled = script.remote_command(
            make_config(values(argon_one=False)), '/tmp/provision.sh'
        )

        self.assertIn('ARGON_ONE=true', enabled)
        self.assertIn('ARGON_ONE=false', disabled)

    def test_remote_command_quotes_root_with_spaces(self) -> None:
        parsed = make_config(values(paths={'root': '/srv/show projects'}))

        command = script.remote_command(parsed, '/tmp/provision.sh')

        self.assertIn("ROOT='/srv/show projects'", command)

    def test_remote_command_passes_git_refname(self) -> None:
        config = make_config(
            values(
                networks=networks(x18=False),
                git={'recs': {'refname': 'my-branch'}},
            ),
        )

        command = script.remote_command(config, '/tmp/provision.sh')

        self.assertIn('RECS_REFNAME=my-branch', command)

    def test_remote_command_passes_enabled_from_stream_table(self) -> None:
        config = make_config(
            values(
                networks=networks(x18=False),
                stream={'enabled': True},
            ),
        )

        command = script.remote_command(config, '/tmp/provision.sh')

        self.assertIn('STREAMO_ENABLED=true', command)

    def test_remote_script_includes_mixer_selectors(self) -> None:
        self.assertIn(
            'done <<<"$RECS_AUDIO_DEVICE_NAMES"',
            script.REMOTE_SCRIPT,
        )
        self.assertIn('args+=(--include "$device_name")', script.REMOTE_SCRIPT)

    def test_mixer_configuration_renders_osc_and_deduplicates_selectors(self) -> None:
        mixers = make_config(values()).mixers

        rendered = script.mixers_toml(mixers)
        osc = script.osc_nodes_toml(mixers)

        self.assertEqual(
            script.unique_selectors(['X18', 'XR18', 'X18']), ['X18', 'XR18']
        )
        self.assertEqual(len(tomllib.loads(rendered)['mixers']), 2)
        self.assertIn("subscription_path = '/xremote'", rendered)
        self.assertEqual(osc.count('[[nodes]]'), 1)
