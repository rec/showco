from __future__ import annotations

import subprocess
import unittest
from collections.abc import Sequence
from io import StringIO
from pathlib import Path

from showco.git_pull import update_programs


class GitPullTests(unittest.TestCase):
    def test_restarts_recs_even_when_pull_fails(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[:3] == ["git", "-C", "/code/recs"]:
                return subprocess.CompletedProcess(command, 1, "", "network down\n")
            return subprocess.CompletedProcess(command, 0, "", "")

        result = update_programs(
            code_dir=Path("/code"), run_command=run_command, output=StringIO()
        )

        self.assertEqual(result, 1)
        self.assertIn(["git", "-C", "/code/recs", "pull"], commands)
        self.assertIn(["systemctl", "--user", "restart", "recs"], commands)
        self.assertIn(["systemctl", "--user", "restart", "showco"], commands)

    def test_pulls_twitcho_without_managing_a_twitcho_service(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            return subprocess.CompletedProcess(command, 0, "", "")

        result = update_programs(
            code_dir=Path("/code"), run_command=run_command, output=StringIO()
        )

        self.assertEqual(result, 0)
        self.assertEqual(
            commands,
            [
                ["systemctl", "--user", "stop", "recs"],
                ["systemctl", "--user", "stop", "showco"],
                ["git", "-C", "/code/recs", "pull"],
                ["git", "-C", "/code/twitcho", "pull"],
                ["git", "-C", "/code/showco", "pull"],
                ["systemctl", "--user", "restart", "recs"],
                ["systemctl", "--user", "restart", "showco"],
            ],
        )


if __name__ == "__main__":
    unittest.main()
