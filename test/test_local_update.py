from __future__ import annotations

import subprocess
import tempfile
import unittest
from collections.abc import Sequence
from io import StringIO
from pathlib import Path
from unittest import mock

from update_helpers import make_config

from showco.deployment import local_update, update


class LocalUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        ensure_log = mock.patch(
            'reccy.services.controller.ServiceController._ensure_log'
        )
        ensure_log.start()
        self.addCleanup(ensure_log.stop)
        self.read_locked_sources = local_update.locked_dependency_sources
        locked_sources = mock.patch(
            'showco.deployment.local_update.locked_dependency_sources', return_value={}
        )
        self.locked_sources = locked_sources.start()
        self.addCleanup(locked_sources.stop)

    def test_locked_dependency_sources_reads_known_git_packages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'uv.lock').write_text(
                '[[package]]\nname = "reccy"\n'
                'source = { git = "https://example/reccy#commit" }\n\n'
                '[[package]]\nname = "pytest"\n'
                'source = { registry = "https://pypi.org/simple" }\n'
            )
            program = update.Program(name='recs', directory=root, service_names=[])

            result = self.read_locked_sources(program, ['reccy'])

        self.assertEqual(result, {'reccy': 'https://example/reccy#commit'})

    def test_provisioning_update_pushes_selected_repos_then_ssh_updates_target(
        self,
    ) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-2:] == ['branch', '--show-current']:
                return subprocess.CompletedProcess(command, 0, 'main\n', '')
            if command[-2:] == ['status', '--porcelain']:
                return subprocess.CompletedProcess(command, 0, '', '')
            if command[-1:] == ['@{upstream}']:
                return subprocess.CompletedProcess(command, 0, 'origin/main\n', '')
            return subprocess.CompletedProcess(command, 0, '', '')

        output = StringIO()
        with (
            mock.patch(
                'showco.deployment.update.provisioning_config',
                return_value=make_config(),
            ),
            mock.patch(
                'showco.deployment.update.run_remote_step',
                return_value=update.StepResult(
                    program='target',
                    step='update',
                    command=['ssh'],
                    returncode=0,
                    output='',
                ),
            ) as remote_update,
        ):
            result = local_update.update_from_provisioning_machine(
                ['showco', 'reccy'],
                root=Path('/code'),
                local_root=Path('/code'),
                run_command=run_command,
                output=output,
            )

        self.assertEqual(result, 0)
        self.assertIn(
            ['git', '-C', '/code/showco', 'push', 'origin', 'HEAD:main'],
            commands,
        )
        self.assertIn(
            ['git', '-C', '/code/reccy', 'push', 'origin', 'HEAD:main'],
            commands,
        )
        self.assertFalse(
            any(command[0] == 'git' and 'reset' in command for command in commands)
        )
        remote_command = remote_update.call_args.args[2][-1]
        self.assertIn('cd /code/showco &&', remote_command)
        self.assertNotIn('git reset', remote_command)
        self.assertNotIn('uv sync', remote_command)
        self.assertTrue(
            remote_command.endswith(
                'uv run --no-sync showco go --target-machine --root /code '
                'reccy recs streamo lyte showco'
            )
        )
        push_indexes = [i for i, c in enumerate(commands) if 'push' in c]
        first_lock = next(i for i, c in enumerate(commands) if c[:2] == ['uv', 'lock'])
        self.assertLess(max(push_indexes[:5]), first_lock)
        self.assertIn('ConnectTimeout=2', remote_update.call_args.args[2])
        self.assertEqual(
            output.getvalue(),
            'Dependency synchronization: unchanged recs, streamo, lyte, showco; '
            'no internal dependencies reccy.\n',
        )

    def test_provisioning_update_rejects_non_main_branches_before_pushing(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            branch = 'feature' if command[2] == '/code/recs' else 'main'
            return subprocess.CompletedProcess(command, 0, f'{branch}\n', '')

        output = StringIO()
        with (
            mock.patch(
                'showco.deployment.update.provisioning_config',
                return_value=make_config(),
            ),
            mock.patch('showco.deployment.update.run_remote_step') as remote_update,
        ):
            result = local_update.update_from_provisioning_machine(
                ['recs', 'showco'],
                root=Path('/code'),
                local_root=Path('/code'),
                run_command=run_command,
                output=output,
            )

        self.assertEqual(result, 1)
        self.assertEqual(
            commands,
            [
                ['git', '-C', '/code/recs', 'branch', '--show-current'],
                ['git', '-C', '/code/showco', 'branch', '--show-current'],
            ],
        )
        self.assertIn('recs main branch: failed', output.getvalue())
        self.assertIn('repository is on feature, expected main', output.getvalue())
        remote_update.assert_not_called()

    def test_provisioning_update_autosquashes_from_oldest_fixup_parent(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-2:] == ['branch', '--show-current']:
                return subprocess.CompletedProcess(command, 0, 'main\n', '')
            if command[3:5] == ['log', '-n']:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    'new\0fixup! newer\0newer-commit\0newer\0old\0'
                    'fixup! older\0older-commit\0older\0',
                    '',
                )
            if command[-2:] == ['rev-parse', 'old^']:
                return subprocess.CompletedProcess(command, 0, 'parent\n', '')
            if command[-2:] == ['status', '--porcelain']:
                return subprocess.CompletedProcess(command, 0, '', '')
            if command[-1:] == ['@{upstream}']:
                return subprocess.CompletedProcess(command, 0, 'origin/main\n', '')
            return subprocess.CompletedProcess(command, 0, '', '')

        with (
            mock.patch(
                'showco.deployment.update.provisioning_config',
                return_value=make_config(),
            ),
            mock.patch(
                'showco.deployment.update.run_remote_step',
                return_value=update.StepResult(
                    program='target',
                    step='update',
                    command=['ssh'],
                    returncode=0,
                    output='',
                ),
            ),
        ):
            result = local_update.update_from_provisioning_machine(
                ['recs'],
                root=Path('/code'),
                local_root=Path('/code'),
                run_command=run_command,
                output=StringIO(),
            )

        self.assertEqual(result, 0)
        self.assertIn(
            [
                'git',
                '-C',
                '/code/recs',
                'rebase',
                '--interactive',
                '--autosquash',
                'parent',
            ],
            commands,
        )

    def test_provisioning_update_skips_autosquash_without_fixup_commits(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-2:] == ['branch', '--show-current']:
                return subprocess.CompletedProcess(command, 0, 'main\n', '')
            if command[3:5] == ['log', '-n']:
                return subprocess.CompletedProcess(command, 0, 'head\0feature\0', '')
            if command[-2:] == ['status', '--porcelain']:
                return subprocess.CompletedProcess(command, 0, '', '')
            if command[-1:] == ['@{upstream}']:
                return subprocess.CompletedProcess(command, 0, 'origin/main\n', '')
            return subprocess.CompletedProcess(command, 0, '', '')

        with (
            mock.patch(
                'showco.deployment.update.provisioning_config',
                return_value=make_config(),
            ),
            mock.patch(
                'showco.deployment.update.run_remote_step',
                return_value=update.StepResult(
                    program='target',
                    step='update',
                    command=['ssh'],
                    returncode=0,
                    output='',
                ),
            ),
        ):
            result = local_update.update_from_provisioning_machine(
                ['recs'],
                root=Path('/code'),
                local_root=Path('/code'),
                run_command=run_command,
                output=StringIO(),
            )

        self.assertEqual(result, 0)

    def test_autosquash_rejects_unknown_fixup_target(self) -> None:
        program = update.Program(
            name='showco', directory=Path('/code/showco'), service_names=[]
        )
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            return subprocess.CompletedProcess(
                command,
                0,
                'new\0fixup! missing target\0old\0other commit\0',
                '',
            )

        result = local_update.autosquash_program(program, 50, run_command)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.ok)
        self.assertIn('No rebase was started', result.output)
        self.assertFalse(any('rebase' in command for command in commands))

    def test_autosquash_strips_git_record_newlines_from_commit_hashes(self) -> None:
        program = update.Program(
            name='showco', directory=Path('/code/showco'), service_names=[]
        )
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[3:5] == ['log', '-n']:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    'new\0fixup! target\0\nold\0target\0',
                    '',
                )
            if command[-2:] == ['rev-parse', 'new^']:
                return subprocess.CompletedProcess(command, 0, 'parent\n', '')
            return subprocess.CompletedProcess(command, 0, '', '')

        result = local_update.autosquash_program(program, 50, run_command)

        self.assertIsNotNone(result)
        self.assertTrue(result.ok if result else False)
        self.assertIn(
            [
                'git',
                '-C',
                '/code/showco',
                'rev-parse',
                'new^',
            ],
            commands,
        )

    def test_push_program_force_pushes_with_pre_autosquash_upstream(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-3:] == ['push', 'origin', 'HEAD:main']:
                return subprocess.CompletedProcess(command, 1, '', 'rejected\n')
            return subprocess.CompletedProcess(command, 0, 'forced\n', '')

        output = StringIO()
        result = local_update.push_program(
            local_update.PublicationState(
                program=update.Program(
                    name='recs', directory=Path('/code/recs'), service_names=[]
                ),
                remote='origin',
                branch='main',
                upstream_commit='1234567890abcdef',
                rewritten=True,
            ),
            run_command,
            output,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.step, 'push --force-with-lease')
        self.assertIn('recs regular push rejected', output.getvalue())
        self.assertIn('recs pre-autosquash upstream commit', output.getvalue())
        self.assertIn('1234567890abcdef', output.getvalue())
        self.assertIn('recs push --force-with-lease: ok', output.getvalue())
        self.assertFalse(any('fetch' in c for c in commands))
        self.assertEqual(
            commands[-1],
            [
                'git',
                '-C',
                '/code/recs',
                'push',
                '--force-with-lease=refs/heads/main:1234567890abcdef',
                'origin',
                'HEAD:main',
            ],
        )

    def test_push_program_skips_network_when_upstream_matches_head(self) -> None:
        program = update.Program(
            name='recs', directory=Path('/code/recs'), service_names=[]
        )
        state = local_update.PublicationState(
            program=program,
            remote='origin',
            branch='main',
            upstream_commit='same-commit',
        )
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            return subprocess.CompletedProcess(command, 0, 'same-commit\n', '')

        result = local_update.push_program(state, run_command)

        self.assertTrue(result.ok)
        self.assertEqual(result.output, 'already published')
        self.assertEqual(
            commands,
            [['git', '-C', '/code/recs', 'rev-parse', 'HEAD']],
        )
        self.assertFalse(any('fetch' in c or 'push' in c for c in commands))

    def test_push_program_does_not_force_unrewritten_history(self) -> None:
        program = update.Program(
            name='recs', directory=Path('/code/recs'), service_names=[]
        )
        state = local_update.PublicationState(
            program=program,
            remote='origin',
            branch='main',
            upstream_commit='original',
        )
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-2:] == ['rev-parse', 'HEAD']:
                return subprocess.CompletedProcess(command, 0, 'local\n', '')
            return subprocess.CompletedProcess(command, 1, '', 'rejected\n')

        result = local_update.push_program(state, run_command)

        self.assertFalse(result.ok)
        self.assertEqual(
            commands,
            [
                ['git', '-C', '/code/recs', 'rev-parse', 'HEAD'],
                ['git', '-C', '/code/recs', 'push', 'origin', 'HEAD:main'],
            ],
        )

    def test_prepare_uses_upstream_captured_before_autosquash(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-2:] == ['branch', '--show-current']:
                return subprocess.CompletedProcess(command, 0, 'main\n', '')
            if command[-2:] == ['status', '--porcelain']:
                return subprocess.CompletedProcess(command, 0, '', '')
            if '--symbolic-full-name' in command:
                return subprocess.CompletedProcess(command, 0, 'origin/main\n', '')
            if command[-2:] == ['rev-parse', '@{upstream}']:
                return subprocess.CompletedProcess(
                    command, 0, 'before-autosquash\n', ''
                )
            if command[3:5] == ['log', '-n']:
                return subprocess.CompletedProcess(
                    command, 0, 'fixup\0fixup! feature\0feature\0feature\0', ''
                )
            if command[-2:] == ['rev-parse', 'fixup^']:
                return subprocess.CompletedProcess(command, 0, 'parent\n', '')
            if command[-3:] == ['push', 'origin', 'HEAD:main']:
                return subprocess.CompletedProcess(command, 1, '', 'rejected\n')
            return subprocess.CompletedProcess(command, 0, '', '')

        result = local_update.prepare_local_repositories(
            ['recs'], Path('/code'), run_command, StringIO()
        )

        self.assertTrue(result)
        capture = ['git', '-C', '/code/recs', 'rev-parse', '@{upstream}']
        rebase = [
            'git',
            '-C',
            '/code/recs',
            'rebase',
            '--interactive',
            '--autosquash',
            'parent',
        ]
        self.assertLess(commands.index(capture), commands.index(rebase))
        self.assertEqual(
            commands[-1],
            [
                'git',
                '-C',
                '/code/recs',
                'push',
                '--force-with-lease=refs/heads/main:before-autosquash',
                'origin',
                'HEAD:main',
            ],
        )

    def test_refresh_program_restores_cutoff_only_lock_change(self) -> None:
        program = update.Program(
            name='recs', directory=Path('/code/recs'), service_names=[]
        )
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            return subprocess.CompletedProcess(command, 0, '', '')

        result = local_update.refresh_program_dependencies(
            program, ['reccy'], run_command, StringIO()
        )

        self.assertEqual(result, local_update.DependencyRefresh.UNCHANGED)
        self.assertIn(
            [
                'git',
                '-C',
                '/code/recs',
                'restore',
                '--staged',
                '--worktree',
                '--',
                'uv.lock',
            ],
            commands,
        )
        self.assertFalse(any('commit' in c for c in commands))

    def test_refresh_program_commits_only_changed_lockfile(self) -> None:
        program = update.Program(
            name='recs', directory=Path('/code/recs'), service_names=[]
        )
        commands: list[list[str]] = []
        self.locked_sources.side_effect = [
            {'reccy': 'old'},
            {'reccy': 'new'},
        ]

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-2:] == ['status', '--porcelain']:
                return subprocess.CompletedProcess(command, 0, ' M uv.lock\n', '')
            if '--symbolic-full-name' in command:
                return subprocess.CompletedProcess(command, 0, 'origin/main\n', '')
            if command[-2:] == ['rev-parse', '@{upstream}']:
                return subprocess.CompletedProcess(command, 0, 'upstream-sha\n', '')
            return subprocess.CompletedProcess(command, 0, '', '')

        result = local_update.refresh_program_dependencies(
            program, ['reccy'], run_command, StringIO()
        )

        self.assertEqual(result, local_update.DependencyRefresh.UPDATED)
        self.assertEqual(
            commands[0],
            [
                'uv',
                'lock',
                '--directory',
                '/code/recs',
                '--upgrade-package',
                'reccy',
            ],
        )
        self.assertEqual(
            commands.count(['git', '-C', '/code/recs', 'status', '--porcelain']),
            2,
        )
        self.assertIn(['git', '-C', '/code/recs', 'add', '--', 'uv.lock'], commands)
        self.assertIn(
            [
                'git',
                '-C',
                '/code/recs',
                'commit',
                '-m',
                'Update internal dependencies',
            ],
            commands,
        )
        self.assertEqual(
            commands[-1],
            ['git', '-C', '/code/recs', 'push', 'origin', 'HEAD:main'],
        )

    def test_refresh_program_rejects_unexpected_changed_path(self) -> None:
        program = update.Program(
            name='recs', directory=Path('/code/recs'), service_names=[]
        )
        commands: list[list[str]] = []
        status_count = 0
        self.locked_sources.side_effect = [
            {'reccy': 'old'},
            {'reccy': 'new'},
        ]

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            nonlocal status_count
            commands.append(list(command))
            if command[-2:] == ['status', '--porcelain']:
                status_count += 1
                if status_count == 1:
                    return subprocess.CompletedProcess(command, 0, ' M uv.lock\n', '')
                return subprocess.CompletedProcess(
                    command, 0, ' M pyproject.toml\n M uv.lock\n', ''
                )
            return subprocess.CompletedProcess(command, 0, '', '')

        output = StringIO()
        result = local_update.refresh_program_dependencies(
            program, ['reccy'], run_command, output
        )

        self.assertEqual(result, local_update.DependencyRefresh.FAILED)
        self.assertIn('unexpected paths', output.getvalue())
        self.assertIn(
            [
                'git',
                '-C',
                '/code/recs',
                'restore',
                '--staged',
                '--worktree',
                '--',
                'uv.lock',
            ],
            commands,
        )
        self.assertFalse(any('commit' in c for c in commands))

    def test_refresh_program_stops_after_failed_lock_check(self) -> None:
        program = update.Program(
            name='recs', directory=Path('/code/recs'), service_names=[]
        )
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-2:] == ['status', '--porcelain']:
                return subprocess.CompletedProcess(command, 0, ' M uv.lock\n', '')
            if command[:3] == ['uv', 'lock', '--check']:
                return subprocess.CompletedProcess(command, 1, '', 'invalid lock\n')
            return subprocess.CompletedProcess(command, 0, '', '')

        result = local_update.refresh_program_dependencies(
            program, ['reccy'], run_command, StringIO()
        )

        self.assertEqual(result, local_update.DependencyRefresh.FAILED)
        self.assertFalse(any(c[:3] == ['uv', 'run', '--locked'] for c in commands))
        self.assertFalse(any('commit' in c for c in commands))

    def test_refresh_publishes_recs_before_locking_showco(self) -> None:
        commands: list[list[str]] = []
        self.locked_sources.side_effect = [
            {'reccy': 'old'},
            {'reccy': 'new'},
            {'reccy': 'old', 'recs': 'old'},
            {'reccy': 'new', 'recs': 'new'},
        ]

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-2:] == ['status', '--porcelain']:
                return subprocess.CompletedProcess(command, 0, ' M uv.lock\n', '')
            if '--symbolic-full-name' in command:
                return subprocess.CompletedProcess(command, 0, 'origin/main\n', '')
            if command[-2:] == ['rev-parse', '@{upstream}']:
                return subprocess.CompletedProcess(command, 0, 'upstream-sha\n', '')
            return subprocess.CompletedProcess(command, 0, '', '')

        output = StringIO()
        result = local_update.refresh_local_dependencies(
            ['recs', 'showco'], Path('/code'), run_command, output
        )

        self.assertTrue(result)
        self.assertEqual(
            output.getvalue(),
            'Dependency synchronization: updated recs, showco.\n',
        )
        recs_push = ['git', '-C', '/code/recs', 'push', 'origin', 'HEAD:main']
        showco_lock = [
            'uv',
            'lock',
            '--directory',
            '/code/showco',
            '--upgrade-package',
            'reccy',
            '--upgrade-package',
            'recs',
        ]
        self.assertLess(commands.index(recs_push), commands.index(showco_lock))

    def test_refresh_reports_unchanged_and_skipped_repositories(self) -> None:
        output = StringIO()

        result = local_update.refresh_local_dependencies(
            ['reccy', 'recs'],
            Path('/code'),
            lambda command: subprocess.CompletedProcess(command, 0, '', ''),
            output,
        )

        self.assertTrue(result)
        self.assertEqual(
            output.getvalue(),
            'Dependency synchronization: unchanged recs; '
            'no internal dependencies reccy.\n',
        )

    def test_provisioning_update_defaults_to_saved_host(self) -> None:
        with (
            mock.patch(
                'showco.deployment.update.provisioning_config',
                return_value=make_config(),
            ),
            mock.patch(
                'showco.deployment.local_update.prepare_local_repositories',
                return_value=True,
            ),
            mock.patch(
                'showco.deployment.local_update.refresh_local_dependencies',
                return_value=True,
            ),
            mock.patch(
                'showco.deployment.update.run_remote_step',
                return_value=update.StepResult(
                    program='target',
                    step='update',
                    command=['ssh'],
                    returncode=0,
                    output='',
                ),
            ) as remote_update,
        ):
            result = local_update.update_from_provisioning_machine(['recs'])

        self.assertEqual(result, 0)
        self.assertIn('tom@bertrand.local', remote_update.call_args.args[2])

    def test_provisioning_update_uses_host_override(self) -> None:
        with (
            mock.patch(
                'showco.deployment.update.provisioning_config',
                return_value=make_config(),
            ),
            mock.patch(
                'showco.deployment.local_update.prepare_local_repositories',
                return_value=True,
            ),
            mock.patch(
                'showco.deployment.local_update.refresh_local_dependencies',
                return_value=True,
            ),
            mock.patch(
                'showco.deployment.update.run_remote_step',
                return_value=update.StepResult(
                    program='target',
                    step='update',
                    command=['ssh'],
                    returncode=0,
                    output='',
                ),
            ) as remote_update,
        ):
            result = local_update.update_from_provisioning_machine(
                ['recs'], host='other.local'
            )

        self.assertEqual(result, 0)
        self.assertIn('tom@other.local', remote_update.call_args.args[2])

    def test_provisioning_update_rejects_dirty_local_repository(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-2:] == ['branch', '--show-current']:
                return subprocess.CompletedProcess(command, 0, 'main\n', '')
            if command[-2:] == ['status', '--porcelain']:
                return subprocess.CompletedProcess(command, 0, ' M file.py\n', '')
            return subprocess.CompletedProcess(command, 0, '', '')

        with mock.patch(
            'showco.deployment.update.provisioning_config',
            return_value=make_config(),
        ):
            result = local_update.update_from_provisioning_machine(
                ['recs'],
                root=Path('/code'),
                local_root=Path('/code'),
                run_command=run_command,
                output=StringIO(),
            )

        self.assertEqual(result, 1)
        self.assertEqual(
            commands,
            [
                ['git', '-C', '/code/recs', 'branch', '--show-current'],
                ['git', '-C', '/code/showco', 'branch', '--show-current'],
                ['git', '-C', '/code/recs', 'status', '--porcelain'],
                ['git', '-C', '/code/showco', 'status', '--porcelain'],
            ],
        )

    def test_provisioning_update_ignores_untracked_local_files(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[-2:] == ['branch', '--show-current']:
                return subprocess.CompletedProcess(command, 0, 'main\n', '')
            if command[-2:] == ['status', '--porcelain']:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    '?? open-loop-forever.md\n?? open-loop.mp4\n',
                    '',
                )
            if command[-1:] == ['@{upstream}']:
                return subprocess.CompletedProcess(command, 0, 'origin/main\n', '')
            return subprocess.CompletedProcess(command, 0, '', '')

        with (
            mock.patch(
                'showco.deployment.update.provisioning_config',
                return_value=make_config(),
            ),
            mock.patch(
                'showco.deployment.update.run_remote_step',
                return_value=update.StepResult(
                    program='target',
                    step='update',
                    command=['ssh'],
                    returncode=0,
                    output='',
                ),
            ),
        ):
            result = local_update.update_from_provisioning_machine(
                ['recs'],
                root=Path('/code'),
                local_root=Path('/code'),
                run_command=run_command,
                output=StringIO(),
            )

        self.assertEqual(result, 0)
        self.assertIn(
            ['git', '-C', '/code/recs', 'push', 'origin', 'HEAD:main'], commands
        )

    def test_provisioning_update_rejects_dirty_uv_lock(self) -> None:
        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            if command[-2:] == ['branch', '--show-current']:
                return subprocess.CompletedProcess(command, 0, 'main\n', '')
            if command[-2:] == ['status', '--porcelain']:
                return subprocess.CompletedProcess(command, 0, ' M uv.lock\n', '')
            if command[-1:] == ['@{upstream}']:
                return subprocess.CompletedProcess(command, 0, 'origin/main\n', '')
            return subprocess.CompletedProcess(command, 0, '', '')

        with (
            mock.patch(
                'showco.deployment.update.provisioning_config',
                return_value=make_config(),
            ),
            mock.patch(
                'showco.deployment.update.run_remote_step',
                return_value=update.StepResult(
                    program='target',
                    step='update',
                    command=['ssh'],
                    returncode=0,
                    output='',
                ),
            ),
        ):
            result = local_update.update_from_provisioning_machine(
                ['recs'],
                root=Path('/code'),
                local_root=Path('/code'),
                run_command=run_command,
                output=StringIO(),
            )

        self.assertEqual(result, 1)
