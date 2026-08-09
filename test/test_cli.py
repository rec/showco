from __future__ import annotations

import unittest
from unittest.mock import patch

from showco import cli


class CliTests(unittest.TestCase):
    def test_dispatches_provision_subcommand(self) -> None:
        with patch.object(cli.provision, "main", return_value=7) as provision:
            self.assertEqual(cli.main(["provision", "--help"]), 7)

        provision.assert_called_once_with(["--help"])

    def test_dispatches_twitcho_subcommand(self) -> None:
        with (
            patch.object(cli.machine_role, "require_target_machine"),
            patch.object(cli.auth, "main", return_value=7) as twitcho,
        ):
            self.assertEqual(cli.main(["twitcho", "--help"]), 7)

        twitcho.assert_called_once_with(["--help"])

    def test_dispatches_update_subcommand(self) -> None:
        with patch.object(cli.update, "main", return_value=7) as update:
            self.assertEqual(cli.main(["update", "recs"]), 7)

        update.assert_called_once_with(["recs"])

    def test_rejects_unknown_subcommand(self) -> None:
        self.assertEqual(cli.main(["unknown"]), 2)


if __name__ == "__main__":
    unittest.main()
