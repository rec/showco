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
            mock.patch("showco.update.tqdm.write") as write,
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
        write.assert_called_once_with("Success!")

    def test_update_root_override_is_used_for_target_ssh(self) -> None:
        with (
            mock.patch(
                "showco.update.machine_role.machine_role",
                return_value="provisioning",
            ),
            mock.patch(
                "showco.update.update_from_provisioning_machine", return_value=0
            ) as update_from_provisioning_machine,
            mock.patch("showco.update.tqdm.write"),
        ):
            result = update.main(["--root", "/srv/show-projects", "recs"])

        self.assertEqual(result, 0)
        self.assertEqual(
            update_from_provisioning_machine.call_args.kwargs,
            {"host": None, "root": Path("/srv/show-projects")},
        )

    def test_target_machine_override_runs_target_update(self) -> None:
        with (
            mock.patch("showco.update.machine_role.machine_role", return_value=""),
            mock.patch("showco.update.update_target", return_value=0) as update_target,
            mock.patch("showco.update.tqdm.write") as write,
        ):
            result = update.main(["--target-machine", "recs"])

        self.assertEqual(result, 0)
        self.assertEqual(update_target.call_args.args, (["recs"],))
        write.assert_called_once_with("Success!")

    def test_update_prints_failure_summary(self) -> None:
        with (
            mock.patch("showco.update.machine_role.machine_role", return_value=""),
            mock.patch("showco.update.update_target", return_value=1),
            mock.patch("showco.update.tqdm.write") as write,
        ):
            result = update.main(["--target-machine", "recs"])

        self.assertEqual(result, 1)
        write.assert_called_once_with("ERROR: update failed")

    def test_remote_update_command_quotes_root_with_spaces(self) -> None:
        command = update.remote_update_command(["recs"], Path("/srv/show projects"))

        self.assertEqual(
            command,
            "cd '/srv/show projects/showco' && "
            'PATH="$HOME/.local/bin:$PATH" '
            "uv run showco update --target-machine --root '/srv/show projects' recs",
        )

    def test_legacy_remote_update_command_has_no_update_arguments(self) -> None:
        command = update.legacy_remote_update_command(Path("/srv/show projects"))

        self.assertEqual(
            command,
            "cd '/srv/show projects/showco' && "
            'PATH="$HOME/.local/bin:$PATH" '
            "uv run showco update",
        )

    def test_rejected_update_arguments_requires_tyro_option_error(self) -> None:
        self.assertTrue(
            update.rejected_update_arguments(
                update.StepResult(
                    program="target",
                    step="update",
                    command=["ssh"],
                    returncode=2,
                    output="Unrecognized options: --root",
                )
            )
        )
        self.assertFalse(
            update.rejected_update_arguments(
                update.StepResult(
                    program="target",
                    step="update",
                    command=["ssh"],
                    returncode=1,
                    output="git pull failed",
                )
            )
        )

    def test_target_update_pulls_selected_repositories_and_restarts_services(
        self,
    ) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-2:] == ["branch", "--show-current"]:
                return subprocess.CompletedProcess(command, 0, "main\n", "")
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
                root=Path("/code"),
                run_command=run_command,
                output=StringIO(),
            )

        self.assertEqual(result, 0)
        self.assertEqual(
            commands,
            [
                ["git", "-C", "/code/recs", "branch", "--show-current"],
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
            if command[-2:] == ["branch", "--show-current"]:
                return subprocess.CompletedProcess(command, 0, "main\n", "")
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
                root=Path("/code"),
                run_command=run_command,
                output=StringIO(),
            )

        self.assertEqual(result, 1)
        self.assertEqual(
            commands,
            [
                ["git", "-C", "/code/showco", "branch", "--show-current"],
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
            if command[-2:] == ["branch", "--show-current"]:
                return subprocess.CompletedProcess(command, 0, "main\n", "")
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
                root=Path("/code"),
                run_command=run_command,
                output=StringIO(),
            )

        self.assertEqual(result, 0)
        self.assertEqual(
            commands[:4],
            [
                ["git", "-C", "/code/reccy", "branch", "--show-current"],
                ["git", "-C", "/code/recs", "branch", "--show-current"],
                ["git", "-C", "/code/showco", "branch", "--show-current"],
                ["git", "-C", "/code/twitcho", "branch", "--show-current"],
            ],
        )
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
            if command[-2:] == ["branch", "--show-current"]:
                return subprocess.CompletedProcess(command, 0, "main\n", "")
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
                root=Path("/code"),
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
            if command[-2:] == ["branch", "--show-current"]:
                return subprocess.CompletedProcess(command, 0, "main\n", "")
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
                "showco.update.run_remote_step",
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
                root=Path("/code"),
                local_root=Path("/code"),
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
            "cd /code/showco && "
            'PATH="$HOME/.local/bin:$PATH" '
            "uv run showco update --target-machine --root /code showco reccy",
        )
        self.assertIn("ConnectTimeout=2", remote_update.call_args.args[2])
        self.assertEqual(output.getvalue(), "")

    def test_provisioning_update_rejects_non_main_branches_before_pushing(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            branch = "feature" if command[2] == "/code/recs" else "main"
            return subprocess.CompletedProcess(command, 0, f"{branch}\n", "")

        output = StringIO()
        with (
            mock.patch(
                "showco.update.provisioning_config",
                return_value=make_config(),
            ),
            mock.patch("showco.update.run_remote_step") as remote_update,
        ):
            result = update.update_from_provisioning_machine(
                ["recs", "showco"],
                root=Path("/code"),
                local_root=Path("/code"),
                run_command=run_command,
                output=output,
            )

        self.assertEqual(result, 1)
        self.assertEqual(
            commands,
            [
                ["git", "-C", "/code/recs", "branch", "--show-current"],
                ["git", "-C", "/code/showco", "branch", "--show-current"],
            ],
        )
        self.assertIn("recs main branch: failed", output.getvalue())
        self.assertIn("repository is on feature, expected main", output.getvalue())
        remote_update.assert_not_called()

    def test_provisioning_update_retries_old_target_without_arguments(self) -> None:
        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            if command[-2:] == ["branch", "--show-current"]:
                return subprocess.CompletedProcess(command, 0, "main\n", "")
            if command[-2:] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[-1:] == ["@{upstream}"]:
                return subprocess.CompletedProcess(command, 0, "origin/main\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        rejected = update.StepResult(
            program="target",
            step="update",
            command=["ssh"],
            returncode=2,
            output="Unrecognized options: --target-machine",
        )
        succeeded = update.StepResult(
            program="target",
            step="legacy update",
            command=["ssh"],
            returncode=0,
            output="",
        )
        with (
            mock.patch(
                "showco.update.provisioning_config",
                return_value=make_config(),
            ),
            mock.patch(
                "showco.update.run_remote_step",
                side_effect=[rejected, succeeded],
            ) as remote_update,
        ):
            result = update.update_from_provisioning_machine(
                ["showco"],
                root=Path("/code"),
                local_root=Path("/code"),
                run_command=run_command,
                output=StringIO(),
            )

        self.assertEqual(result, 0)
        self.assertEqual(remote_update.call_count, 2)
        self.assertEqual(
            remote_update.call_args.args[2][-1],
            'cd /code/showco && PATH="$HOME/.local/bin:$PATH" uv run showco update',
        )

    def test_target_update_rejects_non_main_branches_before_stopping_services(
        self,
    ) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            return subprocess.CompletedProcess(command, 0, "feature\n", "")

        output = StringIO()
        result = update.update_target(
            ["recs"],
            root=Path("/code"),
            run_command=run_command,
            output=output,
        )

        self.assertEqual(result, 1)
        self.assertEqual(
            commands,
            [["git", "-C", "/code/recs", "branch", "--show-current"]],
        )
        self.assertIn("repository is on feature, expected main", output.getvalue())

    def test_push_program_force_pushes_with_current_upstream_commit(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-2:] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[-1:] == ["@{upstream}"]:
                return subprocess.CompletedProcess(command, 0, "origin/main\n", "")
            if command[-3:] == ["push", "origin", "HEAD:main"]:
                return subprocess.CompletedProcess(command, 1, "", "rejected\n")
            if command[-3:] == ["fetch", "origin", "main"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[-1:] == ["origin/main"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    "1234567890abcdef\nRemote commit subject\n",
                    "",
                )
            return subprocess.CompletedProcess(command, 0, "forced\n", "")

        output = StringIO()
        result = update.push_program(
            update.Program(name="recs", directory=Path("/code/recs"), service_names=[]),
            run_command,
            output,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.step, "push --force-with-lease")
        self.assertIn("recs push: failed", output.getvalue())
        self.assertIn("rejected", output.getvalue())
        self.assertIn("recs current upstream commit:", output.getvalue())
        self.assertIn("1234567890abcdef\nRemote commit subject", output.getvalue())
        self.assertEqual(
            commands[-1],
            [
                "git",
                "-C",
                "/code/recs",
                "push",
                "--force-with-lease",
                "origin",
                "HEAD:main",
            ],
        )

    def test_remote_step_reports_remote_output(self) -> None:
        command = ["ssh", "tom@bertrand.local", "showco update"]
        with mock.patch(
            "showco.update.subprocess.run",
            return_value=subprocess.CompletedProcess(
                command,
                1,
                "target output\n",
                "target error\n",
            ),
        ):
            result = update.run_remote_step("target", "update", command)

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.output, "target output\ntarget error\n")

    def test_provisioning_update_defaults_to_saved_host(self) -> None:
        with (
            mock.patch(
                "showco.update.provisioning_config",
                return_value=make_config(),
            ),
            mock.patch("showco.update.check_main_branches", return_value=True),
            mock.patch("showco.update.push_program"),
            mock.patch(
                "showco.update.run_remote_step",
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
            mock.patch("showco.update.check_main_branches", return_value=True),
            mock.patch("showco.update.push_program"),
            mock.patch(
                "showco.update.run_remote_step",
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
            if command[-2:] == ["branch", "--show-current"]:
                return subprocess.CompletedProcess(command, 0, "main\n", "")
            if command[-2:] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(command, 0, " M file.py\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch(
            "showco.update.provisioning_config",
            return_value=make_config(),
        ):
            result = update.update_from_provisioning_machine(
                ["recs"],
                root=Path("/code"),
                local_root=Path("/code"),
                run_command=run_command,
                output=StringIO(),
            )

        self.assertEqual(result, 1)
        self.assertEqual(
            commands,
            [
                ["git", "-C", "/code/recs", "branch", "--show-current"],
                ["git", "-C", "/code/recs", "status", "--porcelain"],
            ],
        )

    def test_provisioning_update_ignores_untracked_local_files(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-2:] == ["branch", "--show-current"]:
                return subprocess.CompletedProcess(command, 0, "main\n", "")
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
                "showco.update.run_remote_step",
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
                root=Path("/code"),
                local_root=Path("/code"),
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
            if command[-2:] == ["branch", "--show-current"]:
                return subprocess.CompletedProcess(command, 0, "main\n", "")
            if command[-2:] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(command, 0, " M file.py\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch(
            "showco.services.service.current_platform", return_value=Platform.linux
        ):
            result = update.update_target(
                ["recs"],
                root=Path("/code"),
                run_command=run_command,
                output=StringIO(),
            )

        self.assertEqual(result, 1)
        self.assertEqual(
            commands,
            [
                ["git", "-C", "/code/recs", "branch", "--show-current"],
                ["systemctl", "--user", "stop", "recs.service"],
                ["git", "-C", "/code/recs", "status", "--porcelain"],
                ["systemctl", "--user", "start", "recs.service"],
            ],
        )

    def test_target_update_ignores_untracked_repository_files(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-2:] == ["branch", "--show-current"]:
                return subprocess.CompletedProcess(command, 0, "main\n", "")
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
                root=Path("/code"),
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

        class Paths:
            root = Path("/home/tom/code")

        paths = Paths()

    return Config()


if __name__ == "__main__":
    unittest.main()
