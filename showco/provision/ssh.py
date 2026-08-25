from __future__ import annotations

import sys
import time
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess, TimeoutExpired

from reccy import subprocess

from . import config

REBOOT_WAIT_SECONDS = 300
SSH_CONNECT_TIMEOUT_SECONDS = 2
SSH_VERIFICATION_TIMEOUT_SECONDS = 15
SCP_TIMEOUT_SECONDS = 60


def wait_for_rebooted_ssh(provision_config: config.Config) -> None:
    wait_for_ssh_disconnect(provision_config)
    wait_for_ssh(provision_config, timeout_seconds=REBOOT_WAIT_SECONDS)


def provisioning_reboot_required(provision_config: config.Config) -> bool:
    return (
        capture_ssh(
            provision_config,
            "test -f /run/showco-provision-reboot-required && echo true || echo false",
        )
        == "true"
    )


def schedule_remote_reboot(provision_config: config.Config) -> None:
    run_ssh(
        provision_config, "sudo systemd-run --on-active=2s /usr/bin/systemctl reboot"
    )


def wait_for_ssh_disconnect(provision_config: config.Config) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if not ssh_is_reachable(provision_config):
            return
        time.sleep(1)
    sys.exit(f"ERROR: {provision_config.ssh_target} did not drop SSH before reboot")


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
        f"ERROR: {provision_config.ssh_target} did not accept SSH "
        f"within {timeout_seconds}s"
    )


def ssh_is_reachable(provision_config: config.Config) -> bool:
    completed = subprocess.run(
        ssh_command(
            provision_config,
            provision_config.ssh_target,
            "true",
            connect_timeout=1,
        ),
        capture_output=True,
        check=False,
        text=True,
    )
    if has_changed_host_key(completed):
        if not provision_config.accept_changed_host_key:
            sys.exit(
                "ERROR: SSH host key changed for "
                f"{provision_config.ssh_target}. Verify the new key and set "
                "accept_changed_host_key = true after reflashing."
            )
        remove_known_host(provision_config)
        return False
    return completed.returncode == 0


def has_changed_host_key(completed: CompletedProcess[str]) -> bool:
    return "REMOTE HOST IDENTIFICATION HAS CHANGED" in (
        f"{completed.stdout}{completed.stderr}"
    )


def remove_known_host(provision_config: config.Config) -> None:
    for host in known_host_names(provision_config):
        print(f"Removing stale SSH host key for {host}...")
        subprocess.run(
            ["ssh-keygen", "-R", host],
            capture_output=True,
            check=False,
            text=True,
        )


def known_host_names(provision_config: config.Config) -> list[str]:
    host = provision_config.network.host
    if provision_config.network.ssh_port == 22:
        return [host]
    return [host, f"[{host}]:{provision_config.network.ssh_port}"]


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
                "scp",
                "-o",
                f"ConnectTimeout={SSH_CONNECT_TIMEOUT_SECONDS}",
                "-P",
                str(provision_config.network.ssh_port),
                str(source),
                f"{provision_config.ssh_target}:{remote_path}",
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
    output = f"{error.stdout or ''}{error.stderr or ''}".strip()
    message = (
        f"ERROR: SSH connection or command failed for {provision_config.ssh_target}. "
        f"SSH connect timeout is {SSH_CONNECT_TIMEOUT_SECONDS} seconds."
    )
    if output:
        message += f"\nssh said: {output}"
    return message


def ssh_command(
    provision_config: config.Config,
    target: str,
    command: str,
    *,
    allocate_tty: bool = False,
    connect_timeout: int | None = SSH_CONNECT_TIMEOUT_SECONDS,
) -> list[str]:
    result = ["ssh"]
    if allocate_tty:
        result.append("-t")
    if connect_timeout is not None:
        result.extend(["-o", f"ConnectTimeout={connect_timeout}"])
    result.extend(["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"])
    result.extend(["-p", str(provision_config.network.ssh_port), target, command])
    return result


def run_command(
    command: list[str],
    *,
    capture_output: bool = False,
    timeout_seconds: int | None = None,
) -> CompletedProcess[str]:
    if timeout_seconds is None:
        return subprocess.run(
            command,
            capture_output=capture_output,
            check=True,
            text=True,
        )
    return subprocess.run(
        command,
        capture_output=capture_output,
        check=True,
        text=True,
        timeout=timeout_seconds,
    )
