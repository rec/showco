from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import webbrowser

import tyro
from pydantic import BaseModel, Field

from ..provision import config, ssh
from . import machine_role, update


class PanelOptions(BaseModel, frozen=True):
    port: int | None = Field(default=None, ge=1, le=65535)
    """Local browser port; defaults to the configured control-panel port."""


def main(argv: list[str] | None = None) -> int:
    options = tyro.cli(
        PanelOptions,
        args=argv,
        description='Open the target panel through SSH; Ctrl-C closes the tunnel',
    )
    return open_panel(options)


def open_panel(
    options: PanelOptions, *, target_config: config.Config | None = None
) -> int:
    machine_role.require_provisioning_machine('showco panel')
    provision_config = target_config or update.provisioning_config()
    port = options.port or provision_config.network.web_port
    command = ssh.ssh_command(
        provision_config,
        provision_config.ssh_target,
        "printf 'showco-panel-ready\\n'; cat >/dev/null",
    )
    command[1:1] = [
        '-T',
        '-o',
        'ExitOnForwardFailure=yes',
        '-o',
        'ServerAliveInterval=15',
        '-o',
        'ServerAliveCountMax=3',
        '-L',
        f'127.0.0.1:{port}:127.0.0.1:{provision_config.network.web_port}',
    ]
    print(f'Connecting to {provision_config.ssh_target}...')
    with tempfile.TemporaryFile(mode='w+t') as errors:
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=errors,
                text=True,
                start_new_session=True,
            )
        except OSError as error:
            sys.exit(f'ERROR: cannot start the panel connection: {error}')
        try:
            assert process.stdout is not None
            ready = False
            for line in process.stdout:
                if line.strip() == 'showco-panel-ready':
                    ready = True
                    url = f'http://127.0.0.1:{port}'
                    print(
                        f'Control panel: {url}\nPress Ctrl-C to close the connection.'
                    )
                    if not webbrowser.open(url):
                        print(
                            'Could not open your browser. Open the URL above manually.'
                        )
                    break
                print(line, end='')
            status = process.wait()
            if not ready and status == 0:
                sys.exit('ERROR: SSH did not confirm that the panel tunnel opened')
            return status
        finally:
            if process.stdin is not None:
                process.stdin.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if process.stdout is not None:
                process.stdout.close()
            errors.seek(0)
            shutil.copyfileobj(errors, sys.stderr)
