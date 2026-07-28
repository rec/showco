#!/usr/bin/env python3
from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import tyro
from pydantic import BaseModel

import showco

from .config import (
    Config,
    config_from_values,
    external_wifi,
    internal_wifi,
    merge_values,
    read_toml,
    require_value,
    string_or_default,
    x18,
)

PROVISION_DIR = Path(__file__).resolve().parent
REMOTE_SCRIPT_TEMPLATE = "provision_locally.tmpl.sh"
REMOTE_SCRIPT = (PROVISION_DIR / REMOTE_SCRIPT_TEMPLATE).read_text()
REMOTE_GITHUB_KEY_TEMPLATE = "remote_github_key.tmpl.sh"
REBOOT_WAIT_SECONDS = 300


class VerificationResult(BaseModel, frozen=True):
    name: str
    error: str
    note: str = ""


def main(argv: list[str] | None = None) -> int:
    return tyro.cli(
        run,
        args=argv,
        description="Provision a reachable Raspberry Pi over SSH",
    )


def run(
    config: Path = PROVISION_DIR / "config.toml",
    secrets: Path = PROVISION_DIR / "secrets.toml",
    host: str | None = None,
    user: str | None = None,
    port: int | None = None,
    recs_repo: str | None = None,
    twitcho_repo: str | None = None,
    showco_repo: str | None = None,
) -> int:
    env = merge_values(read_toml(config), read_toml(secrets))
    parsed_config = config_from_values(
        env,
        host=host,
        user=user,
        port=port,
        recs_repo=recs_repo,
        twitcho_repo=twitcho_repo,
        showco_repo=showco_repo,
    )
    validate_config(parsed_config)
    ssh_target = f"{parsed_config.network.user}@{parsed_config.network.host}"
    remote_script = "/tmp/showco-provision-pi.sh"

    with tempfile.NamedTemporaryFile(
        "w",
        delete=False,
        prefix="showco-provision-pi.",
        suffix=".sh",
    ) as fp:
        local_script = Path(fp.name)
        fp.write(REMOTE_SCRIPT)
    try:
        provision_remote(parsed_config, ssh_target, local_script, remote_script)
    finally:
        local_script.unlink(missing_ok=True)

    print(f"Provisioned {ssh_target}.")
    return 0


def provision_remote(
    config: Config, ssh_target: str, local_script: Path, remote_script: str
) -> None:
    uploaded = False
    print(f"Waiting for SSH connection to {ssh_target}...")
    wait_for_ssh(config, ssh_target)
    print(f"Checking {ssh_target}...")
    run_ssh(
        config,
        ssh_target,
        "set -e; uname -a; id; command -v sudo; command -v apt-get",
    )
    ensure_github_account_key(config, ssh_target)

    try:
        print("Copying provisioning script...")
        run_scp(config, local_script, f"{ssh_target}:{remote_script}")
        uploaded = True

        print(f"Running provisioning on {ssh_target}...")
        run_ssh(config, ssh_target, remote_command(config, remote_script))

        print(f"Waiting for {ssh_target} to reboot...")
        wait_for_rebooted_ssh(config, ssh_target)

        print(f"Checking provisioned services on {ssh_target}...")
        report_verification_results(verify_provisioning(config, ssh_target))
    finally:
        if uploaded:
            try:
                run_ssh(config, ssh_target, f"rm -f {shlex.quote(remote_script)}")
            except subprocess.CalledProcessError as e:
                if sys.exc_info()[0] is None:
                    raise
                print(
                    f"WARNING: Could not remove remote provisioning script: {e}",
                    file=sys.stderr,
                )


def validate_config(config: Config) -> None:
    errors = config_errors(config)
    if not errors:
        return
    sys.exit("ERROR: invalid provisioning configuration\n" + "\n".join(errors))


def config_errors(config: Config) -> list[str]:
    errors = []
    external = external_wifi(config)
    private = internal_wifi(config)
    if not external.name or external.name == "TODO":
        errors.append("- networks.external.wifi.external.name is required")
    if not private.password or private.password == "TODO":
        errors.append("- networks.internal.wifi.private.password is required")
    if not external.password or external.password == "TODO":
        errors.append("- networks.external.wifi.external.password is required")
    return errors


def wait_for_rebooted_ssh(config: Config, ssh_target: str) -> None:
    wait_for_ssh_disconnect(config, ssh_target)
    wait_for_ssh(config, ssh_target, timeout_seconds=REBOOT_WAIT_SECONDS)


def wait_for_ssh_disconnect(config: Config, ssh_target: str) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if not ssh_is_reachable(config, ssh_target):
            return
        time.sleep(1)
    sys.exit(f"ERROR: {ssh_target} did not drop SSH before reboot")


def wait_for_ssh(
    config: Config,
    ssh_target: str,
    *,
    timeout_seconds: int | None = None,
) -> None:
    deadline = None
    if timeout_seconds is not None:
        deadline = time.monotonic() + timeout_seconds
    while deadline is None or time.monotonic() < deadline:
        if ssh_is_reachable(config, ssh_target):
            return
        time.sleep(1)
    sys.exit(f"ERROR: {ssh_target} did not accept SSH within {timeout_seconds}s")


def ssh_is_reachable(config: Config, ssh_target: str) -> bool:
    completed = showco.run(
        ssh_command(config, ssh_target, "true", connect_timeout=1),
        capture_output=True,
        check=False,
        text=True,
    )
    if has_changed_host_key(completed):
        remove_known_host(config, ssh_target)
        return False
    return completed.returncode == 0


def has_changed_host_key(completed: subprocess.CompletedProcess[str]) -> bool:
    output = f"{completed.stdout}{completed.stderr}"
    return "REMOTE HOST IDENTIFICATION HAS CHANGED" in output


def remove_known_host(config: Config, ssh_target: str) -> None:
    for host in known_host_names(config, ssh_target):
        print(f"Removing stale SSH host key for {host}...")
        showco.run(
            ["ssh-keygen", "-R", host],
            capture_output=True,
            check=False,
            text=True,
        )


def known_host_names(config: Config, ssh_target: str) -> list[str]:
    host = ssh_target.rsplit("@", maxsplit=1)[-1]
    if config.network.ssh_port == 22:
        return [host]
    return [host, f"[{host}]:{config.network.ssh_port}"]


def verify_provisioning(config: Config, ssh_target: str) -> list[VerificationResult]:
    return [
        verify_remote_command(
            config,
            ssh_target,
            "no failed systemd units",
            "systemctl --failed --no-legend --plain",
            expect_empty_stdout=True,
        ),
        verify_remote_command(
            config,
            ssh_target,
            "recs project status is clean",
            project_status_command("recs"),
            expect_empty_stdout=True,
        ),
        verify_remote_command(
            config,
            ssh_target,
            "twitcho project status is clean",
            project_status_command("twitcho"),
            expect_empty_stdout=True,
        ),
        verify_remote_command(
            config,
            ssh_target,
            "showco project status is clean",
            project_status_command("showco"),
            expect_empty_stdout=True,
        ),
        verify_remote_command(
            config,
            ssh_target,
            "recs service is active",
            user_systemctl_command("is-active recs.service"),
        ),
        verify_remote_command(
            config,
            ssh_target,
            "showco service is active",
            user_systemctl_command("is-active showco.service"),
        ),
        verify_remote_command(
            config,
            ssh_target,
            "showco service status is healthy",
            user_systemctl_command("status showco.service --no-pager >/dev/null"),
        ),
        verify_remote_command(
            config,
            ssh_target,
            "showco journal is readable",
            user_session_command(
                "journalctl --user -u showco.service -n 100 --no-pager >/dev/null"
            ),
        ),
        verify_remote_command(
            config,
            ssh_target,
            "NetworkManager device status is readable",
            "nmcli device status >/dev/null",
        ),
        verify_remote_command(
            config,
            ssh_target,
            "NetworkManager connection list is readable",
            "nmcli connection show >/dev/null",
        ),
        verify_x18_usb_device(config, ssh_target),
    ]


def project_status_command(project: str) -> str:
    return f'git -C "$HOME/code/{project}" status --short'


def user_systemctl_command(arguments: str) -> str:
    return user_session_command(f"systemctl --user {arguments}")


def user_session_command(command: str) -> str:
    return f"uid=$(id -u); XDG_RUNTIME_DIR=/run/user/$uid {command}"


def verify_x18_usb_device(config: Config, ssh_target: str) -> VerificationResult:
    if not config.usb.x18_device_name or config.usb.x18_device_name == "TODO":
        return VerificationResult(
            name="X18 USB device", error="", note="not configured"
        )
    command = (
        f"arecord -l | grep -F {shlex.quote(config.usb.x18_device_name)} >/dev/null"
    )
    completed = showco.run(
        ssh_command(config, ssh_target, command, connect_timeout=1),
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode == 0:
        return VerificationResult(name="X18 USB device", error="")
    return VerificationResult(
        name="X18 USB device",
        error="",
        note=f"{config.usb.x18_device_name} not detected",
    )


def verify_remote_command(
    config: Config,
    ssh_target: str,
    name: str,
    command: str,
    *,
    expect_empty_stdout: bool = False,
) -> VerificationResult:
    completed = showco.run(
        ssh_command(config, ssh_target, command, connect_timeout=1),
        capture_output=True,
        check=False,
        text=True,
    )
    output = f"{completed.stdout}{completed.stderr}".strip()
    if completed.returncode == 0 and (not expect_empty_stdout or not output):
        return VerificationResult(name=name, error="")
    if not output:
        output = f"command exited with status {completed.returncode}"
    return VerificationResult(name=name, error=output)


def report_verification_results(results: list[VerificationResult]) -> None:
    errors = [r for r in results if r.error]
    notes = [r for r in results if r.note]
    if not errors:
        if notes:
            print("Notes:")
            for note in notes:
                print(f"- {note.name}: {note.note}")
        print("Success!")
        return
    print("ERROR")
    for error in errors:
        print(f"- {error.name}: {error.error}")
    if notes:
        print("Notes:")
        for note in notes:
            print(f"- {note.name}: {note.note}")
    sys.exit(1)


def ensure_github_account_key(config: Config, ssh_target: str) -> None:
    if not shutil.which("gh"):
        sys.exit(
            "ERROR: gh is required on the provisioning machine "
            "to add the Pi SSH key to GitHub."
        )
    print("Creating or reusing Raspberry Pi GitHub SSH key...")
    public_key = capture_ssh(config, ssh_target, remote_github_key_command(config))
    if not public_key.startswith("ssh-ed25519 "):
        sys.exit(f"ERROR: Unexpected SSH public key from {ssh_target}: {public_key}")
    title = github_key_title(config)
    if github_key_exists(public_key):
        print(f"GitHub SSH key already exists: {title}")
        return
    with tempfile.NamedTemporaryFile(
        "w",
        delete=False,
        prefix="showco-pi-github-key.",
        suffix=".pub",
    ) as fp:
        key_file = Path(fp.name)
        fp.write(public_key + "\n")
    try:
        add_github_key(key_file, title)
    finally:
        key_file.unlink(missing_ok=True)


def add_github_key(key_file: Path, title: str) -> None:
    try:
        showco.run(
            ["gh", "ssh-key", "add", str(key_file), "--title", title],
            capture_output=True,
            check=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        sys.exit(
            gh_error_message(
                "Could not add the Pi SSH key to GitHub from the provisioning machine.",
                e,
            )
        )


def github_key_exists(public_key: str) -> bool:
    try:
        completed = showco.run(
            ["gh", "api", "user/keys", "--jq", ".[].key"],
            capture_output=True,
            check=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        sys.exit(
            gh_error_message(
                "Could not list GitHub SSH keys from the provisioning machine.",
                e,
            )
        )
    key = github_key_material(public_key)
    return any(
        github_key_material(line) == key for line in completed.stdout.splitlines()
    )


def gh_error_message(message: str, error: subprocess.CalledProcessError) -> str:
    details = (error.stderr or error.stdout or "").strip()
    result = f"ERROR: {message} Run `gh auth status` on this machine."
    if details:
        result += f"\ngh said: {details}"
    return result


def github_key_material(public_key: str) -> str:
    fields = public_key.split()
    if len(fields) < 2:
        return public_key
    return " ".join(fields[:2])


def github_key_title(config: Config) -> str:
    return f"showco {config.network.host}"


def remote_github_key_command(config: Config) -> str:
    comment = shlex.quote(github_key_title(config))
    template = script_dir() / REMOTE_GITHUB_KEY_TEMPLATE
    return template.read_text().replace("{comment}", comment)


def shell_bool(value: bool) -> str:
    return "true" if value else "false"


def remote_command(config: Config, remote_script: str) -> str:
    private = internal_wifi(config)
    external = external_wifi(config)
    x18_network = x18(config)
    x18_host = ""
    x18_subnet = "10.43.0.0/24"
    if x18_network is not None:
        x18_host = require_value(
            "networks.internal.wired.x18.ip_address",
            x18_network.ip_address,
        )
        x18_subnet = string_or_default(x18_network.subnet, "10.43.0.0/24")
    values = {
        "SHOW_USER": config.network.user,
        "CODE_DIR": f"/home/{config.network.user}/code",
        "RECS_REPO": config.git.recs.url,
        "RECS_REFNAME": config.git.recs.refname,
        "TWITCHO_REPO": config.git.twitcho.url,
        "TWITCHO_REFNAME": config.git.twitcho.refname,
        "SHOWCO_REPO": config.git.showco.url,
        "SHOWCO_REFNAME": config.git.showco.refname,
        "SHOWCO_PORT": str(config.network.web_port),
        "X18": shell_bool(x18_network is not None),
        "SWAP_WIFI": shell_bool(config.network.swap_wifi),
        "NETWORK_TOPOLOGY": config.network.topology,
        "TWITCHO_ENABLED": shell_bool(config.twitch.enabled),
        "PRIVATE_WIFI_SSID": string_or_default(private.name, "showbox"),
        "PRIVATE_WIFI_PASSWORD": private.password,
        "EXTERNAL_WIFI_SSID": external.name,
        "EXTERNAL_WIFI_PASSWORD": external.password,
        "SHOWCO_PI_X18_SUBNET": x18_subnet,
        "SHOWCO_X18_HOST": x18_host,
        "X18_USB_DEVICE_NAME": config.usb.x18_device_name,
    }
    assignments = [f"{k}={shlex.quote(v)}" for k, v in values.items()]
    return " ".join([*assignments, "bash", shlex.quote(remote_script)])


def run_ssh(config: Config, target: str, command: str) -> None:
    run_command(
        ssh_command(config, target, command, allocate_tty=True),
    )


def run_scp(config: Config, source: Path, target: str) -> None:
    run_command(
        ["scp", "-P", str(config.network.ssh_port), str(source), target],
    )


def capture_ssh(config: Config, target: str, command: str) -> str:
    completed = run_command(
        ssh_command(config, target, command),
        capture_output=True,
    )
    return completed.stdout.strip()


def ssh_command(
    config: Config,
    target: str,
    command: str,
    *,
    allocate_tty: bool = False,
    connect_timeout: int | None = None,
) -> list[str]:
    result = ["ssh"]
    if allocate_tty:
        result.append("-t")
    if connect_timeout is not None:
        result.extend(["-o", f"ConnectTimeout={connect_timeout}"])
    result.extend(["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"])
    result.extend(["-p", str(config.network.ssh_port), target, command])
    return result


def run_command(
    command: list[str],
    *,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    return showco.run(
        command,
        capture_output=capture_output,
        check=True,
        text=True,
    )


def script_dir() -> Path:
    return PROVISION_DIR


if __name__ == "__main__":
    sys.exit(main())
