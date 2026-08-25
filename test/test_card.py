from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from showco import card


class PrepareCardTests(unittest.TestCase):
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
            with self.assertRaisesRegex(SystemExit, "user-data file not found"):
                card.prepare_card(Path(directory), "tom")


if __name__ == "__main__":
    unittest.main()
