from __future__ import annotations

import subprocess
import unittest
from collections.abc import Sequence
from io import StringIO
from pathlib import Path
from unittest import mock

from showco import python
from showco.provision import config


class PythonTests(unittest.TestCase):
    def test_runs_source_on_target(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            return subprocess.CompletedProcess(command, 0, "attributes\n", "")

        output = StringIO()
        with mock.patch(
            "showco.python.machine_role.require_provisioning_machine"
        ) as require:
            result = python.run_python(
                python.PythonOptions(
                    code="from showco.recs import RecsClient; print(RecsClient())"
                ),
                target_config=target_config(),
                run_command=run_command,
                output=output,
            )

        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue(), "attributes\n")
        require.assert_called_once_with("showco python")
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
                    "cd /srv/show-projects/showco && .venv/bin/python -c "
                    "'from showco.recs import RecsClient; print(RecsClient())'",
                ]
            ],
        )

    def test_remote_command_quotes_source(self) -> None:
        command = python.remote_python_command('print("it\'s working")', Path("/code"))

        self.assertEqual(
            command,
            "cd /code/showco && .venv/bin/python -c 'print(\"it'\"'\"'s working\")'",
        )


def target_config() -> config.Config:
    return config.config_from_values(
        {
            "network": {"host": "bertrand.local", "user": "tom"},
            "paths": {"root": "/srv/show-projects"},
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
