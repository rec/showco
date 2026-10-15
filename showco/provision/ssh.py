from __future__ import annotations

import base64
import hashlib
import re
import shutil
import sys
import tempfile
import time
from io import TextIOWrapper
from pathlib import Path
from subprocess import (
    PIPE,
    STDOUT,
    CalledProcessError,
    CompletedProcess,
    Popen,
    TimeoutExpired,
)
from threading import Thread
from typing import cast

from reccy.runtime import subprocess
from tqdm import tqdm

from . import config

REBOOT_WAIT_SECONDS = 300
SSH_CONNECT_TIMEOUT_SECONDS = 2
SSH_VERIFICATION_TIMEOUT_SECONDS = 15
SCP_TIMEOUT_SECONDS = 60


def wait_for_rebooted_ssh(
    provision_config: config.Config, previous_boot_id: str
) -> None:
    deadline = time.monotonic() + REBOOT_WAIT_SECONDS
    while time.monotonic() < deadline:
        if (
            boot_id := ssh_boot_id(provision_config)
        ) is not None and boot_id != previous_boot_id:
            return
        time.sleep(1)
    sys.exit(f'ERROR: {provision_config.ssh_target} did not complete its reboot')


def provisioning_reboot_required(provision_config: config.Config) -> bool:
    return (
        capture_ssh(
            provision_config,
            'test -f /run/showco-provision-reboot-required && echo true || echo false',
        )
        == 'true'
    )


def schedule_remote_reboot(provision_config: config.Config) -> str:
    boot_id = capture_ssh(provision_config, 'cat /proc/sys/kernel/random/boot_id')
    if not boot_id:
        sys.exit(f'ERROR: cannot read boot ID from {provision_config.ssh_target}')
    run_ssh(
        provision_config, 'sudo systemd-run --on-active=2s /usr/bin/systemctl reboot'
    )
    return boot_id


def ssh_boot_id(provision_config: config.Config) -> str | None:
    try:
        completed = cast(
            CompletedProcess[str],
            subprocess.run(
                ssh_command(
                    provision_config,
                    provision_config.ssh_target,
                    'cat /proc/sys/kernel/random/boot_id',
                    connect_timeout=1,
                ),
                capture_output=True,
                check=False,
                text=True,
                timeout=5,
            ),
        )
    except TimeoutExpired:
        return None
    if has_changed_host_key(completed):
        approve_changed_host_key(provision_config, completed)
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def wait_for_ssh(
    provision_config: config.Config,
    *,
    timeout_seconds: int | None = None,
) -> None:
    deadline = None
    if timeout_seconds is not None:
        deadline = time.monotonic() + timeout_seconds
    while deadline is None or time.monotonic() < deadline:
        if ssh_is_reachable(provision_config):
            return
        time.sleep(1)
    sys.exit(
        f'ERROR: {provision_config.ssh_target} did not accept SSH '
        f'within {timeout_seconds}s'
    )


def ssh_is_reachable(provision_config: config.Config) -> bool:
    completed = subprocess.run(
        ssh_command(
            provision_config,
            provision_config.ssh_target,
            'true',
            connect_timeout=1,
        ),
        capture_output=True,
        check=False,
        text=True,
    )
    completed = cast(CompletedProcess[str], completed)
    if has_changed_host_key(completed):
        approve_changed_host_key(provision_config, completed)
        return False
    return completed.returncode == 0


def has_changed_host_key(completed: CompletedProcess[str]) -> bool:
    return 'REMOTE HOST IDENTIFICATION HAS CHANGED' in (
        f'{completed.stdout}{completed.stderr}'
    )


def approve_changed_host_key(
    provision_config: config.Config, completed: CompletedProcess[str]
) -> None:
    target = provision_config.ssh_target
    if not provision_config.accept_changed_host_key:
        sys.exit(f'ERROR: SSH host key changed for {target}; replacement is disabled')
    match = re.search(r'SHA256:[A-Za-z0-9+/]+', completed.stderr or '')
    if match is None:
        sys.exit(f'ERROR: cannot identify the changed SSH host key for {target}')
    fingerprint = match.group()
    host = provision_config.network.host
    port = provision_config.network.ssh_port
    try:
        scanned = subprocess.run(
            ['ssh-keyscan', '-T', '5', '-p', str(port), host],
            capture_output=True,
            check=True,
            text=True,
            timeout=SSH_VERIFICATION_TIMEOUT_SECONDS,
        )
        keys = []
        for line in cast(str, scanned.stdout).splitlines():
            if line.startswith('#') or len(fields := line.split()) != 3:
                continue
            digest = (
                base64.b64encode(
                    hashlib.sha256(base64.b64decode(fields[2], validate=True)).digest()
                )
                .decode()
                .rstrip('=')
            )
            if f'SHA256:{digest}' == fingerprint:
                keys.append(
                    f'{known_host_names(provision_config)[0]} {fields[1]} {fields[2]}'
                )
        if not keys:
            sys.exit(
                f'ERROR: scanned SSH key for {target} does not match {fingerprint}'
            )
        print(f'SSH host key changed for {target}.')
        print(f'New host key: {fingerprint}')
        print('Confirm this key belongs to your Pi before replacing the saved key.')
        try:
            answer = input('Replace the saved SSH host key? Type yes to approve: ')
        except EOFError:
            answer = ''
        if answer.strip().lower() != 'yes':
            sys.exit('ERROR: SSH host key replacement declined; saved keys unchanged')
        known_hosts = Path.home() / '.ssh' / 'known_hosts'
        subprocess.run(
            [
                'ssh-keygen',
                '-R',
                known_host_names(provision_config)[0],
                '-f',
                str(known_hosts),
            ],
            capture_output=True,
            check=True,
            text=True,
            timeout=SSH_VERIFICATION_TIMEOUT_SECONDS,
        )
        with known_hosts.open('a') as stream:
            stream.write('\n' + '\n'.join(keys) + '\n')
    except (OSError, ValueError, CalledProcessError, TimeoutExpired) as error:
        sys.exit(f'ERROR: cannot replace SSH host key for {target}: {error}')


def known_host_names(provision_config: config.Config) -> list[str]:
    host = provision_config.network.host
    if provision_config.network.ssh_port == 22:
        return [host]
    return [f'[{host}]:{provision_config.network.ssh_port}']


def run_ssh(
    provision_config: config.Config,
    command: str,
    *,
    exit_on_error: bool = True,
    timeout_seconds: int = SSH_VERIFICATION_TIMEOUT_SECONDS,
) -> None:
    try:
        run_command(
            ssh_command(
                provision_config,
                provision_config.ssh_target,
                command,
                allocate_tty=True,
            ),
            timeout_seconds=timeout_seconds,
        )
    except (CalledProcessError, TimeoutExpired) as error:
        if not exit_on_error:
            raise
        sys.exit(ssh_error_message(provision_config, error))


def run_scp(provision_config: config.Config, source: Path, remote_path: str) -> None:
    try:
        run_command(
            [
                'scp',
                '-o',
                f'ConnectTimeout={SSH_CONNECT_TIMEOUT_SECONDS}',
                '-P',
                str(provision_config.network.ssh_port),
                str(source),
                f'{provision_config.ssh_target}:{remote_path}',
            ],
            timeout_seconds=SCP_TIMEOUT_SECONDS,
        )
    except (CalledProcessError, TimeoutExpired) as error:
        sys.exit(ssh_error_message(provision_config, error))


def capture_ssh(provision_config: config.Config, command: str) -> str:
    try:
        completed = run_command(
            ssh_command(provision_config, provision_config.ssh_target, command),
            capture_output=True,
            timeout_seconds=SSH_VERIFICATION_TIMEOUT_SECONDS,
        )
    except (CalledProcessError, TimeoutExpired) as error:
        sys.exit(ssh_error_message(provision_config, error))
    return completed.stdout.strip()


def ssh_error_message(
    provision_config: config.Config, error: CalledProcessError | TimeoutExpired
) -> str:
    output = ''.join(
        x.decode(errors='replace') if isinstance(x, bytes) else x or ''
        for x in (error.stdout, error.stderr)
    ).strip()
    if isinstance(error, TimeoutExpired):
        message = (
            f'ERROR: SSH operation for {provision_config.ssh_target} '
            f'timed out after {error.timeout} seconds.'
        )
    else:
        message = (
            f'ERROR: SSH command for {provision_config.ssh_target} '
            f'failed with exit status {error.returncode}.'
        )
    if output:
        message += f'\nssh said: {output}'
    return message


def ssh_command(
    provision_config: config.Config,
    target: str,
    command: str,
    *,
    allocate_tty: bool = False,
    connect_timeout: int | None = SSH_CONNECT_TIMEOUT_SECONDS,
) -> list[str]:
    result = ['ssh']
    if allocate_tty:
        result.append('-t')
    if connect_timeout is not None:
        result.extend(['-o', f'ConnectTimeout={connect_timeout}'])
    result.extend(['-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new'])
    result.extend(['-p', str(provision_config.network.ssh_port), target, command])
    return result


def run_command(
    command: list[str],
    *,
    capture_output: bool = False,
    timeout_seconds: int | None = None,
) -> CompletedProcess[str]:
    if not capture_output:
        process = Popen(
            command,
            stdout=PIPE,
            stderr=STDOUT,
            start_new_session=True,
        )
        assert process.stdout is not None
        returncode = None
        with (
            TextIOWrapper(process.stdout, newline='') as stream,
            tempfile.TemporaryFile(mode='w+t', newline='') as record,
            tqdm(
                desc='Running remote command',
                bar_format='{desc} [{elapsed}]',
                file=sys.stdout,
                disable=not sys.stdout.isatty(),
                leave=False,
            ) as progress,
        ):

            def collect_output() -> None:
                for line in stream:
                    record.write(line)
                    if line.startswith('==> '):
                        progress.set_description_str(line[4:].strip())

            reader = Thread(target=collect_output)
            reader.start()
            try:
                returncode = process.wait(timeout=timeout_seconds)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                reader.join()
                progress.close()
                if returncode != 0:
                    record.flush()
                    record.seek(0)
                    shutil.copyfileobj(record, sys.stdout)
                    sys.stdout.flush()
        if returncode != 0:
            raise CalledProcessError(returncode, command)
        return CompletedProcess(command, returncode)
    if timeout_seconds is None:
        return cast(
            CompletedProcess[str],
            subprocess.run(
                command,
                capture_output=capture_output,
                check=True,
                text=True,
            ),
        )
    return cast(
        CompletedProcess[str],
        subprocess.run(
            command,
            capture_output=capture_output,
            check=True,
            text=True,
            timeout=timeout_seconds,
        ),
    )
