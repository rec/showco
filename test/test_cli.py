from __future__ import annotations

import unittest
from unittest.mock import patch

import tyro

from showco import cli


class CliTests(unittest.TestCase):
    def test_web_ui_options_accept_existing_flags(self) -> None:
        options = tyro.cli(
            cli.WebUiOptions,
            args=["--host", "0.0.0.0", "--port", "17352", "--rehearsal"],
        )

        self.assertEqual(options.host, "0.0.0.0")
        self.assertEqual(options.port, 17_352)
        self.assertTrue(options.rehearsal_mode)

    def test_dispatches_go_subcommand(self) -> None:
        with patch.object(cli.go, "main", return_value=7) as go:
            self.assertEqual(cli.main(["go", "recs"]), 7)

        go.assert_called_once_with(["recs"])

    def test_dispatches_go_without_a_subcommand(self) -> None:
        with patch.object(cli.go, "main", return_value=7) as go:
            self.assertEqual(cli.main([]), 7)

        go.assert_called_once_with([])

    def test_dispatches_push_flag_without_go_subcommand(self) -> None:
        with patch.object(cli.go, "main", return_value=7) as go:
            self.assertEqual(cli.main(["--push", "recs"]), 7)

        go.assert_called_once_with(["--push", "recs"])

    def test_dispatches_sync_flag_without_go_subcommand(self) -> None:
        with patch.object(cli.go, "main", return_value=7) as go:
            self.assertEqual(cli.main(["--sync", "reccy"]), 7)

        go.assert_called_once_with(["--sync", "reccy"])

    def test_dispatches_go_options_before_local_mode_flag(self) -> None:
        arguments = ["--autosquash", "0", "--push", "recs"]
        with patch.object(cli.go, "main", return_value=7) as go:
            self.assertEqual(cli.main(arguments), 7)

        go.assert_called_once_with(arguments)

    def test_dispatches_prepare_card_subcommand(self) -> None:
        with patch.object(cli.card, "main", return_value=7) as prepare_card:
            self.assertEqual(cli.main(["prepare-card", "--boot", "/Volumes/bootfs"]), 7)

        prepare_card.assert_called_once_with(["--boot", "/Volumes/bootfs"])

    def test_dispatches_twitcho_subcommand(self) -> None:
        with (
            patch.object(cli.machine_role, "require_target_machine"),
            patch.object(cli.auth, "main", return_value=7) as twitcho,
        ):
            self.assertEqual(cli.main(["twitcho", "--help"]), 7)

        twitcho.assert_called_once_with(["--help"])

    def test_dispatches_logs_subcommand(self) -> None:
        with patch.object(cli.logs, "main", return_value=7) as logs:
            self.assertEqual(cli.main(["logs", "--lines=50", "recs"]), 7)

        logs.assert_called_once_with(["--lines=50", "recs"])

    def test_dispatches_python_subcommand(self) -> None:
        with patch.object(cli.python, "main", return_value=7) as python:
            self.assertEqual(cli.main(["python", "print(1)"]), 7)

        python.assert_called_once_with(["print(1)"])

    def test_rejects_unknown_subcommand(self) -> None:
        self.assertEqual(cli.main(["unknown"]), 2)

    def test_rejects_removed_subcommands(self) -> None:
        self.assertEqual(cli.main(["update"]), 2)
        self.assertEqual(cli.main(["provision"]), 2)


if __name__ == "__main__":
    unittest.main()
