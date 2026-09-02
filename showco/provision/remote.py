from __future__ import annotations

import shlex
import string
import sys
from pathlib import Path
from subprocess import CalledProcessError, TimeoutExpired

from reccy import subprocess

from .. import network_config, repositories
from . import config, script, ssh, verify

SSH_CLEANUP_TIMEOUT_SECONDS = 15
REMOTE_PROVISION_TIMEOUT_SECONDS = 1_800
PROVISIONING_FINGERPRINT_NAME = "provisioning-fingerprint"
WIFI_STATUS_COMMAND = "nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device status"
PASSWORDLESS_SUDO_COMMAND = (
    "sudo -n true || { "
    "echo 'ERROR: passwordless sudo is required. Prepare the SD card with "
    "showco prepare-card before its first boot.' >&2; exit 1; "
    "}"
)


def provision_remote(
    provision_config: config.Config,
    local_script: Path,
    remote_script: str,
    *,
    system: bool = False,
    fingerprint: str | None = None,
) -> None:
    uploaded = False
    if provision_config.accept_changed_host_key:
        ssh.remove_known_host(provision_config)
    print(f"Waiting for SSH connection to {provision_config.ssh_target}...")
    ssh.wait_for_ssh(provision_config)
    require_passwordless_sudo(provision_config)
    validate_remote_worktrees(provision_config)
    topology = preflight_network_config(provision_config)
    print(f"Checking {provision_config.ssh_target}...")
    ssh.run_ssh(
        provision_config,
        "set -e; uname -a; id; command -v sudo; command -v apt-get",
        timeout_seconds=ssh.SSH_VERIFICATION_TIMEOUT_SECONDS,
    )
    try:
        print("Copying provisioning script...")
        ssh.run_scp(provision_config, local_script, remote_script)
        uploaded = True

        print(f"Running provisioning on {provision_config.ssh_target}...")
        ssh.run_ssh(
            provision_config,
            script.remote_command(provision_config, remote_script, system=system),
            timeout_seconds=REMOTE_PROVISION_TIMEOUT_SECONDS,
        )

        if ssh.provisioning_reboot_required(provision_config):
            print(f"Waiting for {provision_config.ssh_target} to reboot...")
            ssh.schedule_remote_reboot(provision_config)
            ssh.wait_for_rebooted_ssh(provision_config)
        else:
            print(f"No reboot is required for {provision_config.ssh_target}.")

        print(f"Checking provisioned services on {provision_config.ssh_target}...")
        verify.report_verification_results(
            verify.wait_for_provisioning_ready(provision_config, topology)
        )
        if fingerprint is not None:
            record_applied_provisioning_fingerprint(provision_config, fingerprint)
    finally:
        if uploaded:
            try:
                ssh.run_ssh(
                    provision_config,
                    f"rm -f {shlex.quote(remote_script)}",
                    exit_on_error=False,
                    timeout_seconds=SSH_CLEANUP_TIMEOUT_SECONDS,
                )
            except CalledProcessError as e:
                if sys.exc_info()[0] is None:
                    raise
                print(
                    f"WARNING: Could not remove remote provisioning script: {e}",
                    file=sys.stderr,
                )


def preflight_network_config(
    provision_config: config.Config,
) -> network_config.NetworkTopology:
    print(f"Checking Wi-Fi interfaces on {provision_config.ssh_target}...")
    status = ssh.capture_ssh(provision_config, WIFI_STATUS_COMMAND)
    interfaces = network_config.wifi_interfaces_from_status(status)
    assignment = network_config.assign_wifi(
        interfaces, provision_config.network.swap_wifi
    )
    topology = network_config.select_topology(
        provision_config, assignment.secondary is not None
    )
    network_config.network_commands(provision_config, assignment, topology)
    return topology


def require_passwordless_sudo(provision_config: config.Config) -> None:
    print(f"Checking passwordless sudo on {provision_config.ssh_target}...")
    ssh.run_ssh(provision_config, PASSWORDLESS_SUDO_COMMAND)


def applied_provisioning_fingerprint(provision_config: config.Config) -> str | None:
    try:
        path = provisioning_fingerprint_path(provision_config)
        completed = subprocess.run(
            ssh.ssh_command(
                provision_config,
                provision_config.ssh_target,
                f"cat {shlex.quote(str(path))}",
            ),
            capture_output=True,
            check=False,
            text=True,
            timeout=ssh.SSH_VERIFICATION_TIMEOUT_SECONDS,
        )
    except TimeoutExpired:
        return None
    fingerprint = completed.stdout.strip()
    if completed.returncode != 0 or len(fingerprint) != 64:
        return None
    if not all(character in string.hexdigits for character in fingerprint):
        return None
    return fingerprint


def record_applied_provisioning_fingerprint(
    provision_config: config.Config, fingerprint: str
) -> None:
    path = provisioning_fingerprint_path(provision_config)
    temporary = path.with_name(f".{path.name}.XXXXXX")
    command = " && ".join(
        [
            f"mkdir -p {shlex.quote(str(path.parent))}",
            f"temporary=$(mktemp {shlex.quote(str(temporary))})",
            f"printf '%s\\n' {shlex.quote(fingerprint)} > \"$temporary\"",
            f'mv "$temporary" {shlex.quote(str(path))}',
        ]
    )
    ssh.run_ssh(provision_config, command)


def provisioning_fingerprint_path(provision_config: config.Config) -> Path:
    return (
        Path("/home")
        / provision_config.network.user
        / ".local/state/showco"
        / (PROVISIONING_FINGERPRINT_NAME)
    )


def validate_remote_worktrees(provision_config: config.Config) -> None:
    print(f"Checking target repository worktrees on {provision_config.ssh_target}...")
    ssh.run_ssh(
        provision_config,
        remote_worktree_command(provision_config.paths.root),
    )


def remote_worktree_command(root: Path) -> str:
    names = " ".join(
        [
            "showco",
            *(name for name in repositories.REPOSITORY_NAMES if name != "showco"),
        ]
    )
    script = f"""root={shlex.quote(str(root))}
failed=false
for name in {names}; do
  path="$root/$name"
  if [[ -e "$path" && ! -d "$path/.git" ]]; then
    printf '%s: not a Git checkout: %s\\n' "$name" "$path"
    failed=true
    continue
  fi
  if [[ ! -d "$path/.git" ]]; then
    continue
  fi
  if ! status=$(git -C "$path" status --short --untracked-files=no); then
    printf '%s: git status failed\\n' "$name"
    failed=true
    continue
  fi
  if [[ -n "$status" ]]; then
    printf '%s:\\n%s\\n' "$name" "$status"
    failed=true
  fi
done
if [[ "$failed" == true ]]; then
  exit 1
fi"""
    return f"bash -c {shlex.quote(script)}"
