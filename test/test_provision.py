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

        run.assert_called_once_with(["ssh"], check=True, env=None)


def args() -> argparse.Namespace:
    return argparse.Namespace(
        host=None,
        user=None,
        port=None,
        hostname=None,
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
