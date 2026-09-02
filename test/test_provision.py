from __future__ import annotations

import shlex
import subprocess
import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import tyro

from showco import network_config, update
from showco.provision import config, provision, remote, script, ssh, state, verify


class ProvisionTests(unittest.TestCase):
    def test_provision_options_accept_existing_flags(self) -> None:
        options = tyro.cli(
            provision.ProvisionOptions,
            args=[
                "--host",
                "bertrand.local",
                "--root",
                "/srv/show-projects",
                "--recs-repo",
                "https://github.com/rec/recs.git",
                "--lyte-enabled",
                "True",
                "--lyte-daemon-config",
                "patches/test-daemon.toml",
                "--system",
            ],
        )

        self.assertEqual(options.host, "bertrand.local")
        self.assertEqual(options.root, Path("/srv/show-projects"))
        self.assertEqual(options.recs_repo, "https://github.com/rec/recs.git")
        self.assertTrue(options.lyte_enabled)
        self.assertEqual(options.lyte_daemon_config, Path("patches/test-daemon.toml"))
        self.assertTrue(options.system)

    def test_run_finishes_after_successful_provisioning(self) -> None:
        options = provision.ProvisionOptions(
            config_path=Path("config.toml"),
            secrets=Path("secrets.toml"),
        )

        with (
            mock.patch(
                "showco.provision.provision.config.read_toml",
                side_effect=[values(), {}],
            ),
            mock.patch(
                "showco.provision.provision.validate_config",
            ),
            mock.patch("showco.update.prepare_local_repositories", return_value=True),
            mock.patch(
                "showco.provision.remote.provision_remote",
            ),
        ):
            result = provision.run(options)

        self.assertEqual(result, 0)

    def test_run_prepares_local_repositories_with_update(self) -> None:
        options = provision.ProvisionOptions(
            config_path=Path("config.toml"), secrets=Path("secrets.toml")
        )
        with (
            mock.patch(
                "showco.provision.provision.config.read_toml",
                side_effect=[values(), {}],
            ),
            mock.patch("showco.provision.provision.validate_config"),
            mock.patch(
                "showco.update.prepare_local_repositories", return_value=True
            ) as prepare,
            mock.patch("showco.provision.remote.provision_remote"),
        ):
            provision.run(options)

        self.assertEqual(prepare.call_args.args[0], update.REPOSITORY_NAMES)

    def test_run_passes_system_update_to_remote_provisioning(self) -> None:
        options = provision.ProvisionOptions(
            config_path=Path("config.toml"), secrets=Path("secrets.toml"), system=True
        )
        with (
            mock.patch(
                "showco.provision.provision.config.read_toml",
                side_effect=[values(), {}],
            ),
            mock.patch("showco.provision.provision.validate_config"),
            mock.patch("showco.update.prepare_local_repositories", return_value=True),
            mock.patch("showco.provision.remote.provision_remote") as provision_remote,
        ):
            provision.run(options)

        self.assertTrue(provision_remote.call_args.kwargs["system"])

    def test_lyte_defaults_to_disabled(self) -> None:
        parsed = make_config(values())

        self.assertFalse(parsed.lyte.enabled)
        self.assertEqual(
            parsed.lyte.daemon_config, Path("patches/wearable-daemon.toml")
        )

    def test_git_repositories_default_to_github(self) -> None:
        configuration = values()
        del configuration["git"]

        parsed = make_config(configuration)

        self.assertEqual(parsed.git.reccy.url, "https://github.com/rec/reccy.git")
        self.assertEqual(parsed.git.recs.url, "https://github.com/rec/recs.git")
        self.assertEqual(parsed.git.twitcho.url, "https://github.com/rec/twitcho.git")
        self.assertEqual(parsed.git.showco.url, "https://github.com/rec/showco.git")
        self.assertEqual(parsed.git.lyte.url, "https://github.com/rec/lyte.git")

    def test_lyte_daemon_config_must_be_within_lyte_checkout(self) -> None:
        with self.assertRaisesRegex(SystemExit, "must be relative"):
            make_config(values(lyte={"daemon_config": "/etc/lyte.toml"}))

    def test_wired_x18_uses_configured_x18_host(self) -> None:
        config = make_config(
            values(),
        )

        self.assertEqual(
            config.networks["internal"]["wired"]["x18"].ip_address,
            "10.0.0.18",
        )
        x18 = next(mixer for mixer in config.mixers if mixer.name == "X18")
        self.assertEqual(x18.probe.host if x18.probe else "", "10.0.0.18")
        self.assertEqual(x18.osc.port if x18.osc else 0, 10024)

    def test_duplicate_mixer_names_are_rejected(self) -> None:
        with self.assertRaisesRegex(SystemExit, "mixer names must be unique"):
            make_config(
                values(
                    mixers=[
                        {"name": "X18"},
                        {"name": "X18"},
                    ]
                )
            )

    def test_mixer_ip_address_and_port_must_be_provided_together(self) -> None:
        with self.assertRaisesRegex(SystemExit, "must be provided together"):
            make_config(
                values(
                    mixers=[
                        {
                            "name": "X18",
                            "ip_address": 18,
                        }
                    ]
                )
            )
        with self.assertRaisesRegex(SystemExit, "must be provided together"):
            make_config(values(mixers=[{"name": "X18", "port": 10024}]))

    def test_mixer_endpoint_requires_a_host(self) -> None:
        with self.assertRaisesRegex(SystemExit, "must not be empty"):
            make_config(
                values(
                    mixers=[
                        {
                            "name": "Flow 8",
                            "probe": {"host": "", "port": 10024},
                        }
                    ]
                )
            )

    def test_unwired_x18_omits_x18_host(self) -> None:
        config = make_config(
            values(networks=networks(x18=False)),
        )

        self.assertEqual(config.networks["internal"]["wired"], {})

    def test_ssh_port_defaults_to_22(self) -> None:
        config = make_config(
            values(networks=networks(x18=False)),
        )

        self.assertEqual(config.network.ssh_port, 22)

    def test_web_port_is_integer(self) -> None:
        config = make_config(
            values(
                network={"web_port": 17353},
                networks=networks(x18=False),
            ),
        )

        self.assertEqual(config.network.web_port, 17353)

    def test_root_is_read_from_paths_table(self) -> None:
        parsed = make_config(values(paths={"root": "/srv/show-projects"}))

        self.assertEqual(parsed.paths.root, Path("/srv/show-projects"))

    def test_root_expands_environment_variables_and_home(self) -> None:
        with mock.patch.dict(
            "os.environ", {"SHOWCO_ROOT": "/srv/show-projects"}, clear=False
        ):
            environment_root = make_config(values(paths={"root": "$SHOWCO_ROOT"}))
        home_root = make_config(values(paths={"root": "~/show-projects"}))

        self.assertEqual(environment_root.paths.root, Path("/srv/show-projects"))
        self.assertEqual(home_root.paths.root, Path.home() / "show-projects")

    def test_root_rejects_relative_path(self) -> None:
        with self.assertRaisesRegex(SystemExit, "paths.root must be an absolute"):
            make_config(values(paths={"root": "show-projects"}))

    def test_config_validation_reports_missing_private_wifi_password(self) -> None:
        config = make_config(
            values(
                networks=networks(
                    internal_wifi={"name": "showbox"},
                    external_wifi={"name": "Venue"},
                )
            ),
        )

        with self.assertRaises(SystemExit) as error:
            provision.validate_config(config)

        self.assertIn(
            "networks.internal.wifi.private.password is required",
            str(error.exception),
        )

    def test_config_validation_accepts_passwordless_external_network(self) -> None:
        config = make_config(
            values(
                networks=networks(
                    internal_wifi={
                        "name": "showbox",
                        "password": "private password",
                    },
                    external_wifi={
                        "name": "Venue",
                    },
                )
            ),
        )

        provision.validate_config(config)

    def test_config_validation_rejects_invalid_private_wifi_password(self) -> None:
        config = make_config(
            values(
                networks=networks(
                    internal_wifi={"name": "showbox", "password": "short"},
                    external_wifi={"name": "Venue"},
                )
            )
        )

        with self.assertRaisesRegex(SystemExit, "must be 8-63"):
            provision.validate_config(config)

    def test_local_checkout_directory_is_parent_of_showco_checkout(self) -> None:
        self.assertEqual(
            provision.local_checkout_directory(),
            Path(__file__).resolve().parents[2],
        )

    def test_persist_network_host_replaces_existing_host(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('[network]\nhost = "old.local"\nweb_port = 17352\n')

            provision.persist_network_host(path, "bertrand.local")

            self.assertEqual(
                path.read_text(),
                '[network]\nhost = "bertrand.local"\nweb_port = 17352\n',
            )

    def test_persist_network_host_adds_missing_host(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text("[network]\nweb_port = 17352\n")

            provision.persist_network_host(path, "bertrand.local")

            self.assertEqual(
                path.read_text(),
                '[network]\nhost = "bertrand.local"\nweb_port = 17352\n',
            )

    def test_persist_paths_root_adds_paths_table(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('[network]\nhost = "bertrand.local"\n')

            provision.persist_paths_root(path, Path("/srv/show-projects"))

            self.assertEqual(
                path.read_text(),
                '[paths]\nroot = "/srv/show-projects"\n\n'
                '[network]\nhost = "bertrand.local"\n',
            )

    def test_network_preflight_rejects_connected_hotspot_interface(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        with (
            mock.patch(
                "showco.provision.ssh.capture_ssh",
                return_value="wlan0:wifi:connected\n",
            ),
            self.assertRaisesRegex(SystemExit, "no unconnected Wi-Fi interface"),
        ):
            remote.preflight_network_config(config)

    def test_network_preflight_preserves_connected_external_wifi(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        with mock.patch(
            "showco.provision.ssh.capture_ssh",
            return_value="wlan0:wifi:disconnected\nwlan1:wifi:connected\n",
        ) as capture_ssh:
            topology = remote.preflight_network_config(config)

        capture_ssh.assert_called_once_with(
            config,
            "nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device status",
        )
        self.assertEqual(topology, network_config.NetworkTopology.PRIVATE)

    def test_network_preflight_reuses_existing_private_hotspot(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        with mock.patch(
            "showco.provision.ssh.capture_ssh",
            return_value=(
                "wlan0:wifi:connected:Livebox\nwlan1:wifi:connected:showco-private\n"
            ),
        ):
            topology = remote.preflight_network_config(config)

        self.assertEqual(topology, network_config.NetworkTopology.PRIVATE)

    def test_remote_script_is_removed_after_remote_failure(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        original_error = subprocess.CalledProcessError(1, ["ssh", "provision"])
        cleanup_error = subprocess.CalledProcessError(1, ["ssh", "cleanup"])
        with (
            mock.patch(
                "showco.provision.ssh.run_ssh",
                side_effect=[None, None, original_error, cleanup_error],
            ) as run_ssh,
            mock.patch("showco.provision.ssh.wait_for_ssh"),
            mock.patch(
                "showco.provision.remote.preflight_network_config",
                return_value=network_config.NetworkTopology.PRIVATE,
            ),
            mock.patch("showco.provision.remote.validate_remote_worktrees"),
            mock.patch("showco.provision.ssh.run_scp"),
            self.assertRaises(subprocess.CalledProcessError) as error,
        ):
            remote.provision_remote(
                config,
                Path("/tmp/local.sh"),
                "/tmp/remote.sh",
            )

        self.assertIs(error.exception, original_error)
        self.assertEqual(run_ssh.call_args_list[-1].args[1], "rm -f /tmp/remote.sh")

    def test_run_uses_key_based_ssh_command(self) -> None:
        with mock.patch("reccy.subprocess.run") as run:
            ssh.run_command(["ssh"])

        run.assert_called_once_with(
            ["ssh"],
            capture_output=False,
            check=True,
            text=True,
        )

    def test_provision_checks_worktrees_before_network_preflight(self) -> None:
        calls: list[str] = []
        config = make_config(values(networks=networks(x18=False)))

        def preflight_network_config(config: config.Config) -> None:
            calls.append("preflight")

        def validate_remote_worktrees(config: config.Config) -> None:
            calls.append("worktrees")

        def run_scp(config: config.Config, source: Path, remote_path: str) -> None:
            calls.append("scp")

        with (
            mock.patch("showco.provision.ssh.run_ssh"),
            mock.patch(
                "showco.provision.remote.preflight_network_config",
                side_effect=preflight_network_config,
            ),
            mock.patch(
                "showco.provision.remote.validate_remote_worktrees",
                side_effect=validate_remote_worktrees,
            ),
            mock.patch("showco.provision.ssh.run_scp", side_effect=run_scp),
            mock.patch("showco.provision.ssh.wait_for_ssh"),
            mock.patch("showco.provision.ssh.wait_for_rebooted_ssh"),
            mock.patch(
                "showco.provision.ssh.provisioning_reboot_required",
                return_value=False,
            ),
            mock.patch(
                "showco.provision.verify.verify_provisioning",
                return_value=[],
            ),
            mock.patch("showco.provision.verify.report_verification_results"),
        ):
            remote.provision_remote(
                config,
                Path("/tmp/local.sh"),
                "/tmp/remote.sh",
            )

        self.assertEqual(calls, ["worktrees", "preflight", "scp"])

    def test_provision_requires_passwordless_sudo_before_remote_checks(self) -> None:
        provision_config = make_config(values(networks=networks(x18=False)))
        with (
            mock.patch("showco.provision.ssh.run_ssh") as run_ssh,
            mock.patch("showco.provision.ssh.wait_for_ssh"),
            mock.patch("showco.provision.remote.validate_remote_worktrees"),
            mock.patch(
                "showco.provision.remote.preflight_network_config",
                return_value=network_config.NetworkTopology.PRIVATE,
            ),
            mock.patch("showco.provision.ssh.run_scp"),
            mock.patch(
                "showco.provision.ssh.provisioning_reboot_required",
                return_value=False,
            ),
            mock.patch(
                "showco.provision.verify.wait_for_provisioning_ready",
                return_value=[],
            ),
            mock.patch("showco.provision.verify.report_verification_results"),
        ):
            remote.provision_remote(
                provision_config,
                Path("/tmp/local.sh"),
                "/tmp/remote.sh",
            )

        self.assertEqual(
            run_ssh.call_args_list[0].args[1], remote.PASSWORDLESS_SUDO_COMMAND
        )

    def test_fingerprint_changes_with_configuration_or_script(self) -> None:
        provision_config = make_config(values())
        changed_config = make_config(values(network={"web_port": 10000}))

        fingerprint = state.provisioning_fingerprint(provision_config, "script")

        self.assertNotEqual(
            fingerprint, state.provisioning_fingerprint(changed_config, "script")
        )
        self.assertNotEqual(
            fingerprint, state.provisioning_fingerprint(provision_config, "changed")
        )

    def test_provision_waits_for_reboot_and_reports_verification(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        result = [verify.VerificationResult(name="showco", error="")]
        with (
            mock.patch("showco.provision.ssh.run_ssh"),
            mock.patch(
                "showco.provision.remote.preflight_network_config",
                return_value=network_config.NetworkTopology.PRIVATE,
            ),
            mock.patch("showco.provision.remote.validate_remote_worktrees"),
            mock.patch("showco.provision.ssh.run_scp"),
            mock.patch("showco.provision.ssh.wait_for_ssh") as initial_wait,
            mock.patch("showco.provision.ssh.remove_known_host") as remove_host,
            mock.patch(
                "showco.provision.ssh.provisioning_reboot_required",
                return_value=True,
            ),
            mock.patch("showco.provision.ssh.schedule_remote_reboot") as schedule,
            mock.patch("showco.provision.ssh.wait_for_rebooted_ssh") as wait,
            mock.patch(
                "showco.provision.verify.verify_provisioning",
                return_value=result,
            ) as verification,
            mock.patch("showco.provision.verify.report_verification_results") as report,
        ):
            remote.provision_remote(
                config,
                Path("/tmp/local.sh"),
                "/tmp/remote.sh",
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
            mock.patch("showco.provision.ssh.run_ssh"),
            mock.patch(
                "showco.provision.remote.preflight_network_config",
                return_value=network_config.NetworkTopology.PRIVATE,
            ),
            mock.patch("showco.provision.remote.validate_remote_worktrees"),
            mock.patch("showco.provision.ssh.run_scp"),
            mock.patch("showco.provision.ssh.wait_for_ssh"),
            mock.patch(
                "showco.provision.ssh.provisioning_reboot_required",
                return_value=False,
            ),
            mock.patch(
                "showco.provision.verify.wait_for_provisioning_ready", return_value=[]
            ),
            mock.patch("showco.provision.verify.report_verification_results"),
            mock.patch(
                "showco.provision.remote.record_applied_provisioning_fingerprint"
            ) as record,
        ):
            remote.provision_remote(
                provision_config,
                Path("/tmp/local.sh"),
                "/tmp/remote.sh",
                fingerprint="a" * 64,
            )

        record.assert_called_once_with(provision_config, "a" * 64)

    def test_provision_does_not_wait_for_reboot_when_not_required(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        with (
            mock.patch("showco.provision.ssh.run_ssh"),
            mock.patch(
                "showco.provision.remote.preflight_network_config",
                return_value=network_config.NetworkTopology.PRIVATE,
            ),
            mock.patch("showco.provision.remote.validate_remote_worktrees"),
            mock.patch("showco.provision.ssh.run_scp"),
            mock.patch("showco.provision.ssh.wait_for_ssh"),
            mock.patch(
                "showco.provision.ssh.provisioning_reboot_required",
                return_value=False,
            ),
            mock.patch("showco.provision.ssh.schedule_remote_reboot") as schedule,
            mock.patch("showco.provision.ssh.wait_for_rebooted_ssh") as wait,
            mock.patch("showco.provision.verify.verify_provisioning", return_value=[]),
            mock.patch("showco.provision.verify.report_verification_results"),
        ):
            remote.provision_remote(
                config,
                Path("/tmp/local.sh"),
                "/tmp/remote.sh",
            )

        schedule.assert_not_called()
        wait.assert_not_called()

    def test_wait_for_provisioning_ready_retries_startup_checks(self) -> None:
        starting = [
            verify.VerificationResult(name="recs service is active", error="activating")
        ]
        ready = [verify.VerificationResult(name="recs service is active", error="")]
        config = make_config(values())
        with (
            mock.patch(
                "showco.provision.verify.verify_provisioning",
                side_effect=[starting, ready],
            ) as verification,
            mock.patch("showco.provision.ssh.time.sleep") as sleep,
        ):
            result = verify.wait_for_provisioning_ready(
                config,
                network_config.NetworkTopology.MIXED,
            )

        self.assertEqual(result, ready)
        self.assertEqual(verification.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_lyte_service_is_a_startup_check(self) -> None:
        self.assertIn("Lyte service", verify.STARTUP_CHECK_NAMES)

    def test_twitcho_health_command_uses_target_showco(self) -> None:
        command = verify.showco_twitcho_health_command(Path("/code"))

        self.assertIn("cd /code/showco", command)
        self.assertIn("uv run --locked showco run twitcho-health", command)

    def test_initial_wait_for_ssh_retries_until_connected(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        with (
            mock.patch(
                "showco.provision.ssh.ssh_is_reachable",
                side_effect=[False, False, True],
            ) as reachable,
            mock.patch("showco.provision.ssh.time.sleep") as sleep,
        ):
            ssh.wait_for_ssh(config)

        self.assertEqual(reachable.call_count, 3)
        sleep.assert_has_calls([mock.call(1), mock.call(1)])

    def test_changed_host_key_is_removed_during_ssh_retry(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        changed_key = subprocess.CompletedProcess(
            ["ssh"],
            255,
            "",
            "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!",
        )
        connected = subprocess.CompletedProcess(["ssh"], 0, "", "")
        removed = subprocess.CompletedProcess(["ssh-keygen"], 0, "", "")
        with mock.patch(
            "reccy.subprocess.run", side_effect=[changed_key, removed, connected]
        ) as run:
            self.assertFalse(ssh.ssh_is_reachable(config))
            self.assertTrue(ssh.ssh_is_reachable(config))

        self.assertEqual(
            run.call_args_list[1].args[0],
            ["ssh-keygen", "-R", "recs-stage.local"],
        )

    def test_config_uses_ssh_target_and_accepts_changed_host_keys(self) -> None:
        config = make_config(values(networks=networks(x18=False)))

        self.assertEqual(config.ssh_target, "tom@recs-stage.local")
        self.assertTrue(config.accept_changed_host_key)

    def test_changed_host_key_requires_explicit_acceptance(self) -> None:
        config = make_config(
            values(networks=networks(x18=False), accept_changed_host_key=False)
        )
        changed_key = subprocess.CompletedProcess(
            ["ssh"], 255, "", "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!"
        )
        with mock.patch("reccy.subprocess.run", return_value=changed_key):
            with self.assertRaisesRegex(SystemExit, "accept_changed_host_key"):
                ssh.ssh_is_reachable(config)

    def test_ssh_retry_uses_non_interactive_host_key_options(self) -> None:
        config = make_config(values(networks=networks(x18=False)))

        command = ssh.ssh_command(
            config,
            config.ssh_target,
            "true",
            connect_timeout=1,
        )

        self.assertIn("BatchMode=yes", command)
        self.assertIn("StrictHostKeyChecking=accept-new", command)
        self.assertIn("ConnectTimeout=1", command)

    def test_ssh_command_uses_short_default_connect_timeout(self) -> None:
        config = make_config(values(networks=networks(x18=False)))

        command = ssh.ssh_command(config, config.ssh_target, "true")

        self.assertIn("ConnectTimeout=2", command)

    def test_run_scp_uses_short_connect_timeout(self) -> None:
        config = make_config(values(networks=networks(x18=False)))

        with mock.patch("reccy.subprocess.run") as run:
            ssh.run_scp(config, Path("/tmp/local.sh"), "/tmp/remote.sh")

        self.assertIn("ConnectTimeout=2", run.call_args.args[0])

    def test_run_ssh_reports_connection_failure_without_traceback(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        error = subprocess.CalledProcessError(
            255,
            ["ssh"],
            stderr="ssh: connect to host failed\n",
        )

        with (
            mock.patch("reccy.subprocess.run", side_effect=error),
            self.assertRaises(SystemExit) as exit_error,
        ):
            ssh.run_ssh(config, "true")

        self.assertIn(
            "ERROR: SSH connection or command failed for tom@recs-stage.local.",
            str(exit_error.exception),
        )
        self.assertIn("SSH connect timeout is 2 seconds.", str(exit_error.exception))
        self.assertIn(
            "ssh said: ssh: connect to host failed", str(exit_error.exception)
        )

    def test_known_host_names_include_port_specific_host(self) -> None:
        config = make_config(
            values(networks=networks(x18=False)),
            port=2200,
        )

        self.assertEqual(
            ssh.known_host_names(config),
            ["recs-stage.local", "[recs-stage.local]:2200"],
        )

    def test_remote_script_is_loaded_from_template_file(self) -> None:
        template = script.SCRIPT_DIR / script.REMOTE_SCRIPT_TEMPLATE

        self.assertEqual(script.REMOTE_SCRIPT, template.read_text())

    def test_remote_script_configures_external_storage_mounts(self) -> None:
        self.assertIn("exfatprogs", script.REMOTE_SCRIPT)
        self.assertIn('phase "configuring storage mounts"', script.REMOTE_SCRIPT)
        self.assertIn("lsblk -f", script.REMOTE_SCRIPT)
        self.assertIn("UUID=%s %s %s %s 0 2", script.REMOTE_SCRIPT)
        self.assertIn("fstab_mountpoint_for_uuid()", script.REMOTE_SCRIPT)
        self.assertIn("mount_target_for_disk()", script.REMOTE_SCRIPT)
        self.assertIn('target="/mnt/$name-$suffix"', script.REMOTE_SCRIPT)

    def test_remote_script_preserves_broken_checkouts_for_reruns(self) -> None:
        self.assertIn("prepare_checkout_path()", script.REMOTE_SCRIPT)
        self.assertIn("Moving non-git checkout aside", script.REMOTE_SCRIPT)
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
        self.assertIn("uv --version", script.REMOTE_SCRIPT)

    def test_remote_script_checks_before_syncing_unchanged_environments(self) -> None:
        self.assertIn("uv sync --locked", script.REMOTE_SCRIPT)
        self.assertIn("uv sync --locked --check", script.REMOTE_SCRIPT)

    def test_remote_script_configures_github_urls_before_syncing(self) -> None:
        github_urls = script.REMOTE_SCRIPT.index(
            'git config --global url."https://github.com/".insteadOf'
        )
        syncing = script.REMOTE_SCRIPT.index('phase "syncing repositories"')

        self.assertLess(github_urls, syncing)

    def test_remote_script_skips_unchanged_system_and_services(self) -> None:
        self.assertIn("install_base_packages()", script.REMOTE_SCRIPT)
        self.assertIn("Base packages are already installed.", script.REMOTE_SCRIPT)
        self.assertIn("service_is_current()", script.REMOTE_SCRIPT)
        self.assertIn("record_service_state()", script.REMOTE_SCRIPT)
        self.assertIn("completed in %ss", script.REMOTE_SCRIPT)

    def test_remote_script_uses_locked_uv_run(self) -> None:
        self.assertIn("uv run --locked showco run network-config", script.REMOTE_SCRIPT)
        self.assertIn("uv run --locked recs daemon install", script.REMOTE_SCRIPT)
        self.assertIn("uv run --locked twitcho daemon install", script.REMOTE_SCRIPT)
        self.assertIn("uv run --locked lyte daemon install", script.REMOTE_SCRIPT)
        self.assertIn(
            "uv run --locked showco run install-service", script.REMOTE_SCRIPT
        )

    def test_remote_script_writes_provisioning_report(self) -> None:
        self.assertIn('phase "writing provisioning report"', script.REMOTE_SCRIPT)
        self.assertIn("Disks discovered:", script.REMOTE_SCRIPT)
        self.assertIn("Wi-Fi interfaces discovered:", script.REMOTE_SCRIPT)
        self.assertIn("nmcli device status", script.REMOTE_SCRIPT)
        self.assertIn("iw dev", script.REMOTE_SCRIPT)
        self.assertIn("Lyte:", script.REMOTE_SCRIPT)
        self.assertIn("lyte service:", script.REMOTE_SCRIPT)
        self.assertIn("Twitcho:", script.REMOTE_SCRIPT)
        self.assertIn("twitcho service:", script.REMOTE_SCRIPT)
        self.assertIn("PROVISIONING-REPORT.txt", script.REMOTE_SCRIPT)

    def test_remote_script_marks_target_machine(self) -> None:
        self.assertIn(
            '"/home/$SHOW_USER/.config/showco/machine-role"',
            script.REMOTE_SCRIPT,
        )
        self.assertIn("printf 'target\\n'", script.REMOTE_SCRIPT)

    def test_remote_script_configures_network(self) -> None:
        self.assertIn('phase "configuring network"', script.REMOTE_SCRIPT)
        self.assertIn("configure_network()", script.REMOTE_SCRIPT)
        self.assertIn("uv run --locked showco run network-config", script.REMOTE_SCRIPT)
        self.assertIn('write_toml_string host "$SHOWCO_HOST"', script.REMOTE_SCRIPT)
        self.assertIn("printf '\\n[git.reccy]\\n'", script.REMOTE_SCRIPT)
        self.assertIn("printf '\\n[git.lyte]\\n'", script.REMOTE_SCRIPT)
        self.assertIn("Skipping network configuration", script.REMOTE_SCRIPT)

    def test_remote_script_installs_showco_service_through_showco(self) -> None:
        self.assertIn(
            "uv run --locked showco run install-service", script.REMOTE_SCRIPT
        )
        self.assertNotIn('tee "$service_file"', script.REMOTE_SCRIPT)
        self.assertNotIn("ExecStart=$command", script.REMOTE_SCRIPT)

    def test_remote_script_restarts_changed_services(self) -> None:
        self.assertIn("user_systemctl restart recs.service", script.REMOTE_SCRIPT)
        self.assertIn("user_systemctl restart showco.service", script.REMOTE_SCRIPT)
        self.assertIn("user_systemctl restart twitcho.service", script.REMOTE_SCRIPT)
        self.assertIn("user_systemctl restart lyte.service", script.REMOTE_SCRIPT)

    def test_remote_script_reboots_only_when_the_system_requires_it(self) -> None:
        network = script.REMOTE_SCRIPT.index('phase "configuring network"')
        reboot = script.REMOTE_SCRIPT.index('phase "rebooting"')

        self.assertIn(
            "sudo touch /run/showco-provision-reboot-required",
            script.REMOTE_SCRIPT,
        )
        self.assertNotIn(
            "sudo systemd-run --on-active=2s /usr/bin/systemctl reboot",
            script.REMOTE_SCRIPT,
        )
        self.assertNotIn("NETWORK_CHANGED", script.REMOTE_SCRIPT)
        self.assertIn(
            "if [[ -f /var/run/reboot-required ]]; then", script.REMOTE_SCRIPT
        )
        self.assertLess(
            script.REMOTE_SCRIPT.index(
                "sudo rm -f /run/showco-provision-reboot-required"
            ),
            network,
        )
        self.assertLess(network, reboot)

    def test_remote_script_configures_locale_before_package_updates(self) -> None:
        main = script.REMOTE_SCRIPT.rindex("main() {")
        locale = script.REMOTE_SCRIPT.index('phase "configuring locale"', main)
        journal = script.REMOTE_SCRIPT.index(
            'phase "configuring persistent journal"', main
        )
        install = script.REMOTE_SCRIPT.index(
            'install_base_packages "${packages[@]}"', main
        )

        self.assertLess(locale, journal)
        self.assertIn("sudo apt-get update", script.REMOTE_SCRIPT)
        self.assertIn("sudo apt-get upgrade -y", script.REMOTE_SCRIPT)
        self.assertLess(journal, install)

    def test_remote_script_configures_persistent_journal(self) -> None:
        self.assertIn("Storage=persistent", script.REMOTE_SCRIPT)
        self.assertIn("/var/log/journal", script.REMOTE_SCRIPT)
        self.assertIn("systemctl restart systemd-journald", script.REMOTE_SCRIPT)

    def test_remote_script_exports_locale_before_package_updates(self) -> None:
        main = script.REMOTE_SCRIPT.rindex("main() {")
        locale = script.REMOTE_SCRIPT.index("configure_locale", main)
        install = script.REMOTE_SCRIPT.index(
            'install_base_packages "${packages[@]}"', main
        )
        self.assertIn("unset LC_ALL", script.REMOTE_SCRIPT)
        self.assertLess(locale, install)

    def test_read_toml_preserves_string_lists(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('[twitch]\ntags = ["Live Music", "Music"]\n')

            values = config.read_toml(path)

        self.assertEqual(
            config.table_value(values, "twitch")["tags"],
            ["Live Music", "Music"],
        )

    def test_network_secrets_merge_by_key(self) -> None:
        config_values = {
            "networks": {
                "external": {
                    "wifi": {
                        "venue": {"name": "Venue"},
                    },
                },
            },
        }
        secrets = {
            "networks": {
                "external": {
                    "wifi": {
                        "venue": {"password": "venue password"},
                    },
                },
            },
        }

        values = config.merge_values(config_values, secrets)

        self.assertEqual(
            values["networks"]["external"]["wifi"]["venue"],
            {
                "name": "Venue",
                "password": "venue password",
            },
        )

    def test_remote_command_passes_network_config(self) -> None:
        config = make_config(
            values(
                networks=networks(
                    x18=False,
                    internal_wifi={"password": "private password"},
                    external_wifi={
                        "name": "Venue",
                        "password": "venue password",
                    },
                ),
            ),
        )

        command = script.remote_command(config, "/tmp/provision.sh")

        self.assertIn("SHOWCO_HOST=recs-stage.local", command)
        self.assertIn("ROOT=/srv/show-projects", command)
        self.assertIn("RECCY_REFNAME=''", command)
        self.assertIn("EXTERNAL_WIFI_SSID=Venue", command)
        self.assertIn("EXTERNAL_WIFI_PASSWORD='venue password'", command)
        self.assertIn("PRIVATE_WIFI_PASSWORD='private password'", command)
        self.assertIn("X18=false", command)
        self.assertIn("RECS_REFNAME=''", command)

    def test_remote_command_passes_system_update_request(self) -> None:
        command = script.remote_command(
            make_config(values(networks=networks(x18=False))),
            "/tmp/provision.sh",
            system=True,
        )

        self.assertIn("SYSTEM_UPDATE=true", command)

    def test_remote_worktree_command_reports_all_tracked_changes(self) -> None:
        command = shlex.split(
            remote.remote_worktree_command(Path("/srv/show-projects"))
        )[2]

        self.assertIn("for name in showco reccy recs twitcho lyte", command)
        self.assertIn('git -C "$path" status --short --untracked-files=no', command)
        self.assertNotIn("sed -E '/^.. (.*\\/)?uv\\.lock$/d'", command)
        self.assertIn('printf \'%s:\\n%s\\n\' "$name" "$status"', command)
        self.assertIn("printf '%s: not a Git checkout: %s\\n'", command)
        self.assertIn("exit 1", command)

    def test_project_status_command_reports_dirty_uv_lock(self) -> None:
        command = verify.project_status_command("recs", Path("/srv/show-projects"))

        self.assertEqual(
            command,
            "git -C /srv/show-projects/recs status --short",
        )

    def test_remote_command_quotes_root_with_spaces(self) -> None:
        parsed = make_config(values(paths={"root": "/srv/show projects"}))

        command = script.remote_command(parsed, "/tmp/provision.sh")

        self.assertIn("ROOT='/srv/show projects'", command)

    def test_remote_command_passes_git_refname(self) -> None:
        config = make_config(
            values(
                networks=networks(x18=False),
                git={"recs": {"refname": "my-branch"}},
            ),
        )

        command = script.remote_command(config, "/tmp/provision.sh")

        self.assertIn("RECS_REFNAME=my-branch", command)

    def test_remote_command_passes_enabled_from_twitch_table(self) -> None:
        config = make_config(
            values(
                networks=networks(x18=False),
                twitch={"enabled": True},
            ),
        )

        command = script.remote_command(config, "/tmp/provision.sh")

        self.assertIn("TWITCHO_ENABLED=true", command)

    def test_wait_for_rebooted_ssh_waits_for_disconnect_then_connect(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        with (
            mock.patch(
                "showco.provision.ssh.ssh_is_reachable",
                side_effect=[True, False, False, True],
            ) as reachable,
            mock.patch("showco.provision.ssh.time.sleep"),
        ):
            ssh.wait_for_rebooted_ssh(config)

        self.assertEqual(reachable.call_count, 4)

    def test_verify_provisioning_checks_projects_and_user_services(self) -> None:
        config = make_config(values())
        with mock.patch(
            "reccy.subprocess.run",
            return_value=subprocess.CompletedProcess(["ssh"], 0, "", ""),
        ) as run:
            results = verify.verify_provisioning(
                config,
                network_config.NetworkTopology.MIXED,
            )

        commands = [c.args[0][-1] for c in run.call_args_list]
        self.assertFalse([r for r in results if r.error])
        self.assertIn(
            verify.project_statuses_command(Path("/srv/show-projects")),
            commands,
        )
        self.assertIn(
            "uid=$(id -u); XDG_RUNTIME_DIR=/run/user/$uid "
            'cd /srv/show-projects/showco && PATH="$HOME/.local/bin:$PATH" '
            "uv run --locked showco run service-status recs",
            commands,
        )
        self.assertIn(
            "nmcli -t -f TYPE,STATE,CONNECTION device status "
            "| grep -F -x 'wifi:connected:showco-private'",
            commands,
        )
        self.assertIn(
            "ip -4 -o address show dev br-x18 | grep -F 10.0.0.1/24",
            commands,
        )
        self.assertIn(
            "uid=$(id -u); XDG_RUNTIME_DIR=/run/user/$uid "
            'cd /srv/show-projects/showco && PATH="$HOME/.local/bin:$PATH" '
            "uv run --locked showco run service-status showco",
            commands,
        )
        self.assertTrue(
            any('status="$HOME/.local/state/recs/status.json"' in c for c in commands)
        )
        self.assertTrue(
            any("systemd-cat --identifier=showco-provisioning" in c for c in commands)
        )

    def test_missing_mixer_devices_are_notes_not_errors(self) -> None:
        config = make_config(values())
        with mock.patch(
            "reccy.subprocess.run",
            return_value=subprocess.CompletedProcess(["ssh"], 1, "", ""),
        ):
            result = verify.verify_mixer_devices(config)

        self.assertTrue(all(value.error == "" for value in result))
        self.assertEqual(result[0].name, "X18 USB audio")
        self.assertEqual(result[0].note, "X18/XR18 not detected")

    def test_mixer_audio_device_check_accepts_any_selector(self) -> None:
        config = make_config(values())
        with mock.patch(
            "reccy.subprocess.run",
            return_value=subprocess.CompletedProcess(["ssh"], 0, "", ""),
        ) as run:
            result = verify.verify_mixer_audio_inputs(config, "X18", ["X18", "XR18"])

        self.assertIn(
            "arecord -l | grep -Fi -e X18 -e XR18 >/dev/null",
            run.call_args.args[0],
        )
        self.assertEqual(result.name, "X18 USB audio")

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
            script.unique_selectors(["X18", "XR18", "X18"]), ["X18", "XR18"]
        )
        self.assertEqual(len(tomllib.loads(rendered)["mixers"]), 2)
        self.assertIn("subscription_path = '/xremote'", rendered)
        self.assertEqual(osc.count("[[nodes]]"), 1)

    def test_report_verification_results_exits_with_errors(self) -> None:
        with self.assertRaises(SystemExit):
            verify.report_verification_results(
                [verify.VerificationResult(name="showco", error="inactive")]
            )

    def test_report_verification_results_allows_notes(self) -> None:
        verify.report_verification_results(
            [
                verify.VerificationResult(
                    name="X18 USB device",
                    error="",
                    note="X18/XR18 not detected",
                )
            ]
        )


def make_config(values: dict[str, object], *, port: int | None = None) -> config.Config:
    return config.config_from_values(values, port=port)


def values(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "network": {
            "host": "recs-stage.local",
            "user": "tom",
            "web_port": 17352,
        },
        "paths": {"root": "/srv/show-projects"},
        "networks": networks(),
        "mixers": [
            {
                "name": "X18",
                "audio_device_names": ["X18", "XR18"],
                "port": 10024,
                "ip_address": 18,
                "probe": {"protocol": "udp"},
                "osc": {
                    "subscription_path": "/xremote",
                    "resubscribe_period": 10,
                },
            },
            {
                "name": "Flow 8",
                "audio_device_names": ["FLOW 8"],
                "midi_input_names": ["FLOW 8"],
            },
        ],
        "git": {
            "reccy": {"url": "https://github.com/rec/reccy.git"},
            "recs": {"url": "https://github.com/rec/recs.git"},
            "twitcho": {"url": "https://github.com/rec/twitcho.git"},
            "lyte": {"url": "https://github.com/rec/lyte.git"},
            "showco": {"url": "https://github.com/rec/showco.git"},
        },
    }
    for k, v in overrides.items():
        if k == "networks":
            result[k] = v
        elif isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = config.merge_values(result[k], v)
        else:
            result[k] = v
    internal = result["networks"]["internal"]
    if not internal.pop("_x18", True):
        result["mixers"] = result["mixers"][1:]
    return result


def networks(
    *,
    x18: bool = True,
    internal_wifi: dict[str, object] | None = None,
    external_wifi: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "internal": {
            "_x18": x18,
            "subnet": "10.0.0.0/24",
            "wifi": {
                **{"name": "showbox", "ip_address": 1},
                **(internal_wifi or {}),
            },
        },
        "external": {
            "wifi": external_wifi or {},
        },
    }


if __name__ == "__main__":
    unittest.main()
