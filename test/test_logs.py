from __future__ import annotations

import subprocess
import unittest
from collections.abc import Sequence
from io import StringIO

from showco import logs
from showco.provision import config


class LogsTests(unittest.TestCase):
    def test_fetches_selected_remote_service_logs(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            return subprocess.CompletedProcess(command, 0, "logs\n", "")

        output = StringIO()

        result = logs.fetch_logs(
            logs.LogsOptions(services=["recs", "twitcho"], lines=25),
            target_config=target_config(),
            run_command=run_command,
            output=output,
        )

        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue(), "logs\n")
        self.assertEqual(
            commands,
            [
                [
                    "ssh",
                    "-o",
                    "ConnectTimeout=2",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "StrictHostKeyChecking=accept-new",
                    "-p",
                    "22",
                    "tom@bertrand.local",
                    'tail --lines=25 "$HOME/.local/state/recs/recs.log" '
                    '"$HOME/.local/state/twitcho/twitcho.log"',
                ]
            ],
        )

    def test_defaults_to_all_service_logs(self) -> None:
        self.assertEqual(
            logs.selected_services([]), ["showco", "recs", "twitcho", "lyte"]
        )

    def test_rejects_unknown_services(self) -> None:
        with self.assertRaisesRegex(SystemExit, "unknown log target"):
            logs.selected_services(["recs", "unknown"])

    def test_remote_logs_command_quotes_units(self) -> None:
        self.assertEqual(
            logs.remote_logs_command(["recs", "lyte"], 50),
            'tail --lines=50 "$HOME/.local/state/recs/recs.log" '
            '"$HOME/.local/state/lyte/lyte.log"',
        )


def target_config() -> config.Config:
    return config.config_from_values(
        {
            "network": {"host": "bertrand.local", "user": "tom"},
            "paths": {},
            "networks": {},
            "usb": {},
            "twitch": {},
            "lyte": {},
            "git": {
                "reccy": {"url": "git@github.com:rec/reccy"},
                "recs": {"url": "git@github.com:rec/recs"},
                "twitcho": {"url": "git@github.com:rec/twitcho"},
                "showco": {"url": "git@github.com:rec/showco"},
                "lyte": {"url": "git@github.com:rec/lyte"},
            },
        }
    )


if __name__ == "__main__":
    unittest.main()
