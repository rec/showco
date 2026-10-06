from __future__ import annotations

import os
import shlex
import subprocess
import tomllib
import unittest
from pathlib import Path
from unittest import mock

import pytest
from provision_helpers import make_config, networks, values

from showco.provision import config, remote, script
from showco.provision import network as network_config


class ProvisionScriptTests(unittest.TestCase):
    def test_remote_command_quotes_repeated_target_audio_paths(self) -> None:
        source = Path("/mnt/audio/performer's tracks/$intro.flac")
        parsed = make_config(values()).model_copy(
            update={
                'audio': [Path('/mnt/audio/common'), source],
                'setup': [Path('/mnt/audio/open')],
            }
        )
        command = script.remote_command(parsed, '/tmp/provision.sh')
        assignment = next(
            a for a in shlex.split(command) if a.startswith('SHOWCO_AUDIO_ARGS=')
        )
        self.assertEqual(
            shlex.split(assignment.partition('=')[2]),
            [
                '--audio',
                '/mnt/audio/common',
                '--audio',
                str(source),
                '--setup',
                '/mnt/audio/open',
            ],
        )

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
        self.assertIn("--ssh-peer '${SSH_CLIENT%% *}'", script.REMOTE_SCRIPT)
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

    def test_remote_script_limits_external_ingress_to_ssh_and_mdns(self) -> None:
        self.assertIn('nftables', script.REMOTE_SCRIPT)
        self.assertIn('phase "restricting inbound connections"', script.REMOTE_SCRIPT)
        self.assertIn('iifname "$private_interface" accept', script.REMOTE_SCRIPT)
        self.assertIn('ct state established,related accept', script.REMOTE_SCRIPT)
        self.assertIn('tcp dport $SHOWCO_SSH_PORT accept', script.REMOTE_SCRIPT)
        self.assertIn('udp dport 5353 accept', script.REMOTE_SCRIPT)
        self.assertIn('policy drop;', script.REMOTE_SCRIPT)
        self.assertIn('WantedBy=multi-user.target', script.REMOTE_SCRIPT)

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
        self.assertIn('name = "Venue"', command)
        self.assertIn('password = "venue password"', command)
        self.assertIn("PRIVATE_WIFI_PASSWORD='private password'", command)
        self.assertIn('X18=false', command)
        self.assertIn("RECS_REFNAME=''", command)
        self.assertIn('SHOWCO_SSH_PORT=22', command)
        self.assertIn('SHOWCO_GUI_PATH=showco/gui.toml', command)
        self.assertIn('RESTRICT_EXTERNAL_INGRESS=false', command)

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


@pytest.mark.parametrize('x18', [True, False])
@pytest.mark.parametrize('hotspot_offset', [None, 7])
def test_generated_network_files_are_readable_and_preserve_addresses(
    tmp_path: Path, x18: bool, hotspot_offset: int | None
) -> None:
    network_values = networks(
        x18=x18,
        internal_wifi={
            'name': 'show "box"',
            'ip_address': hotspot_offset,
            'password': 'test-private-password',
        },
        external_wifi={'name': 'venue Wi-Fi', 'password': 'test-external-password'},
    )
    network_values['external']['wifi']['venue'] = {
        'name': 'Venue "guest" Wi-Fi',
        'password': "second ' password\\with slash",
    }
    network_values['internal']['subnet'] = '192.168.70.0/24'
    original = make_config(
        values(
            networks=network_values,
            network={'topology': 'mixed', 'restrict_external_ingress': True},
        )
    )
    assignments = shlex.split(script.remote_command(original, '/tmp/provision.sh'))[:-2]
    environment = dict(os.environ)
    environment.update(a.split('=', 1) for a in assignments)
    public_file = tmp_path / 'network.toml'
    secrets_file = tmp_path / 'secrets.toml'
    # Exercise only the file writer, with privileged ownership changes disabled.
    writer = (script.SCRIPT_DIR / 'templates/network.sh').read_text()
    subprocess.run(
        [
            'bash',
            '-c',
            writer + '\nsudo() { :; }\nwrite_network_config_files "$1" "$2"',
            'writer',
            str(public_file),
            str(secrets_file),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )

    restored = config.config_from_values(config.load_values(public_file, secrets_file))

    assert restored.network == original.network
    assert restored.networks == original.networks
    assert config.x18(restored) == config.x18(original)
    assert 'test-private-password' not in public_file.read_text()
    assert 'test-external-password' not in public_file.read_text()
    assert 'second' not in public_file.read_text()
    assert list(restored.networks['external']['wifi']) == ['home', 'venue']
