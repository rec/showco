from __future__ import annotations

import os
import subprocess
import unittest
from collections.abc import Sequence
from io import StringIO
from pathlib import Path

from showco.deployment import logs
from showco.provision import config


class LogsTests(unittest.TestCase):
    def test_fetches_selected_remote_service_logs(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            return subprocess.CompletedProcess(command, 0, 'logs\n', '')

        output = StringIO()

        result = logs.fetch_logs(
            logs.LogsOptions(services=['recs', 'streamo'], lines=25),
            target_config=target_config(),
            run_command=run_command,
            output=output,
        )

        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue(), 'logs\n')
        self.assertEqual(
            commands,
            [
                [
                    'ssh',
                    '-o',
                    'ConnectTimeout=2',
                    '-o',
                    'BatchMode=yes',
                    '-o',
                    'StrictHostKeyChecking=accept-new',
                    '-p',
                    '22',
                    'tom@bertrand.local',
                    'tail --lines=25 "$HOME/.local/state/recs/recs.log" '
                    '"$HOME/.local/state/streamo/streamo.log"',
                ]
            ],
        )

    def test_defaults_to_all_service_logs(self) -> None:
        self.assertEqual(
            logs.selected_services([]), ['showco', 'recs', 'streamo', 'lyte']
        )

    def test_rejects_unknown_services(self) -> None:
        with self.assertRaisesRegex(SystemExit, 'unknown log target'):
            logs.selected_services(['recs', 'unknown'])

    def test_remote_logs_command_quotes_units(self) -> None:
        self.assertEqual(
            logs.remote_logs_command(['recs', 'lyte'], 50),
            'tail --lines=50 "$HOME/.local/state/recs/recs.log" '
            '"$HOME/.local/state/lyte/lyte.log"',
        )


def target_config() -> config.Config:
    return config.config_from_values(
        {
            'network': {'host': 'bertrand.local', 'user': 'tom'},
            'paths': {},
            'networks': {
                'internal': {'subnet': '10.0.0.0/24', 'wifi': {}},
                'external': {'wifi': {}},
            },
            'usb': {},
            'stream': {},
            'lyte': {},
            'git': {
                'reccy': {'url': 'git@github.com:rec/reccy'},
                'recs': {'url': 'git@github.com:rec/recs'},
                'streamo': {'url': 'git@github.com:rec/streamo'},
                'showco': {'url': 'git@github.com:rec/showco'},
                'lyte': {'url': 'git@github.com:rec/lyte'},
            },
        }
    )


if __name__ == '__main__':
    unittest.main()


def test_clear_logs_preserves_running_writer_and_unrelated_history(
    tmp_path: Path,
) -> None:
    root = tmp_path / "code with ' quotes"
    checkout = root / 'showco'
    checkout.mkdir(parents=True)
    errors = checkout / 'showco-errors.txt'
    errors.write_text('old error')
    paths: list[Path] = [errors]
    for service in logs.SERVICE_NAMES:
        directory = tmp_path / '.local/state' / service
        directory.mkdir(parents=True)
        for suffix in ('', '.1', '.2'):
            path = directory / f'{service}.log{suffix}'
            path.write_text('old log\n')
            paths.append(path)
    history = tmp_path / '.local/state/showco/incidents.json'
    history.write_text('saved incidents')
    unrelated = tmp_path / 'unrelated.log'
    unrelated.write_text('keep this')
    (history.parent / 'showco.log.link').symlink_to(unrelated)
    current = history.parent / 'showco.log'
    inode = current.stat().st_ino
    with current.open('a') as writer:
        completed = subprocess.run(
            ['/bin/sh', '-c', logs.remote_clear_logs_command(root)],
            env=dict(os.environ, HOME=str(tmp_path)),
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert 'Cleared 13 log files.' in completed.stdout
        assert all(p.read_text() == '' for p in paths)
        writer.write('new entry\n')
    assert current.stat().st_ino == inode
    assert current.read_text() == 'new entry\n'
    assert history.read_text() == 'saved incidents'
    assert unrelated.read_text() == 'keep this'


def test_clear_logs_skips_missing_files(tmp_path: Path) -> None:
    completed = subprocess.run(
        ['/bin/sh', '-c', logs.remote_clear_logs_command(tmp_path)],
        env=dict(os.environ, HOME=str(tmp_path)),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert 'Cleared 0 log files.' in completed.stdout
    assert list(tmp_path.iterdir()) == []
