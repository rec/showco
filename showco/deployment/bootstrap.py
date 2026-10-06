"""Standalone bootstrap sent to the target over SSH standard input."""

from __future__ import annotations

import os
import subprocess
import sys
import tarfile
from io import BytesIO
from pathlib import Path
from shutil import copy2
from tempfile import TemporaryDirectory


def main(root: Path, arguments: list[str]) -> int:
    checkout = root / 'showco'
    try:
        print('Loading the published showCo updater...', flush=True)
        subprocess.run(['git', '-C', str(checkout), 'fetch'], check=True)
        archive = subprocess.run(
            ['git', '-C', str(checkout), 'archive', '@{upstream}'],
            check=True,
            capture_output=True,
        )
        with TemporaryDirectory(prefix='showco-update-') as directory:
            updater = Path(directory) / 'showco'
            updater.mkdir()
            # Service specifications are read from the existing sibling checkouts.
            for name in ('recs', 'lyte'):
                (updater.parent / name).symlink_to(
                    root / name, target_is_directory=True
                )
            with tarfile.open(fileobj=BytesIO(archive.stdout)) as files:
                files.extractall(updater, filter='data')
            for name in ('config.toml', 'secrets.toml'):
                source = checkout / 'showco' / 'provision' / name
                if source.is_file():
                    copy2(source, updater / 'showco' / 'provision' / name)
            environment = dict(os.environ)
            environment.pop('VIRTUAL_ENV', None)
            return subprocess.run(
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
                    str(root),
                    *arguments,
                ],
                cwd=updater,
                env=environment,
                check=False,
            ).returncode
    except subprocess.CalledProcessError as error:
        if error.stderr:
            sys.stderr.buffer.write(error.stderr)
        print(f'Cannot load the published showCo updater: {error}', file=sys.stderr)
        return 1
    except (OSError, tarfile.TarError) as error:
        print(f'Cannot load the published showCo updater: {error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Interrupted', file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(main(Path(sys.argv[1]), sys.argv[2:]))
