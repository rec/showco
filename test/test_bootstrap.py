from __future__ import annotations

import subprocess
import tarfile
from io import BytesIO
from pathlib import Path

import pytest

from showco.deployment import bootstrap


@pytest.mark.parametrize('status', [0, 1, 130])
def test_published_updater_runs_without_changing_installed_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    checkout = tmp_path / 'showco'
    provision = checkout / 'showco' / 'provision'
    provision.mkdir(parents=True)
    (checkout / 'installed-version').write_text('old CLI with go')
    (provision / 'config.toml').write_text('target configuration')
    (provision / 'secrets.toml').write_text('target secrets')
    archive = BytesIO()
    with tarfile.open(fileobj=archive, mode='w') as files:
        for name in ('showco/provision/config.toml', 'uv.lock'):
            data = b'published content'
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            files.addfile(entry, BytesIO(data))
    commands: list[list[str]] = []
    directories: list[Path] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        if 'archive' in command:
            return subprocess.CompletedProcess(command, 0, archive.getvalue())
        if command[0] == 'uv':
            directory = kwargs['cwd']
            assert isinstance(directory, Path)
            directories.append(directory)
            assert directory != checkout
            assert (directory.parent / 'recs').readlink() == tmp_path / 'recs'
            assert (directory.parent / 'lyte').readlink() == tmp_path / 'lyte'
            assert (directory / 'uv.lock').read_text() == 'published content'
            assert (
                directory / 'showco/provision/config.toml'
            ).read_text() == 'target configuration'
            assert (
                directory / 'showco/provision/secrets.toml'
            ).read_text() == 'target secrets'
            assert (checkout / 'installed-version').read_text() == 'old CLI with go'
            assert 'VIRTUAL_ENV' not in kwargs['env']
            return subprocess.CompletedProcess(command, status)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setenv('VIRTUAL_ENV', str(checkout / '.venv'))
    monkeypatch.setattr(bootstrap.subprocess, 'run', run)

    assert bootstrap.main(tmp_path, ['--clear-settings', 'recs']) == status
    assert commands == [
        ['git', '-C', str(checkout), 'fetch'],
        ['git', '-C', str(checkout), 'archive', '@{upstream}'],
        [
            'uv',
            'run',
            '--locked',
            '--no-dev',
            'python',
            '-m',
            'showco',
            'deploy',
            '--target-machine',
            '--root',
            str(tmp_path),
            '--clear-settings',
            'recs',
        ],
    ]
    assert not directories[0].exists()
    assert (checkout / 'installed-version').read_text() == 'old CLI with go'


def test_failed_bootstrap_stops_before_starting_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    commands: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        raise subprocess.CalledProcessError(128, command)

    monkeypatch.setattr(bootstrap.subprocess, 'run', run)

    assert bootstrap.main(tmp_path, ['showco']) == 1
    assert len(commands) == 1
    assert 'Cannot load the published showCo updater' in capsys.readouterr().err
