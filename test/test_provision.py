from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest import mock

from showco.provision import provision


class ProvisionTests(unittest.TestCase):
    def test_wired_x18_uses_configured_x18_host(self) -> None:
        config = provision.config_from_args(
            args(),
            values(
                is_x18_wired=True,
                showco_x18_wired_ethernet_ip_address="10.43.0.18",
            ),
        )

        self.assertEqual(config.showco_x18_host, "10.43.0.18")

    def test_unwired_x18_omits_x18_host(self) -> None:
        config = provision.config_from_args(
            args(),
            values(is_x18_wired=False),
        )

        self.assertEqual(config.showco_x18_host, "")

    def test_remote_script_is_removed_after_remote_failure(self) -> None:
        config = provision.config_from_args(args(), values(is_x18_wired=False))
        original_error = subprocess.CalledProcessError(1, ["ssh", "provision"])
        cleanup_error = subprocess.CalledProcessError(1, ["ssh", "cleanup"])
        with (
            mock.patch(
                "showco.provision.provision.run_ssh",
                side_effect=[None, original_error, cleanup_error],
            ) as run_ssh,
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
        with mock.patch("showco.run") as run:
            provision.run(["ssh"])

        run.assert_called_once_with(
            ["ssh"],
            capture_output=False,
            check=True,
            text=True,
        )

    def test_provision_adds_github_key_before_uploading_script(self) -> None:
        calls: list[str] = []
        config = provision.config_from_args(args(), values(is_x18_wired=False))

        def ensure_github_account_key(
            config: provision.Config, ssh_target: str
        ) -> None:
            calls.append("github")

        def run_scp(config: provision.Config, source: Path, target: str) -> None:
            calls.append("scp")

        with (
            mock.patch("showco.provision.provision.run_ssh"),
            mock.patch(
                "showco.provision.provision.ensure_github_account_key",
                side_effect=ensure_github_account_key,
            ),
            mock.patch("showco.provision.provision.run_scp", side_effect=run_scp),
        ):
            provision.provision_remote(
                config,
                "tom@recs-stage.local",
                Path("/tmp/local.sh"),
                "/tmp/remote.sh",
            )

        self.assertEqual(calls, ["github", "scp"])

    def test_github_key_title_uses_host(self) -> None:
        config = provision.config_from_args(args(), values(is_x18_wired=False))

        self.assertEqual(provision.github_key_title(config), "showco recs-stage.local")

    def test_remote_github_key_command_writes_only_public_key_to_stdout(self) -> None:
        config = provision.config_from_args(args(), values(is_x18_wired=False))
        command = provision.remote_github_key_command(config)

        self.assertIn('} >&2\ncat "$HOME/.ssh/id_ed25519.pub"', command)

    def test_remote_github_key_command_regenerates_missing_public_key(self) -> None:
        config = provision.config_from_args(args(), values(is_x18_wired=False))
        command = provision.remote_github_key_command(config)

        self.assertIn('ssh-keygen -y -f "$HOME/.ssh/id_ed25519"', command)

    def test_remote_github_key_command_does_not_run_gh_on_pi(self) -> None:
        config = provision.config_from_args(args(), values(is_x18_wired=False))
        command = provision.remote_github_key_command(config)

        self.assertNotIn("gh ", command)
        self.assertNotIn("gh\n", command)

    def test_remote_github_key_command_is_loaded_from_template_file(self) -> None:
        config = provision.config_from_args(args(), values(is_x18_wired=False))
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

    def test_remote_script_reinstalls_broken_uv(self) -> None:
        self.assertIn("uv --version", provision.REMOTE_SCRIPT)

    def test_remote_script_writes_provisioning_report(self) -> None:
        self.assertIn('phase "writing provisioning report"', provision.REMOTE_SCRIPT)
        self.assertIn("Disks discovered:", provision.REMOTE_SCRIPT)
        self.assertIn("Wi-Fi interfaces discovered:", provision.REMOTE_SCRIPT)
        self.assertIn("nmcli device status", provision.REMOTE_SCRIPT)
        self.assertIn("iw dev", provision.REMOTE_SCRIPT)
        self.assertIn("PROVISIONING-REPORT.txt", provision.REMOTE_SCRIPT)

    def test_remote_script_configures_network(self) -> None:
        self.assertIn('phase "configuring network"', provision.REMOTE_SCRIPT)
        self.assertIn("configure_network()", provision.REMOTE_SCRIPT)
        self.assertIn("uv run showco run network-config", provision.REMOTE_SCRIPT)
        self.assertIn("Skipping network configuration", provision.REMOTE_SCRIPT)

    def test_remote_script_configures_locale_before_package_updates(self) -> None:
        locale = provision.REMOTE_SCRIPT.index('phase "configuring locale"')
        update = provision.REMOTE_SCRIPT.index("sudo apt-get update")
        upgrade = provision.REMOTE_SCRIPT.index("sudo apt-get upgrade -y")
        install = provision.REMOTE_SCRIPT.index("sudo apt-get install -y")

        self.assertLess(locale, update)
        self.assertLess(update, upgrade)
        self.assertLess(upgrade, install)

    def test_remote_command_passes_network_config(self) -> None:
        config = provision.config_from_args(
            args(),
            values(
                is_x18_wired=False,
                external_wifi_ssid="Venue",
                external_wifi_password="venue password",
                private_wifi_password="private password",
            ),
        )

        command = provision.remote_command(config, "/tmp/provision.sh")

        self.assertIn("EXTERNAL_WIFI_SSID=Venue", command)
        self.assertIn("EXTERNAL_WIFI_PASSWORD='venue password'", command)
        self.assertIn("PRIVATE_WIFI_PASSWORD='private password'", command)
        self.assertIn("IS_X18_WIRED=false", command)

    def test_ensure_github_account_key_adds_new_key(self) -> None:
        config = provision.config_from_args(args(), values(is_x18_wired=False))
        public_key = "ssh-ed25519 AAAATEST showco recs-stage.local"
        with (
            mock.patch("showco.provision.provision.shutil.which", return_value="/gh"),
            mock.patch(
                "showco.provision.provision.capture_ssh", return_value=public_key
            ),
            mock.patch(
                "showco.provision.provision.github_key_exists", return_value=False
            ),
            mock.patch("showco.run") as run,
        ):
            provision.ensure_github_account_key(config, "tom@recs-stage.local")

        self.assertEqual(run.call_args.args[0][:3], ["gh", "ssh-key", "add"])
        self.assertEqual(
            run.call_args.args[0][-2:], ["--title", "showco recs-stage.local"]
        )

    def test_ensure_github_account_key_reports_add_failure(self) -> None:
        config = provision.config_from_args(args(), values(is_x18_wired=False))
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
            mock.patch("showco.run", side_effect=add_error),
            self.assertRaises(SystemExit) as error,
        ):
            provision.ensure_github_account_key(config, "tom@recs-stage.local")

        self.assertIn(
            "Could not add the Pi SSH key to GitHub from the provisioning machine.",
            str(error.exception),
        )
        self.assertIn("gh said: not logged in", str(error.exception))

    def test_ensure_github_account_key_requires_local_gh(self) -> None:
        config = provision.config_from_args(args(), values(is_x18_wired=False))
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
        config = provision.config_from_args(args(), values(is_x18_wired=False))
        public_key = "ssh-ed25519 AAAATEST showco recs-stage.local"
        with (
            mock.patch("showco.provision.provision.shutil.which", return_value="/gh"),
            mock.patch(
                "showco.provision.provision.capture_ssh", return_value=public_key
            ),
            mock.patch(
                "showco.provision.provision.github_key_exists", return_value=True
            ),
            mock.patch("showco.run") as run,
        ):
            provision.ensure_github_account_key(config, "tom@recs-stage.local")

        run.assert_not_called()

    def test_github_key_exists_ignores_public_key_comment(self) -> None:
        with mock.patch(
            "showco.run",
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
            mock.patch("showco.run", side_effect=gh_error),
            self.assertRaises(SystemExit) as error,
        ):
            provision.github_key_exists("ssh-ed25519 AAAATEST showco bertrand")

        self.assertIn(
            "Could not list GitHub SSH keys from the provisioning machine.",
            str(error.exception),
        )
        self.assertIn("Run `gh auth status` on this machine.", str(error.exception))
        self.assertIn("gh said: authentication required", str(error.exception))


def args() -> provision.ProvisionOptions:
    return provision.ProvisionOptions(
        config=Path("/config.toml"),
        secrets=Path("/secrets.toml"),
        host=None,
        user=None,
        port=None,
        recs_repo=None,
        twitcho_repo=None,
        showco_repo=None,
    )


def values(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "showco_pi_host": "recs-stage.local",
        "showco_pi_user": "tom",
        "showco_pi_ssh_port": "22",
        "recs_repo": "git@github.com:rec/recs.git",
        "twitcho_repo": "git@github.com:rec/twitcho.git",
        "showco_repo": "git@github.com:rec/showco.git",
    }
    result.update(overrides)
    return result


if __name__ == "__main__":
    unittest.main()
