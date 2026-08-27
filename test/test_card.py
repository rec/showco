from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import tyro

from showco import card
from showco.provision import config


class PrepareCardTests(unittest.TestCase):
    def test_prepare_card_options_detect_card_by_default(self) -> None:
        options = tyro.cli(card.PrepareCardOptions, args=[])

        self.assertIsNone(options.card)

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

    def test_prepare_card_selects_single_small_external_card(self) -> None:
        with mock.patch(
            "showco.card.disk_values",
            return_value=[{"DeviceIdentifier": "disk4", "Size": 128 * 1024**3}],
        ):
            self.assertEqual(card.select_card(None), Path("/dev/disk4"))

    def test_prepare_card_rejects_multiple_small_external_cards(self) -> None:
        with (
            mock.patch(
                "showco.card.disk_values",
                return_value=[
                    {"DeviceIdentifier": "disk4", "Size": 128 * 1024**3},
                    {"DeviceIdentifier": "disk5", "Size": 64 * 1024**3},
                ],
            ),
            self.assertRaisesRegex(SystemExit, "multiple external physical cards"),
        ):
            card.select_card(None)

    def test_prepare_card_displays_selected_card(self) -> None:
        with mock.patch("showco.card.subprocess.run") as run:
            card.show_card(Path("/dev/disk4"))

        run.assert_called_once_with(["diskutil", "list", "/dev/disk4"], check=True)

    def test_prepare_card_ejects_selected_card(self) -> None:
        with mock.patch("showco.card.subprocess.run") as run:
            card.eject_card(Path("/dev/disk4"))

        run.assert_called_once_with(["diskutil", "eject", "/dev/disk4"], check=True)

    def test_prepare_card_finds_user_data_on_selected_card(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "user-data"
            path.write_text("#cloud-config\n")
            with mock.patch(
                "showco.card.disk_values",
                return_value=[
                    {
                        "DeviceIdentifier": "disk4",
                        "Partitions": [{"MountPoint": str(path.parent)}],
                    }
                ],
            ):
                self.assertEqual(card.card_user_data_path(Path("/dev/disk4")), path)

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
