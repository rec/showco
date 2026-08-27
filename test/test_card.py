from __future__ import annotations

import plistlib
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import tyro

from showco import card
from showco.provision import config


class PrepareCardTests(unittest.TestCase):
    def test_prepare_card_options_default_to_imager_boot_volume(self) -> None:
        options = tyro.cli(card.PrepareCardOptions, args=[])

        self.assertEqual(options.boot, Path("/Volumes/bootfs"))

    def test_prepare_card_adds_sudo_commands_to_runcmd(self) -> None:
        with TemporaryDirectory() as directory:
            boot = Path(directory)
            path = boot / "user-data"
            path.write_text(
                "#cloud-config\n"
                "runcmd:\n"
                "  - [ systemctl, enable, ssh ]\n"
                "final_message: done\n"
            )

            self.assertTrue(card.prepare_card(boot, "tom"))

            self.assertEqual(
                path.read_text(),
                "#cloud-config\n"
                "runcmd:\n"
                "  - [ systemctl, enable, ssh ]\n"
                "  - [ sh, -c, \"echo 'tom ALL=(ALL) NOPASSWD: ALL' > "
                '/etc/sudoers.d/010_tom-nopasswd" ]\n'
                '  - [ chmod, "0440", "/etc/sudoers.d/010_tom-nopasswd" ]\n'
                "final_message: done\n",
            )

    def test_prepare_card_adds_runcmd_when_missing(self) -> None:
        with TemporaryDirectory() as directory:
            boot = Path(directory)
            path = boot / "user-data"
            path.write_text("#cloud-config\nhostname: bertrand\n")

            self.assertTrue(card.prepare_card(boot, "tom"))

            self.assertIn("runcmd:\n", path.read_text())

    def test_prepare_card_is_idempotent(self) -> None:
        with TemporaryDirectory() as directory:
            boot = Path(directory)
            (boot / "user-data").write_text("#cloud-config\n")

            card.prepare_card(boot, "tom")

            self.assertFalse(card.prepare_card(boot, "tom"))

    def test_prepare_card_rejects_missing_user_data(self) -> None:
        with TemporaryDirectory() as directory:
            with (
                mock.patch("showco.card.mount_external_disks") as mount,
                self.assertRaisesRegex(SystemExit, "user-data file not found"),
            ):
                card.prepare_card(Path(directory), "tom")

        mount.assert_called_once_with()

    def test_prepare_card_mounts_external_physical_disks(self) -> None:
        disk_list = plistlib.dumps({"WholeDisks": ["disk4", "disk5"]})
        with mock.patch(
            "showco.card.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, disk_list),
        ) as run:
            card.mount_external_disks()

        self.assertEqual(
            run.call_args_list,
            [
                mock.call(
                    ["diskutil", "list", "-plist", "external", "physical"],
                    capture_output=True,
                    check=True,
                ),
                mock.call(["diskutil", "mountDisk", "/dev/disk4"], check=False),
                mock.call(["diskutil", "mountDisk", "/dev/disk5"], check=False),
            ],
        )

    def test_prepare_card_finds_mounted_boot_volume(self) -> None:
        with TemporaryDirectory() as directory:
            volumes = Path(directory)
            boot = volumes / "bootfs 1"
            boot.mkdir()
            path = boot / "user-data"
            path.write_text("#cloud-config\n")

            with mock.patch("showco.card.mount_external_disks"):
                self.assertEqual(
                    card.user_data_path(volumes / "bootfs", volumes),
                    path,
                )

    def test_write_cloud_init_configures_key_only_access(self) -> None:
        with TemporaryDirectory() as directory:
            boot = Path(directory)

            card.write_cloud_init(
                boot,
                "bertrand",
                "tom",
                "ssh-ed25519 public-key tom@developer",
                config.Network(name="Livebox", password="wireless secret"),
            )

            user_data = (boot / "user-data").read_text()
            self.assertIn("lock_passwd: true", user_data)
            self.assertIn("NOPASSWD:ALL", user_data)
            self.assertIn('"ssh-ed25519 public-key tom@developer"', user_data)
            self.assertIn('"Livebox"', (boot / "network-config").read_text())


if __name__ == "__main__":
    unittest.main()
