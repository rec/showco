from __future__ import annotations

import subprocess
import unittest
from collections.abc import Sequence
from io import StringIO
from pathlib import Path
from unittest import mock

from reccy.services.models import Platform, StatusResult

from showco import update


class UpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        ensure_log = mock.patch(
            "reccy.services.controller.ServiceController._ensure_log"
        )
        ensure_log.start()
        self.addCleanup(ensure_log.stop)

    def test_remote_update_reports_target_before_ssh(self) -> None:
        output = StringIO()
        with mock.patch(
            "showco.update.run_remote_step",
            return_value=update.StepResult(
                program="target",
                step="update",
                command=["ssh"],
                returncode=0,
                output="",
            ),
        ):
            result = update.update_remote_target(
                ["recs"], target_config=make_config(), output=output
            )

        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue(), "Updating tom@bertrand.local from GitHub\n")

    def test_remote_update_command_quotes_root_with_spaces(self) -> None:
        command = update.remote_update_command(["recs"], Path("/srv/show projects"))

        self.assertTrue(command.startswith("cd '/srv/show projects/showco' && "))
        self.assertIn(
            "git status --porcelain --untracked-files=no",
            command,
        )
        self.assertIn('printf "%s\\n" "$status" >&2', command)
        self.assertNotIn("sed -E '/^.. (.*\\/)?uv\\.lock$/d'", command)
        self.assertIn(
            'git fetch "$remote" "+refs/heads/$branch:refs/remotes/$remote/$branch"',
            command,
        )
        self.assertIn('git reset --hard "$remote/$branch"', command)
        self.assertIn(
            "for directory in '/srv/show projects/reccy' '/srv/show projects/recs' "
            "'/srv/show projects/twitcho' '/srv/show projects/lyte'; do ",
            command,
        )
        self.assertEqual(command.count('git reset --hard "$remote/$branch"'), 2)
        self.assertIn(
            'git config --global url."https://github.com/".insteadOf '
            '"ssh://git@github.com/"',
            command,
        )
        self.assertIn(
            "uv sync --locked --directory '/srv/show projects/showco'",
            command,
        )
        self.assertIn(
            "uv sync --locked --directory '/srv/show projects/showco' "
            "&& cd '/srv/show projects/showco' && ",
            command,
        )
        self.assertTrue(
            command.endswith(
                "uv run --locked showco go --target-machine "
                "--root '/srv/show projects' recs"
            )
        )

    def test_remote_update_command_omits_worktree_check(self) -> None:
        command = update.remote_update_command(
            ["recs"], Path("/code"), skip_worktree_check=True
        )

        self.assertNotIn("git status --porcelain", command)
        self.assertIn('git reset --hard "$remote/$branch"', command)
        self.assertTrue(
            command.endswith(
                "uv run --locked showco go --target-machine --root /code recs"
            )
        )

    def test_remote_update_command_can_preserve_recs_settings(self) -> None:
        command = update.remote_update_command(
            ["recs"], Path("/code"), clear_settings=False
        )

        self.assertTrue(
            command.endswith(
                "uv run --locked showco go --target-machine --root /code "
                "--no-clear-settings recs"
            )
        )

    def test_target_update_clears_saved_recs_settings(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch("showco.update.programs_for_repositories", return_value=[]):
            result = update.update_target(
                ["lyte"],
                root=Path("/code"),
                run_command=run_command,
                output=StringIO(),
                clear_settings=True,
            )

        self.assertEqual(result, 0)
        self.assertEqual(
            commands[0],
            ["rm", "-f", "/home/tom/.config/recs/settings.json"],
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
            "showco.services.controller.current_platform", return_value=Platform.linux
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
                ["uv", "sync", "--locked", "--directory", "/code/recs"],
                ["systemctl", "--user", "start", "recs.service"],
                ["sh", "-c", update.recs.status_changes_command()],
            ],
        )

    def test_target_update_reports_recs_status_that_does_not_advance(self) -> None:
        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            if command[-2:] == ["branch", "--show-current"]:
                return subprocess.CompletedProcess(command, 0, "main\n", "")
            if command[-2:] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[-2:] == ["rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(command, 0, "same\n", "")
            if command[:2] == ["sh", "-c"]:
                return subprocess.CompletedProcess(
                    command,
                    1,
                    '{"updated_at":1,"errors":[{"message":"device stalled"}]}\n',
                    "",
                )
            return subprocess.CompletedProcess(command, 0, "", "")

        output = StringIO()
        with mock.patch(
            "showco.services.controller.current_platform", return_value=Platform.linux
        ):
            result = update.update_target(
                ["recs"],
                root=Path("/code"),
                run_command=run_command,
                output=output,
            )

        self.assertEqual(result, 1)
        self.assertIn("recs status is advancing: failed", output.getvalue())
        self.assertIn("Recent Recs errors:\n- device stalled", output.getvalue())

    def test_target_update_resets_repository_when_dependency_sync_fails(self) -> None:
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
            if command[:3] == ["uv", "sync", "--locked"]:
                return subprocess.CompletedProcess(
                    command, 1, "", "package unavailable\n"
                )
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch(
            "showco.services.controller.current_platform", return_value=Platform.linux
        ):
            result = update.update_target(
                ["recs"],
                root=Path("/code"),
                run_command=run_command,
                output=StringIO(),
            )

        self.assertEqual(result, 1)
        self.assertIn(["uv", "sync", "--locked", "--directory", "/code/recs"], commands)
        self.assertIn(["git", "-C", "/code/recs", "reset", "--hard", "old"], commands)
        self.assertIn(["systemctl", "--user", "start", "recs.service"], commands)

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
            "showco.services.controller.current_platform", return_value=Platform.linux
        ):
            result = update.update_target(
                ["showco"],
                root=Path("/code"),
                run_command=run_command,
                output=StringIO(),
            )

        self.assertEqual(result, 1)
        self.assertEqual(
            commands[:-1],
            [
                ["git", "-C", "/code/showco", "branch", "--show-current"],
                ["systemctl", "--user", "stop", "showco.service"],
                ["git", "-C", "/code/showco", "status", "--porcelain"],
                ["git", "-C", "/code/showco", "rev-parse", "HEAD"],
                ["git", "-C", "/code/showco", "pull", "--ff-only"],
                ["git", "-C", "/code/showco", "rev-parse", "@{upstream}"],
                ["git", "-C", "/code/showco", "reset", "--hard", "saved"],
                ["systemctl", "--user", "start", "showco.service"],
            ],
        )
        self.assertEqual(commands[-1][:2], ["sh", "-c"])
        self.assertIn(
            f"http://127.0.0.1:{update.provisioning_config().network.web_port}/status",
            commands[-1][2],
        )

    def test_target_update_resets_to_force_pushed_upstream(self) -> None:
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
            if command[-1:] == ["@{upstream}"]:
                return subprocess.CompletedProcess(command, 0, "new\n", "")
            if command[-2:] == ["pull", "--ff-only"]:
                return subprocess.CompletedProcess(command, 1, "", "diverged\n")
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch(
            "showco.services.controller.current_platform", return_value=Platform.linux
        ):
            result = update.update_target(
                ["showco"],
                root=Path("/code"),
                run_command=run_command,
                output=StringIO(),
            )

        self.assertEqual(result, 0)
        self.assertIn(
            ["git", "-C", "/code/showco", "reset", "--hard", "new"],
            commands,
        )
        self.assertIn(
            ["uv", "sync", "--locked", "--directory", "/code/showco"],
            commands,
        )
        self.assertIn(["systemctl", "--user", "start", "showco.service"], commands)

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

        with (
            mock.patch(
                "showco.services.controller.current_platform",
                return_value=Platform.linux,
            ),
            mock.patch(
                "showco.services.refresh_service_definition",
                return_value=StatusResult(installed=True, running=True),
            ) as refresh,
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
        self.assertEqual(commands[-2][:2], ["sh", "-c"])
        self.assertIn(
            f"http://127.0.0.1:{update.provisioning_config().network.web_port}/status",
            commands[-2][2],
        )
        self.assertEqual(
            commands[-1], ["sh", "-c", update.recs.status_changes_command()]
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
        self.assertEqual(refresh.call_count, 1)

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

        with (
            mock.patch(
                "showco.services.controller.current_platform",
                return_value=Platform.linux,
            ),
            mock.patch(
                "showco.services.refresh_service_definition",
                return_value=StatusResult(installed=True, running=True),
            ) as refresh,
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
        self.assertEqual(refresh.call_count, 1)

    def test_target_update_reinstalls_lyte_with_lyte_environment(self) -> None:
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

        with (
            mock.patch(
                "showco.services.controller.current_platform",
                return_value=Platform.linux,
            ),
            mock.patch(
                "showco.update.provisioning_config", return_value=make_config(True)
            ),
            mock.patch(
                "showco.services.refresh_service_definition",
                return_value=StatusResult(installed=True, running=True),
            ) as refresh,
        ):
            result = update.update_target(
                ["reccy"],
                root=Path("/code"),
                run_command=run_command,
                output=StringIO(),
            )

        self.assertEqual(result, 0)
        self.assertIn(
            [
                "sh",
                "-c",
                "cd /code/lyte && uv run --locked lyte daemon install "
                "--config /code/lyte/patches/wearable-daemon.toml",
            ],
            commands,
        )
        self.assertIn(
            ["sh", "-c", "cd /code/recs && uv run --locked recs daemon install"],
            commands,
        )
        self.assertEqual(refresh.call_count, 1)

    def test_twitcho_install_uses_twitcho_environment(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            return subprocess.CompletedProcess(command, 0, "", "")

        result = update.install_twitcho_service(Path("/code"), "tom", run_command)

        self.assertTrue(result.ok)
        self.assertEqual(
            commands,
            [
                [
                    "sh",
                    "-c",
                    "cd /code/twitcho && uv run --locked twitcho daemon install "
                    "--config /home/tom/.config/twitcho/config.json",
                ]
            ],
        )

    def test_target_update_restarts_lyte_service(self) -> None:
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

        with (
            mock.patch(
                "showco.services.controller.current_platform",
                return_value=Platform.linux,
            ),
            mock.patch(
                "showco.update.provisioning_config", return_value=make_config(True)
            ),
        ):
            result = update.update_target(
                ["lyte"],
                root=Path("/code"),
                run_command=run_command,
                output=StringIO(),
            )

        self.assertEqual(result, 0)
        self.assertIn(["git", "-C", "/code/lyte", "pull", "--ff-only"], commands)
        self.assertIn(["systemctl", "--user", "stop", "lyte.service"], commands)
        self.assertIn(["systemctl", "--user", "start", "lyte.service"], commands)

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
        self.assertFalse(
            any(command[0] == "git" and "reset" in command for command in commands)
        )
        remote_command = remote_update.call_args.args[2][-1]
        self.assertIn("cd /code/showco &&", remote_command)
        self.assertIn('git reset --hard "$remote/$branch"', remote_command)
        self.assertIn("uv sync --locked --directory /code/showco", remote_command)
        self.assertTrue(
            remote_command.endswith(
                "uv run --locked showco go --target-machine --root /code showco reccy"
            )
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

    def test_provisioning_update_autosquashes_from_oldest_fixup_parent(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-2:] == ["branch", "--show-current"]:
                return subprocess.CompletedProcess(command, 0, "main\n", "")
            if command[3:5] == ["log", "-n"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    "new\0fixup! newer\0newer-commit\0newer\0old\0"
                    "fixup! older\0older-commit\0older\0",
                    "",
                )
            if command[-2:] == ["rev-parse", "old^"]:
                return subprocess.CompletedProcess(command, 0, "parent\n", "")
            if command[-2:] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(command, 0, "", "")
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
            [
                "git",
                "-C",
                "/code/recs",
                "rebase",
                "--interactive",
                "--autosquash",
                "parent",
            ],
            commands,
        )

    def test_provisioning_update_skips_autosquash_without_fixup_commits(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-2:] == ["branch", "--show-current"]:
                return subprocess.CompletedProcess(command, 0, "main\n", "")
            if command[3:5] == ["log", "-n"]:
                return subprocess.CompletedProcess(command, 0, "head\0feature\0", "")
            if command[-2:] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(command, 0, "", "")
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

    def test_autosquash_rejects_unknown_fixup_target(self) -> None:
        program = update.Program(
            name="showco", directory=Path("/code/showco"), service_names=[]
        )
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            return subprocess.CompletedProcess(
                command,
                0,
                "new\0fixup! missing target\0old\0other commit\0",
                "",
            )

        result = update.autosquash_program(program, 50, run_command)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.ok)
        self.assertIn("No rebase was started", result.output)
        self.assertFalse(any("rebase" in command for command in commands))

    def test_autosquash_strips_git_record_newlines_from_commit_hashes(self) -> None:
        program = update.Program(
            name="showco", directory=Path("/code/showco"), service_names=[]
        )
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[3:5] == ["log", "-n"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    "new\0fixup! target\0\nold\0target\0",
                    "",
                )
            if command[-2:] == ["rev-parse", "new^"]:
                return subprocess.CompletedProcess(command, 0, "parent\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        result = update.autosquash_program(program, 50, run_command)

        self.assertIsNotNone(result)
        self.assertTrue(result.ok if result else False)
        self.assertIn(
            [
                "git",
                "-C",
                "/code/showco",
                "rev-parse",
                "new^",
            ],
            commands,
        )

    def test_run_command_uses_noninteractive_editor_for_rebase(self) -> None:
        command = ["git", "-C", "/code/recs", "rebase", "--interactive"]
        with mock.patch(
            "showco.update.subprocess.run",
            return_value=subprocess.CompletedProcess(command, 0, "", ""),
        ) as run:
            update.run_command_with_timeout(command)

        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment["GIT_SEQUENCE_EDITOR"], ":")
        self.assertEqual(environment["GIT_EDITOR"], ":")

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
            if command[-2:] == [
                "origin",
                "+refs/heads/main:refs/remotes/origin/main",
            ]:
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
        self.assertIn("recs regular push rejected", output.getvalue())
        self.assertIn("recs current upstream commit:", output.getvalue())
        self.assertIn("1234567890abcdef\nRemote commit subject", output.getvalue())
        self.assertIn("recs push --force-with-lease: ok", output.getvalue())
        self.assertIn(
            [
                "git",
                "-C",
                "/code/recs",
                "fetch",
                "origin",
                "+refs/heads/main:refs/remotes/origin/main",
            ],
            commands,
        )
        self.assertEqual(
            commands[-1],
            [
                "git",
                "-C",
                "/code/recs",
                "push",
                "--force-with-lease=refs/heads/main:1234567890abcdef",
                "origin",
                "HEAD:main",
            ],
        )

    def test_remote_step_reports_remote_output(self) -> None:
        command = ["ssh", "tom@bertrand.local", "showco go"]
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
                [
                    "git",
                    "-C",
                    "/code/recs",
                    "log",
                    "-n",
                    "50",
                    "--format=%H%x00%s%x00",
                    "HEAD",
                ],
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

    def test_provisioning_update_rejects_dirty_uv_lock(self) -> None:
        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            if command[-2:] == ["branch", "--show-current"]:
                return subprocess.CompletedProcess(command, 0, "main\n", "")
            if command[-2:] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(command, 0, " M uv.lock\n", "")
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

        self.assertEqual(result, 1)

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
            "showco.services.controller.current_platform", return_value=Platform.linux
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
                ["sh", "-c", update.recs.status_changes_command()],
            ],
        )

    def test_target_update_ignores_untracked_files(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-2:] == ["branch", "--show-current"]:
                return subprocess.CompletedProcess(command, 0, "main\n", "")
            if command[-2:] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    "?? open-loop.mp4\n",
                    "",
                )
            if command[-2:] == ["rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(command, 0, "same\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch(
            "showco.services.controller.current_platform", return_value=Platform.linux
        ):
            result = update.update_target(
                ["recs"],
                root=Path("/code"),
                run_command=run_command,
                output=StringIO(),
            )

        self.assertEqual(result, 0)
        self.assertIn(["git", "-C", "/code/recs", "pull", "--ff-only"], commands)

    def test_showco_revision_step_checks_running_web_ui(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            return subprocess.CompletedProcess(command, 0, "", "")

        result = update.showco_revision_step(Path("/code"), 17352, run_command)

        self.assertTrue(result.ok)
        self.assertEqual(
            commands,
            [
                [
                    "sh",
                    "-c",
                    "expected=$(git -C /code/showco rev-parse HEAD) && "
                    "curl --fail --silent --show-error --retry 5 --retry-connrefused "
                    "--retry-delay 1 http://127.0.0.1:17352/status | "
                    'grep --fixed-strings "\\"revision\\":\\"$expected\\""',
                ]
            ],
        )

    def test_selected_repositories_defaults_to_all(self) -> None:
        self.assertEqual(update.selected_repositories([]), update.REPOSITORY_NAMES)

    def test_disabled_twitcho_has_no_service_to_restart(self) -> None:
        programs = update.programs_for_repositories(
            ["reccy", "twitcho"], Path("/code"), twitcho_enabled=False
        )

        self.assertNotIn("twitcho", programs[0].service_names)
        self.assertEqual(programs[1].service_names, [])

    def test_disabled_lyte_has_no_service_to_restart(self) -> None:
        programs = update.programs_for_repositories(
            ["reccy", "lyte"], Path("/code"), lyte_enabled=False
        )

        self.assertNotIn("lyte", programs[0].service_names)
        self.assertEqual(programs[1].service_names, [])

    def test_selected_repositories_rejects_unknown_names(self) -> None:
        with self.assertRaisesRegex(SystemExit, "unknown update target"):
            update.selected_repositories(["bogus"])


def make_config(lyte_enabled: bool = False) -> object:
    class Network:
        user = "tom"
        host = "bertrand.local"
        ssh_port = 22
        web_port = 17_352

    class Config:
        network = Network()
        mixers = []

        class Twitch:
            enabled = False

        twitch = Twitch()

        class Lyte:
            enabled = lyte_enabled
            daemon_config = Path("patches/wearable-daemon.toml")

        lyte = Lyte()

        class Paths:
            root = Path("/home/tom/code")

        paths = Paths()

    return Config()


if __name__ == "__main__":
    unittest.main()
