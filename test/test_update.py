from __future__ import annotations

import subprocess
import unittest
from collections.abc import Sequence
from io import StringIO
from pathlib import Path
from unittest import mock

from reccy.models import Platform

from showco import update


class UpdateTests(unittest.TestCase):
    def test_update_host_override_is_used_for_target_ssh(self) -> None:
        with (
            mock.patch(
                "showco.update.machine_role.machine_role",
                return_value="provisioning",
            ),
            mock.patch(
                "showco.update.update_from_provisioning_machine", return_value=0
            ) as update_from_provisioning_machine,
        ):
            result = update.main(["--host", "other.local", "recs"])

        self.assertEqual(result, 0)
        self.assertEqual(
            update_from_provisioning_machine.call_args.args,
            (["recs"],),
        )
        self.assertEqual(
            update_from_provisioning_machine.call_args.kwargs,
            {"host": "other.local"},
        )

    def test_target_update_pulls_selected_repositories_and_restarts_services(
        self,
    ) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-2:] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[-2:] == ["rev-parse", "HEAD"]:
                stdout = "old\n" if commands.count(list(command)) == 1 else "new\n"
                return subprocess.CompletedProcess(command, 0, stdout, "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch(
            "showco.services.service.current_platform", return_value=Platform.linux
        ):
            result = update.update_target(
                ["recs"],
                code_dir=Path("/code"),
                run_command=run_command,
                output=StringIO(),
            )

        self.assertEqual(result, 0)
        self.assertEqual(
            commands,
            [
                ["systemctl", "--user", "stop", "recs.service"],
                ["git", "-C", "/code/recs", "status", "--porcelain"],
                ["git", "-C", "/code/recs", "rev-parse", "HEAD"],
                ["git", "-C", "/code/recs", "pull", "--ff-only"],
                ["git", "-C", "/code/recs", "rev-parse", "HEAD"],
                ["systemctl", "--user", "start", "recs.service"],
            ],
        )

    def test_target_update_rolls_back_failed_pull_and_restarts_service(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-2:] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[-2:] == ["rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(command, 0, "saved\n", "")
            if command[-2:] == ["pull", "--ff-only"]:
                return subprocess.CompletedProcess(command, 1, "", "pull failed\n")
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch(
            "showco.services.service.current_platform", return_value=Platform.linux
        ):
            result = update.update_target(
                ["showco"],
                code_dir=Path("/code"),
                run_command=run_command,
                output=StringIO(),
            )

        self.assertEqual(result, 1)
        self.assertEqual(
            commands,
            [
                ["systemctl", "--user", "stop", "showco.service"],
                ["git", "-C", "/code/showco", "status", "--porcelain"],
                ["git", "-C", "/code/showco", "rev-parse", "HEAD"],
                ["git", "-C", "/code/showco", "pull", "--ff-only"],
                ["git", "-C", "/code/showco", "reset", "--hard", "saved"],
                ["systemctl", "--user", "start", "showco.service"],
            ],
        )

    def test_target_update_updates_showco_first_and_starts_showco_last(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-2:] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[-2:] == ["rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(command, 0, "same\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch(
            "showco.services.service.current_platform", return_value=Platform.linux
        ):
            result = update.update_target(
                ["reccy", "recs", "showco", "twitcho"],
                code_dir=Path("/code"),
                run_command=run_command,
                output=StringIO(),
            )

        self.assertEqual(result, 0)
        self.assertEqual(commands[0], ["systemctl", "--user", "stop", "showco.service"])
        self.assertEqual(
            commands[-1], ["systemctl", "--user", "start", "showco.service"]
        )
        self.assertLess(
            commands.index(["git", "-C", "/code/showco", "pull", "--ff-only"]),
            commands.index(["git", "-C", "/code/reccy", "pull", "--ff-only"]),
        )
        self.assertLess(
            commands.index(["git", "-C", "/code/showco", "pull", "--ff-only"]),
            commands.index(["git", "-C", "/code/recs", "pull", "--ff-only"]),
        )
        self.assertLess(
            commands.index(["git", "-C", "/code/showco", "pull", "--ff-only"]),
            commands.index(["git", "-C", "/code/twitcho", "pull", "--ff-only"]),
        )
        self.assertIn(["systemctl", "--user", "stop", "recs.service"], commands)
        self.assertIn(["systemctl", "--user", "start", "recs.service"], commands)

    def test_target_update_knows_reccy_affects_recs_and_showco(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-2:] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[-2:] == ["rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(command, 0, "same\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch(
            "showco.services.service.current_platform", return_value=Platform.linux
        ):
            result = update.update_target(
                ["reccy"],
                code_dir=Path("/code"),
                run_command=run_command,
                output=StringIO(),
            )

        self.assertEqual(result, 0)
        self.assertIn(["systemctl", "--user", "stop", "recs.service"], commands)
        self.assertIn(["systemctl", "--user", "stop", "showco.service"], commands)
        self.assertIn(["git", "-C", "/code/reccy", "pull", "--ff-only"], commands)
        self.assertIn(["systemctl", "--user", "start", "recs.service"], commands)
        self.assertIn(["systemctl", "--user", "start", "showco.service"], commands)

    def test_provisioning_update_pushes_selected_repos_then_ssh_updates_target(
        self,
    ) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-2:] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[-1:] == ["@{upstream}"]:
                return subprocess.CompletedProcess(command, 0, "origin/main\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        output = StringIO()
        with (
            mock.patch(
                "showco.update.provisioning_config",
                return_value=make_config(),
            ),
            mock.patch(
                "showco.update.run_uncaptured_step",
                return_value=update.StepResult(
                    program="target",
                    step="update",
                    command=["ssh"],
                    returncode=0,
                    output="",
                ),
            ) as remote_update,
        ):
            result = update.update_from_provisioning_machine(
                ["showco", "reccy"],
                code_dir=Path("/code"),
                run_command=run_command,
                output=output,
            )

        self.assertEqual(result, 0)
        self.assertIn(
            ["git", "-C", "/code/showco", "push", "origin", "HEAD:main"],
            commands,
        )
        self.assertIn(
            ["git", "-C", "/code/reccy", "push", "origin", "HEAD:main"],
            commands,
        )
        self.assertEqual(
            remote_update.call_args.args[2][-1],
            'cd "$HOME/code/showco" && uv run showco update showco reccy',
        )
        self.assertIn("ConnectTimeout=2", remote_update.call_args.args[2])
        self.assertIn("showco push: ok", output.getvalue())
        self.assertIn("Updating target tom@bertrand.local", output.getvalue())

    def test_provisioning_update_defaults_to_saved_host(self) -> None:
        with (
            mock.patch(
                "showco.update.provisioning_config",
                return_value=make_config(),
            ),
            mock.patch("showco.update.push_program"),
            mock.patch(
                "showco.update.run_uncaptured_step",
                return_value=update.StepResult(
                    program="target",
                    step="update",
                    command=["ssh"],
                    returncode=0,
                    output="",
                ),
            ) as remote_update,
        ):
            result = update.update_from_provisioning_machine(["recs"])

        self.assertEqual(result, 0)
        self.assertIn("tom@bertrand.local", remote_update.call_args.args[2])

    def test_provisioning_update_uses_host_override(self) -> None:
        with (
            mock.patch(
                "showco.update.provisioning_config",
                return_value=make_config(),
            ),
            mock.patch("showco.update.push_program"),
            mock.patch(
                "showco.update.run_uncaptured_step",
                return_value=update.StepResult(
                    program="target",
                    step="update",
                    command=["ssh"],
                    returncode=0,
                    output="",
                ),
            ) as remote_update,
        ):
            result = update.update_from_provisioning_machine(
                ["recs"], host="other.local"
            )

        self.assertEqual(result, 0)
        self.assertIn("tom@other.local", remote_update.call_args.args[2])

    def test_provisioning_update_rejects_dirty_local_repository(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-2:] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(command, 0, " M file.py\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch(
            "showco.update.provisioning_config",
            return_value=make_config(),
        ):
            result = update.update_from_provisioning_machine(
                ["recs"],
                code_dir=Path("/code"),
                run_command=run_command,
                output=StringIO(),
            )

        self.assertEqual(result, 1)
        self.assertEqual(
            commands,
            [["git", "-C", "/code/recs", "status", "--porcelain"]],
        )

    def test_provisioning_update_ignores_untracked_local_files(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-2:] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    "?? open-loop-forever.md\n?? open-loop.mp4\n",
                    "",
                )
            if command[-1:] == ["@{upstream}"]:
                return subprocess.CompletedProcess(command, 0, "origin/main\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            mock.patch(
                "showco.update.provisioning_config",
                return_value=make_config(),
            ),
            mock.patch(
                "showco.update.run_uncaptured_step",
                return_value=update.StepResult(
                    program="target",
                    step="update",
                    command=["ssh"],
                    returncode=0,
                    output="",
                ),
            ),
        ):
            result = update.update_from_provisioning_machine(
                ["recs"],
                code_dir=Path("/code"),
                run_command=run_command,
                output=StringIO(),
            )

        self.assertEqual(result, 0)
        self.assertIn(
            ["git", "-C", "/code/recs", "push", "origin", "HEAD:main"], commands
        )

    def test_target_update_rejects_dirty_repository_and_restarts_service(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-2:] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(command, 0, " M file.py\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch(
            "showco.services.service.current_platform", return_value=Platform.linux
        ):
            result = update.update_target(
                ["recs"],
                code_dir=Path("/code"),
                run_command=run_command,
                output=StringIO(),
            )

        self.assertEqual(result, 1)
        self.assertEqual(
            commands,
            [
                ["systemctl", "--user", "stop", "recs.service"],
                ["git", "-C", "/code/recs", "status", "--porcelain"],
                ["systemctl", "--user", "start", "recs.service"],
            ],
        )

    def test_target_update_ignores_untracked_repository_files(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-2:] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(command, 0, "?? open-loop.mp4\n", "")
            if command[-2:] == ["rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(command, 0, "same\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch(
            "showco.services.service.current_platform", return_value=Platform.linux
        ):
            result = update.update_target(
                ["recs"],
                code_dir=Path("/code"),
                run_command=run_command,
                output=StringIO(),
            )

        self.assertEqual(result, 0)
        self.assertIn(["git", "-C", "/code/recs", "pull", "--ff-only"], commands)

    def test_selected_repositories_defaults_to_all(self) -> None:
        self.assertEqual(update.selected_repositories([]), update.REPOSITORY_NAMES)

    def test_selected_repositories_rejects_unknown_names(self) -> None:
        with self.assertRaisesRegex(SystemExit, "unknown update target"):
            update.selected_repositories(["bogus"])


def make_config() -> object:
    class Network:
        user = "tom"
        host = "bertrand.local"
        ssh_port = 22

    class Config:
        network = Network()

    return Config()


if __name__ == "__main__":
    unittest.main()
