from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import tyro

from showco import network_config
from showco.provision import config, provision


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
                "git@github.com:rec/recs.git",
            ],
        )

        self.assertEqual(options.host, "bertrand.local")
        self.assertEqual(options.root, Path("/srv/show-projects"))
        self.assertEqual(options.recs_repo, "git@github.com:rec/recs.git")

    def test_wired_x18_uses_configured_x18_host(self) -> None:
        config = make_config(
            values(),
        )

        self.assertEqual(
            config.networks["internal"]["wired"]["x18"].ip_address,
            "10.43.0.18",
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

    def test_local_repository_errors_pushes_ahead_repository(self) -> None:
        with TemporaryDirectory() as directory:
            repository = provision.LocalRepository(
                name="recs",
                path=Path(directory),
            )
            with mock.patch(
                "reccy.subprocess.run",
                side_effect=[
                    subprocess.CompletedProcess(["git"], 0, "true\n", ""),
                    subprocess.CompletedProcess(["git"], 0, "origin/main\n", ""),
                    subprocess.CompletedProcess(["git"], 0, "2\n", ""),
                    subprocess.CompletedProcess(["git"], 0, "", ""),
                    subprocess.CompletedProcess(["git"], 0, "0\n", ""),
                ],
            ) as run:
                errors = provision.repository_errors(repository)

        self.assertEqual(errors, [])
        push_command = run.call_args_list[3].args[0]
        self.assertEqual(
            push_command,
            ["git", "-C", str(Path(directory)), "push", "origin", "HEAD:main"],
        )
        self.assertNotIn("--force", push_command)

    def test_local_repository_errors_reports_push_failure(self) -> None:
        with TemporaryDirectory() as directory:
            repository = provision.LocalRepository(
                name="recs",
                path=Path(directory),
            )
            with mock.patch(
                "reccy.subprocess.run",
                side_effect=[
                    subprocess.CompletedProcess(["git"], 0, "true\n", ""),
                    subprocess.CompletedProcess(["git"], 0, "origin/main\n", ""),
                    subprocess.CompletedProcess(["git"], 0, "2\n", ""),
                    subprocess.CalledProcessError(
                        1,
                        ["git", "push"],
                        stderr="rejected\n",
                    ),
                ],
            ):
                errors = provision.repository_errors(repository)

        self.assertEqual(
            errors,
            ["- recs: could not push 2 local commit(s) to origin/main: rejected"],
        )

    def test_local_repository_errors_reports_missing_upstream(self) -> None:
        with TemporaryDirectory() as directory:
            repository = provision.LocalRepository(
                name="showco",
                path=Path(directory),
            )
            with mock.patch(
                "reccy.subprocess.run",
                side_effect=[
                    subprocess.CompletedProcess(["git"], 0, "true\n", ""),
                    subprocess.CalledProcessError(128, ["git"]),
                ],
            ):
                errors = provision.repository_errors(repository)

        self.assertEqual(errors, ["- showco: current branch has no upstream"])

    def test_validate_local_repositories_reports_all_deployed_repositories(
        self,
    ) -> None:
        with (
            mock.patch(
                "showco.provision.provision.repository_errors",
                side_effect=lambda r: [r.name],
            ),
            self.assertRaises(SystemExit) as error,
        ):
            provision.validate_local_repositories(Path("/code"))

        message = str(error.exception)
        self.assertIn("showco", message)
        self.assertIn("reccy", message)
        self.assertIn("recs", message)
        self.assertIn("twitcho", message)

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
                "showco.provision.provision.capture_ssh",
                return_value="wlan0:wifi:connected\n",
            ),
            self.assertRaisesRegex(SystemExit, "no unconnected Wi-Fi interface"),
        ):
            provision.preflight_network_config(config, "tom@recs-stage.local")

    def test_network_preflight_preserves_connected_external_wifi(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        with mock.patch(
            "showco.provision.provision.capture_ssh",
            return_value="wlan0:wifi:disconnected\nwlan1:wifi:connected\n",
        ) as capture_ssh:
            topology = provision.preflight_network_config(
                config, "tom@recs-stage.local"
            )

        capture_ssh.assert_called_once_with(
            config,
            "tom@recs-stage.local",
            "nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device status",
        )
        self.assertEqual(topology, network_config.NetworkTopology.PRIVATE)

    def test_network_preflight_reuses_existing_private_hotspot(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        with mock.patch(
            "showco.provision.provision.capture_ssh",
            return_value=(
                "wlan0:wifi:connected:Livebox\nwlan1:wifi:connected:showco-private\n"
            ),
        ):
            topology = provision.preflight_network_config(
                config, "tom@recs-stage.local"
            )

        self.assertEqual(topology, network_config.NetworkTopology.PRIVATE)

    def test_remote_script_is_removed_after_remote_failure(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        original_error = subprocess.CalledProcessError(1, ["ssh", "provision"])
        cleanup_error = subprocess.CalledProcessError(1, ["ssh", "cleanup"])
        with (
            mock.patch(
                "showco.provision.provision.run_ssh",
                side_effect=[None, original_error, cleanup_error],
            ) as run_ssh,
            mock.patch("showco.provision.provision.wait_for_ssh"),
            mock.patch(
                "showco.provision.provision.preflight_network_config",
                return_value=network_config.NetworkTopology.PRIVATE,
            ),
            mock.patch("showco.provision.provision.ensure_github_account_key"),
            mock.patch("showco.provision.provision.run_scp"),
            self.assertRaises(subprocess.CalledProcessError) as error,
        ):
            provision.provision_remote(
                config,
                "tom@recs-stage.local",
                Path("/tmp/local.sh"),
                "/tmp/remote.sh",
            )

        self.assertIs(error.exception, original_error)
        self.assertEqual(run_ssh.call_args_list[-1].args[2], "rm -f /tmp/remote.sh")

    def test_run_uses_key_based_ssh_command(self) -> None:
        with mock.patch("reccy.subprocess.run") as run:
            provision.run_command(["ssh"])

        run.assert_called_once_with(
            ["ssh"],
            capture_output=False,
            check=True,
            text=True,
        )

    def test_provision_adds_github_key_before_uploading_script(self) -> None:
        calls: list[str] = []
        config = make_config(values(networks=networks(x18=False)))

        def preflight_network_config(config: config.Config, ssh_target: str) -> None:
            calls.append("preflight")

        def ensure_github_account_key(config: config.Config, ssh_target: str) -> None:
            calls.append("github")

        def run_scp(config: config.Config, source: Path, target: str) -> None:
            calls.append("scp")

        with (
            mock.patch("showco.provision.provision.run_ssh"),
            mock.patch(
                "showco.provision.provision.preflight_network_config",
                side_effect=preflight_network_config,
            ),
            mock.patch(
                "showco.provision.provision.ensure_github_account_key",
                side_effect=ensure_github_account_key,
            ),
            mock.patch("showco.provision.provision.run_scp", side_effect=run_scp),
            mock.patch("showco.provision.provision.wait_for_ssh"),
            mock.patch("showco.provision.provision.wait_for_rebooted_ssh"),
            mock.patch(
                "showco.provision.provision.verify_provisioning",
                return_value=[],
            ),
            mock.patch("showco.provision.provision.report_verification_results"),
        ):
            provision.provision_remote(
                config,
                "tom@recs-stage.local",
                Path("/tmp/local.sh"),
                "/tmp/remote.sh",
            )

        self.assertEqual(calls, ["preflight", "github", "scp"])

    def test_provision_waits_for_reboot_and_reports_verification(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        result = [provision.VerificationResult(name="showco", error="")]
        with (
            mock.patch("showco.provision.provision.run_ssh"),
            mock.patch(
                "showco.provision.provision.preflight_network_config",
                return_value=network_config.NetworkTopology.PRIVATE,
            ),
            mock.patch("showco.provision.provision.ensure_github_account_key"),
            mock.patch("showco.provision.provision.run_scp"),
            mock.patch("showco.provision.provision.wait_for_ssh") as initial_wait,
            mock.patch("showco.provision.provision.wait_for_rebooted_ssh") as wait,
            mock.patch(
                "showco.provision.provision.verify_provisioning",
                return_value=result,
            ) as verify,
            mock.patch(
                "showco.provision.provision.report_verification_results"
            ) as report,
        ):
            provision.provision_remote(
                config,
                "tom@recs-stage.local",
                Path("/tmp/local.sh"),
                "/tmp/remote.sh",
            )

        initial_wait.assert_called_once_with(config, "tom@recs-stage.local")
        wait.assert_called_once_with(config, "tom@recs-stage.local")
        verify.assert_called_once_with(
            config,
            "tom@recs-stage.local",
            network_config.NetworkTopology.PRIVATE,
        )
        report.assert_called_once_with(result)

    def test_wait_for_provisioning_ready_retries_startup_checks(self) -> None:
        starting = [
            provision.VerificationResult(
                name="recs service is active", error="activating"
            )
        ]
        ready = [provision.VerificationResult(name="recs service is active", error="")]
        config = make_config(values())
        with (
            mock.patch(
                "showco.provision.provision.verify_provisioning",
                side_effect=[starting, ready],
            ) as verify,
            mock.patch("showco.provision.provision.time.sleep") as sleep,
        ):
            result = provision.wait_for_provisioning_ready(
                config,
                "tom@recs-stage.local",
                network_config.NetworkTopology.MIXED,
            )

        self.assertEqual(result, ready)
        self.assertEqual(verify.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_initial_wait_for_ssh_retries_until_connected(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        with (
            mock.patch(
                "showco.provision.provision.ssh_is_reachable",
                side_effect=[False, False, True],
            ) as reachable,
            mock.patch("showco.provision.provision.time.sleep") as sleep,
        ):
            provision.wait_for_ssh(config, "tom@recs-stage.local")

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
            self.assertFalse(provision.ssh_is_reachable(config, "tom@recs-stage.local"))
            self.assertTrue(provision.ssh_is_reachable(config, "tom@recs-stage.local"))

        self.assertEqual(
            run.call_args_list[1].args[0],
            ["ssh-keygen", "-R", "recs-stage.local"],
        )

    def test_ssh_retry_uses_non_interactive_host_key_options(self) -> None:
        config = make_config(values(networks=networks(x18=False)))

        command = provision.ssh_command(
            config,
            "tom@recs-stage.local",
            "true",
            connect_timeout=1,
        )

        self.assertIn("BatchMode=yes", command)
        self.assertIn("StrictHostKeyChecking=accept-new", command)
        self.assertIn("ConnectTimeout=1", command)

    def test_ssh_command_uses_short_default_connect_timeout(self) -> None:
        config = make_config(values(networks=networks(x18=False)))

        command = provision.ssh_command(config, "tom@recs-stage.local", "true")

        self.assertIn("ConnectTimeout=2", command)

    def test_run_scp_uses_short_connect_timeout(self) -> None:
        config = make_config(values(networks=networks(x18=False)))

        with mock.patch("reccy.subprocess.run") as run:
            provision.run_scp(config, Path("/tmp/local.sh"), "tom@host:/tmp/remote.sh")

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
            provision.run_ssh(config, "tom@host", "true")

        self.assertIn(
            "ERROR: SSH connection or command failed for tom@host.",
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
            provision.known_host_names(config, "tom@recs-stage.local"),
            ["recs-stage.local", "[recs-stage.local]:2200"],
        )

    def test_github_key_title_uses_host(self) -> None:
        config = make_config(values(networks=networks(x18=False)))

        self.assertEqual(provision.github_key_title(config), "showco recs-stage.local")

    def test_remote_github_key_command_writes_only_public_key_to_stdout(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        command = provision.remote_github_key_command(config)

        self.assertIn('} >&2\ncat "$HOME/.ssh/id_ed25519.pub"', command)

    def test_remote_github_key_command_regenerates_missing_public_key(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        command = provision.remote_github_key_command(config)

        self.assertIn('ssh-keygen -y -f "$HOME/.ssh/id_ed25519"', command)

    def test_remote_github_key_command_does_not_run_gh_on_pi(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        command = provision.remote_github_key_command(config)

        self.assertNotIn("gh ", command)
        self.assertNotIn("gh\n", command)

    def test_remote_github_key_command_is_loaded_from_template_file(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        template = provision.script_dir() / provision.REMOTE_GITHUB_KEY_TEMPLATE

        self.assertEqual(
            provision.remote_github_key_command(config),
            template.read_text().replace("{comment}", "'showco recs-stage.local'"),
        )

    def test_remote_script_is_loaded_from_template_file(self) -> None:
        template = provision.script_dir() / provision.REMOTE_SCRIPT_TEMPLATE

        self.assertEqual(provision.REMOTE_SCRIPT, template.read_text())

    def test_remote_script_configures_external_storage_mounts(self) -> None:
        self.assertIn("exfatprogs", provision.REMOTE_SCRIPT)
        self.assertIn('phase "configuring storage mounts"', provision.REMOTE_SCRIPT)
        self.assertIn("lsblk -f", provision.REMOTE_SCRIPT)
        self.assertIn("UUID=%s %s %s %s 0 2", provision.REMOTE_SCRIPT)
        self.assertIn("fstab_mountpoint_for_uuid()", provision.REMOTE_SCRIPT)
        self.assertIn("mount_target_for_disk()", provision.REMOTE_SCRIPT)
        self.assertIn('target="/mnt/$name-$suffix"', provision.REMOTE_SCRIPT)

    def test_remote_script_preserves_broken_checkouts_for_reruns(self) -> None:
        self.assertIn("prepare_checkout_path()", provision.REMOTE_SCRIPT)
        self.assertIn("Moving non-git checkout aside", provision.REMOTE_SCRIPT)
        self.assertIn('sudo mv "$path" "$backup"', provision.REMOTE_SCRIPT)

    def test_remote_script_discards_local_checkout_changes_before_pull(self) -> None:
        reset = provision.REMOTE_SCRIPT.index('git -C "$path" reset --hard HEAD')
        fetch = provision.REMOTE_SCRIPT.index('git -C "$path" fetch --all --prune')
        pull = provision.REMOTE_SCRIPT.index('git -C "$path" pull --ff-only')

        self.assertLess(reset, fetch)
        self.assertLess(fetch, pull)

    def test_remote_script_checks_out_configured_refname(self) -> None:
        self.assertIn('git -C "$path" checkout "$refname"', provision.REMOTE_SCRIPT)
        self.assertIn(
            'sync_repo reccy "$RECCY_REPO" "$RECCY_REFNAME"', provision.REMOTE_SCRIPT
        )
        self.assertIn(
            'sync_repo recs "$RECS_REPO" "$RECS_REFNAME"', provision.REMOTE_SCRIPT
        )

    def test_remote_script_reinstalls_broken_uv(self) -> None:
        self.assertIn("uv --version", provision.REMOTE_SCRIPT)

    def test_remote_script_uses_frozen_uv_sync(self) -> None:
        self.assertIn("uv sync --frozen", provision.REMOTE_SCRIPT)

    def test_remote_script_uses_frozen_uv_run(self) -> None:
        self.assertIn(
            "uv run --frozen showco run network-config", provision.REMOTE_SCRIPT
        )
        self.assertIn("uv run --frozen recs daemon install", provision.REMOTE_SCRIPT)
        self.assertIn(
            "uv run --frozen showco run install-service", provision.REMOTE_SCRIPT
        )

    def test_remote_script_writes_provisioning_report(self) -> None:
        self.assertIn('phase "writing provisioning report"', provision.REMOTE_SCRIPT)
        self.assertIn("Disks discovered:", provision.REMOTE_SCRIPT)
        self.assertIn("Wi-Fi interfaces discovered:", provision.REMOTE_SCRIPT)
        self.assertIn("nmcli device status", provision.REMOTE_SCRIPT)
        self.assertIn("iw dev", provision.REMOTE_SCRIPT)
        self.assertIn("PROVISIONING-REPORT.txt", provision.REMOTE_SCRIPT)

    def test_remote_script_marks_target_machine(self) -> None:
        self.assertIn(
            '"/home/$SHOW_USER/.config/showco/machine-role"',
            provision.REMOTE_SCRIPT,
        )
        self.assertIn("printf 'target\\n'", provision.REMOTE_SCRIPT)

    def test_remote_script_configures_network(self) -> None:
        self.assertIn('phase "configuring network"', provision.REMOTE_SCRIPT)
        self.assertIn("configure_network()", provision.REMOTE_SCRIPT)
        self.assertIn(
            "uv run --frozen showco run network-config", provision.REMOTE_SCRIPT
        )
        self.assertIn('write_toml_string host "$SHOWCO_HOST"', provision.REMOTE_SCRIPT)
        self.assertIn("printf '\\n[git.reccy]\\n'", provision.REMOTE_SCRIPT)
        self.assertIn("Skipping network configuration", provision.REMOTE_SCRIPT)

    def test_remote_script_installs_showco_service_through_showco(self) -> None:
        self.assertIn(
            "uv run --frozen showco run install-service", provision.REMOTE_SCRIPT
        )
        self.assertNotIn('tee "$service_file"', provision.REMOTE_SCRIPT)
        self.assertNotIn("ExecStart=$command", provision.REMOTE_SCRIPT)

    def test_remote_script_reboots_after_successful_network_config(self) -> None:
        network = provision.REMOTE_SCRIPT.index('phase "configuring network"')
        reboot = provision.REMOTE_SCRIPT.index('phase "rebooting"')

        self.assertIn(
            "sudo systemd-run --on-active=2s /usr/bin/systemctl reboot",
            provision.REMOTE_SCRIPT,
        )
        self.assertLess(network, reboot)

    def test_remote_script_configures_locale_before_package_updates(self) -> None:
        locale = provision.REMOTE_SCRIPT.index('phase "configuring locale"')
        update = provision.REMOTE_SCRIPT.index("sudo apt-get update")
        upgrade = provision.REMOTE_SCRIPT.index("sudo apt-get upgrade -y")
        install = provision.REMOTE_SCRIPT.index("sudo apt-get install -y")

        self.assertLess(locale, update)
        self.assertLess(update, upgrade)
        self.assertLess(upgrade, install)

    def test_remote_script_exports_locale_before_package_updates(self) -> None:
        export = provision.REMOTE_SCRIPT.index("export LC_CTYPE=en_US.UTF-8")
        update = provision.REMOTE_SCRIPT.index("sudo apt-get update")

        self.assertIn("unset LC_ALL", provision.REMOTE_SCRIPT)
        self.assertLess(export, update)

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

        command = provision.remote_command(config, "/tmp/provision.sh")

        self.assertIn("SHOWCO_HOST=recs-stage.local", command)
        self.assertIn("ROOT=/srv/show-projects", command)
        self.assertIn("RECCY_REFNAME=''", command)
        self.assertIn("EXTERNAL_WIFI_SSID=Venue", command)
        self.assertIn("EXTERNAL_WIFI_PASSWORD='venue password'", command)
        self.assertIn("PRIVATE_WIFI_PASSWORD='private password'", command)
        self.assertIn("X18=false", command)
        self.assertIn("RECS_REFNAME=''", command)

    def test_remote_command_quotes_root_with_spaces(self) -> None:
        parsed = make_config(values(paths={"root": "/srv/show projects"}))

        command = provision.remote_command(parsed, "/tmp/provision.sh")

        self.assertIn("ROOT='/srv/show projects'", command)

    def test_remote_command_passes_git_refname(self) -> None:
        config = make_config(
            values(
                networks=networks(x18=False),
                git={"recs": {"refname": "my-branch"}},
            ),
        )

        command = provision.remote_command(config, "/tmp/provision.sh")

        self.assertIn("RECS_REFNAME=my-branch", command)

    def test_remote_command_passes_enabled_from_twitch_table(self) -> None:
        config = make_config(
            values(
                networks=networks(x18=False),
                twitch={"enabled": True},
            ),
        )

        command = provision.remote_command(config, "/tmp/provision.sh")

        self.assertIn("TWITCHO_ENABLED=true", command)

    def test_wait_for_rebooted_ssh_waits_for_disconnect_then_connect(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        with (
            mock.patch(
                "showco.provision.provision.ssh_is_reachable",
                side_effect=[True, False, False, True],
            ) as reachable,
            mock.patch("showco.provision.provision.time.sleep"),
        ):
            provision.wait_for_rebooted_ssh(config, "tom@recs-stage.local")

        self.assertEqual(reachable.call_count, 4)

    def test_verify_provisioning_checks_projects_and_user_services(self) -> None:
        config = make_config(values())
        with mock.patch(
            "reccy.subprocess.run",
            return_value=subprocess.CompletedProcess(["ssh"], 0, "", ""),
        ) as run:
            results = provision.verify_provisioning(
                config,
                "tom@recs-stage.local",
                network_config.NetworkTopology.MIXED,
            )

        commands = [c.args[0][-1] for c in run.call_args_list]
        self.assertFalse([r for r in results if r.error])
        self.assertIn("git -C /srv/show-projects/reccy status --short", commands)
        self.assertIn("git -C /srv/show-projects/recs status --short", commands)
        self.assertIn("git -C /srv/show-projects/twitcho status --short", commands)
        self.assertIn("git -C /srv/show-projects/showco status --short", commands)
        self.assertIn(
            "uid=$(id -u); XDG_RUNTIME_DIR=/run/user/$uid "
            'cd /srv/show-projects/showco && PATH="$HOME/.local/bin:$PATH" '
            "uv run --frozen showco run service-status recs",
            commands,
        )
        self.assertIn(
            "nmcli -t -f TYPE,STATE,CONNECTION device status "
            "| grep -F -x 'wifi:connected:showco-private'",
            commands,
        )
        self.assertIn(
            "ip -4 -o address show dev br-x18 | grep -F 10.43.0.1/24",
            commands,
        )
        self.assertIn(
            "uid=$(id -u); XDG_RUNTIME_DIR=/run/user/$uid "
            'cd /srv/show-projects/showco && PATH="$HOME/.local/bin:$PATH" '
            "uv run --frozen showco run service-status showco",
            commands,
        )

    def test_missing_x18_usb_device_is_note_not_error(self) -> None:
        config = make_config(values())
        with mock.patch(
            "reccy.subprocess.run",
            return_value=subprocess.CompletedProcess(["ssh"], 1, "", ""),
        ):
            result = provision.verify_x18_usb_device(config, "tom@recs-stage.local")

        self.assertEqual(result.error, "")
        self.assertEqual(result.note, "X18/XR18 not detected")

    def test_x18_usb_device_check_accepts_model_components(self) -> None:
        config = make_config(values())
        with mock.patch(
            "reccy.subprocess.run",
            return_value=subprocess.CompletedProcess(["ssh"], 0, "", ""),
        ) as run:
            provision.verify_x18_usb_device(config, "tom@recs-stage.local")

        self.assertIn(
            "arecord -l | grep -Fi -e X18 -e XR18 >/dev/null",
            run.call_args.args[0],
        )

    def test_remote_script_includes_x18_model_components(self) -> None:
        self.assertIn(
            'IFS=/ read -r -a device_names <<<"$X18_USB_DEVICE_NAME"',
            provision.REMOTE_SCRIPT,
        )
        self.assertIn('args+=(--include "$device_name")', provision.REMOTE_SCRIPT)

    def test_report_verification_results_exits_with_errors(self) -> None:
        with self.assertRaises(SystemExit):
            provision.report_verification_results(
                [provision.VerificationResult(name="showco", error="inactive")]
            )

    def test_report_verification_results_allows_notes(self) -> None:
        provision.report_verification_results(
            [
                provision.VerificationResult(
                    name="X18 USB device",
                    error="",
                    note="X18/XR18 not detected",
                )
            ]
        )

    def test_ensure_github_account_key_adds_new_key(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        public_key = "ssh-ed25519 AAAATEST showco recs-stage.local"
        with (
            mock.patch("showco.provision.provision.shutil.which", return_value="/gh"),
            mock.patch(
                "showco.provision.provision.capture_ssh", return_value=public_key
            ),
            mock.patch(
                "showco.provision.provision.github_key_exists", return_value=False
            ),
            mock.patch("reccy.subprocess.run") as run,
        ):
            provision.ensure_github_account_key(config, "tom@recs-stage.local")

        self.assertEqual(run.call_args.args[0][:3], ["gh", "ssh-key", "add"])
        self.assertEqual(
            run.call_args.args[0][-2:], ["--title", "showco recs-stage.local"]
        )

    def test_ensure_github_account_key_reports_add_failure(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        public_key = "ssh-ed25519 AAAATEST showco recs-stage.local"
        add_error = subprocess.CalledProcessError(
            1,
            ["gh", "ssh-key", "add"],
            stderr="not logged in",
        )
        with (
            mock.patch("showco.provision.provision.shutil.which", return_value="/gh"),
            mock.patch(
                "showco.provision.provision.capture_ssh", return_value=public_key
            ),
            mock.patch(
                "showco.provision.provision.github_key_exists", return_value=False
            ),
            mock.patch("reccy.subprocess.run", side_effect=add_error),
            self.assertRaises(SystemExit) as error,
        ):
            provision.ensure_github_account_key(config, "tom@recs-stage.local")

        self.assertIn(
            "Could not add the Pi SSH key to GitHub from the provisioning machine.",
            str(error.exception),
        )
        self.assertIn("gh said: not logged in", str(error.exception))

    def test_ensure_github_account_key_requires_local_gh(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        with (
            mock.patch("showco.provision.provision.shutil.which", return_value=None),
            self.assertRaises(SystemExit) as error,
        ):
            provision.ensure_github_account_key(config, "tom@recs-stage.local")

        self.assertEqual(
            str(error.exception),
            "ERROR: gh is required on the provisioning machine "
            "to add the Pi SSH key to GitHub.",
        )

    def test_ensure_github_account_key_skips_existing_key(self) -> None:
        config = make_config(values(networks=networks(x18=False)))
        public_key = "ssh-ed25519 AAAATEST showco recs-stage.local"
        with (
            mock.patch("showco.provision.provision.shutil.which", return_value="/gh"),
            mock.patch(
                "showco.provision.provision.capture_ssh", return_value=public_key
            ),
            mock.patch(
                "showco.provision.provision.github_key_exists", return_value=True
            ),
            mock.patch("reccy.subprocess.run") as run,
        ):
            provision.ensure_github_account_key(config, "tom@recs-stage.local")

        run.assert_not_called()

    def test_github_key_exists_ignores_public_key_comment(self) -> None:
        with mock.patch(
            "reccy.subprocess.run",
            return_value=subprocess.CompletedProcess(
                ["gh"],
                0,
                "ssh-ed25519 AAAATEST\n",
                "",
            ),
        ):
            exists = provision.github_key_exists("ssh-ed25519 AAAATEST showco bertrand")

        self.assertTrue(exists)

    def test_github_key_exists_reports_local_gh_failure(self) -> None:
        gh_error = subprocess.CalledProcessError(
            1,
            ["gh", "api", "user/keys"],
            stderr="authentication required",
        )
        with (
            mock.patch("reccy.subprocess.run", side_effect=gh_error),
            self.assertRaises(SystemExit) as error,
        ):
            provision.github_key_exists("ssh-ed25519 AAAATEST showco bertrand")

        self.assertIn(
            "Could not list GitHub SSH keys from the provisioning machine.",
            str(error.exception),
        )
        self.assertIn("Run `gh auth status` on this machine.", str(error.exception))
        self.assertIn("gh said: authentication required", str(error.exception))


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
        "usb": {"x18_device_name": "X18/XR18"},
        "git": {
            "reccy": {"url": "git@github.com:rec/reccy.git"},
            "recs": {"url": "git@github.com:rec/recs.git"},
            "twitcho": {"url": "git@github.com:rec/twitcho.git"},
            "showco": {"url": "git@github.com:rec/showco.git"},
        },
    }
    for k, v in overrides.items():
        if k == "networks":
            result[k] = v
        elif isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = config.merge_values(result[k], v)
        else:
            result[k] = v
    return result


def networks(
    *,
    x18: bool = True,
    internal_wifi: dict[str, object] | None = None,
    external_wifi: dict[str, object] | None = None,
) -> dict[str, object]:
    wired_networks: dict[str, object] = {}
    if x18:
        wired_networks = {
            "x18": {
                "name": "x18",
                "ip_address": "10.43.0.18",
                "subnet": "10.43.0.0/24",
            }
        }
    return {
        "internal": {
            "wired": wired_networks,
            "wifi": {"private": internal_wifi or {"name": "showbox"}},
        },
        "external": {
            "wifi": {"external": external_wifi or {}},
        },
    }


if __name__ == "__main__":
    unittest.main()
