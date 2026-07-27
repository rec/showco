from __future__ import annotations

import argparse
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

    def test_configured_password_requires_sshpass(self) -> None:
        config = provision.config_from_args(
            args(), values(is_x18_wired=False, showco_pi_password="password")
        )
        with (
            mock.patch("showco.provision.provision.shutil.which", return_value=None),
            mock.patch("showco.provision.provision.subprocess.run") as run,
            self.assertRaises(SystemExit) as error,
        ):
            provision.run(config, ["ssh"], ["sshpass", "-e", "ssh"])

        self.assertEqual(
            str(error.exception),
            "ERROR: showco_pi_password requires sshpass to be installed.",
        )
        run.assert_not_called()

    def test_key_based_ssh_does_not_require_sshpass(self) -> None:
        config = provision.config_from_args(args(), values(is_x18_wired=False))
        with (
            mock.patch("showco.provision.provision.shutil.which", return_value=None),
            mock.patch("showco.provision.provision.subprocess.run") as run,
        ):
            provision.run(config, ["ssh"], ["sshpass", "-e", "ssh"])

        run.assert_called_once_with(
            ["ssh"],
            capture_output=False,
            check=True,
            env=None,
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

    def test_remote_script_configures_external_storage_mounts(self) -> None:
        self.assertIn("exfatprogs", provision.REMOTE_SCRIPT)
        self.assertIn('phase "configuring storage mounts"', provision.REMOTE_SCRIPT)
        self.assertIn("lsblk -f", provision.REMOTE_SCRIPT)
        self.assertIn("UUID=%s %s %s %s 0 2", provision.REMOTE_SCRIPT)
        self.assertIn('target="/mnt/$name"', provision.REMOTE_SCRIPT)

    def test_remote_script_writes_provisioning_report(self) -> None:
        self.assertIn('phase "writing provisioning report"', provision.REMOTE_SCRIPT)
        self.assertIn("Disks discovered:", provision.REMOTE_SCRIPT)
        self.assertIn("Wi-Fi interfaces discovered:", provision.REMOTE_SCRIPT)
        self.assertIn("nmcli device status", provision.REMOTE_SCRIPT)
        self.assertIn("iw dev", provision.REMOTE_SCRIPT)
        self.assertIn("PROVISIONING-REPORT.txt", provision.REMOTE_SCRIPT)

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
            mock.patch("showco.provision.provision.subprocess.run") as run,
        ):
            provision.ensure_github_account_key(config, "tom@recs-stage.local")

        self.assertEqual(run.call_args.args[0][:3], ["gh", "ssh-key", "add"])
        self.assertEqual(
            run.call_args.args[0][-2:], ["--title", "showco recs-stage.local"]
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
            mock.patch("showco.provision.provision.subprocess.run") as run,
        ):
            provision.ensure_github_account_key(config, "tom@recs-stage.local")

        run.assert_not_called()

    def test_github_key_exists_ignores_public_key_comment(self) -> None:
        with mock.patch(
            "showco.provision.provision.subprocess.run",
            return_value=subprocess.CompletedProcess(
                ["gh"],
                0,
                "ssh-ed25519 AAAATEST\n",
                "",
            ),
        ):
            exists = provision.github_key_exists("ssh-ed25519 AAAATEST showco bertrand")

        self.assertTrue(exists)


def args() -> argparse.Namespace:
    return argparse.Namespace(
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
