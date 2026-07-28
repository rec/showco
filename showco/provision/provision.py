#!/usr/bin/env python3
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import cast

import tyro
from pydantic import BaseModel

import showco

PROVISION_DIR = Path(__file__).resolve().parent
REMOTE_SCRIPT_TEMPLATE = "provision_locally.tmpl.sh"
REMOTE_SCRIPT = (PROVISION_DIR / REMOTE_SCRIPT_TEMPLATE).read_text()
REMOTE_GITHUB_KEY_TEMPLATE = "remote_github_key.tmpl.sh"
REBOOT_WAIT_SECONDS = 300


class GitRepo(BaseModel, frozen=True):
    url: str
    refname: str


class Network(BaseModel, frozen=True):
    name: str = ""
    dhcp_start: str = ""
    dhcp_end: str = ""
    ip_address: str = ""
    subnet: str = ""
    password: str = ""


class Config(BaseModel, frozen=True):
    host: str
    user: str
    ssh_port: int
    recs: GitRepo
    twitcho: GitRepo
    showco: GitRepo
    web_port: int
    x18: bool
    swap_wifi: bool
    network_topology: str
    twitcho_enabled: bool
    private_wifi_ssid: str
    private_wifi_password: str
    external_wifi_ssid: str
    external_wifi_password: str
    x18_subnet: str
    x18_host: str
    x18_usb_device_name: str


class ProvisionOptions(BaseModel, frozen=True):
    config: Path
    secrets: Path
    host: str | None
    user: str | None
    port: int | None
    recs_repo: str | None
    twitcho_repo: str | None
    showco_repo: str | None


class VerificationResult(BaseModel, frozen=True):
    name: str
    error: str
    note: str = ""


def main(argv: list[str] | None = None) -> int:
    options = tyro.cli(
        provision_options,
        args=argv,
        description="Provision a reachable Raspberry Pi over SSH",
    )
    env = merge_values(read_toml(options.config), read_toml(options.secrets))
    config = config_from_args(options, env)
    ssh_target = f"{config.user}@{config.host}"
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
        provision_remote(config, ssh_target, local_script, remote_script)
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
    if config.ssh_port == 22:
        return [host]
    return [host, f"[{host}]:{config.ssh_port}"]


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
    if not config.x18_usb_device_name or config.x18_usb_device_name == "TODO":
        return VerificationResult(
            name="X18 USB device", error="", note="not configured"
        )
    command = (
        f"arecord -l | grep -F {shlex.quote(config.x18_usb_device_name)} >/dev/null"
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
        note=f"{config.x18_usb_device_name} not detected",
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
    return f"showco {config.host}"


def remote_github_key_command(config: Config) -> str:
    comment = shlex.quote(github_key_title(config))
    template = script_dir() / REMOTE_GITHUB_KEY_TEMPLATE
    return template.read_text().replace("{comment}", comment)


def provision_options(
    config: Path = PROVISION_DIR / "config.toml",
    secrets: Path = PROVISION_DIR / "secrets.toml",
    host: str | None = None,
    user: str | None = None,
    port: int | None = None,
    recs_repo: str | None = None,
    twitcho_repo: str | None = None,
    showco_repo: str | None = None,
) -> ProvisionOptions:
    return ProvisionOptions(
        config=config,
        secrets=secrets,
        host=host,
        user=user,
        port=port,
        recs_repo=recs_repo,
        twitcho_repo=twitcho_repo,
        showco_repo=showco_repo,
    )


def config_from_args(args: ProvisionOptions, env: dict[str, object]) -> Config:
    network = table_value(env, "network")
    networks = table_value(env, "networks")
    internal = table_value(networks, "internal")
    external = table_value(networks, "external")
    internal_wired_networks = network_dict(
        table_value(internal, "wired"),
        "networks.internal.wired",
    )
    internal_wifi = first_network(
        network_dict(
            table_value(internal, "wifi"),
            "networks.internal.wifi",
        ),
        "networks.internal.wifi",
        default=Network(name="showbox"),
    )
    external_wifi = first_network(
        network_dict(
            table_value(external, "wifi"),
            "networks.external.wifi",
        ),
        "networks.external.wifi",
        default=Network(),
    )
    usb = table_value(env, "usb")
    twitch = table_value(env, "twitch")
    git = table_value(env, "git")
    host = value_or_env(args.host, network, "host")
    user = value_or_env(
        args.user,
        network,
        "user",
        default=os.environ.get("USER", ""),
    )
    ssh_port = value_or_int(args.port, network, "ssh_port", default=22)
    recs = git_repo("recs", table_value(git, "recs"), override=args.recs_repo)
    twitcho = git_repo(
        "twitcho",
        table_value(git, "twitcho"),
        override=args.twitcho_repo,
    )
    showco = git_repo(
        "showco",
        table_value(git, "showco"),
        override=args.showco_repo,
    )
    web_port = int_value(network, "web_port", default=17352)
    x18 = bool(internal_wired_networks)
    x18_host = ""
    if x18:
        x18_network = first_network(
            internal_wired_networks,
            "networks.internal.wired",
        )
        x18_host = require_value(
            "networks.internal.wired.x18.ip_address",
            x18_network.ip_address,
        )
        x18_subnet = string_or_default(x18_network.subnet, "10.43.0.0/24")
    else:
        x18_subnet = "10.43.0.0/24"

    return Config(
        host=require_value("network.host", host),
        user=require_value("network.user or USER", user),
        ssh_port=ssh_port,
        recs=recs,
        twitcho=twitcho,
        showco=showco,
        web_port=web_port,
        x18=x18,
        swap_wifi=bool_value(network, "swap_wifi", default=False),
        network_topology=string_value(network, "topology"),
        twitcho_enabled=bool_value(twitch, "enabled", default=False),
        private_wifi_ssid=string_or_default(internal_wifi.name, "showbox"),
        private_wifi_password=internal_wifi.password,
        external_wifi_ssid=external_wifi.name,
        external_wifi_password=external_wifi.password,
        x18_subnet=x18_subnet,
        x18_host=x18_host,
        x18_usb_device_name=string_value(usb, "x18_device_name"),
    )


def value_or_env(
    value: str | None,
    env: dict[str, object],
    name: str,
    *,
    default: str = "",
) -> str:
    if value:
        return value
    return string_value(env, name, default=default)


def value_or_int(
    value: int | None,
    env: dict[str, object],
    name: str,
    *,
    default: int,
) -> int:
    if value is not None:
        return value
    return int_value(env, name, default=default)


def git_repo(name: str, values: dict[str, object], *, override: str | None) -> GitRepo:
    url = override or string_value(values, "url")
    return GitRepo(
        url=require_value(f"git.{name}.url", url),
        refname=string_value(values, "refname"),
    )


def table_value(values: dict[str, object], name: str) -> dict[str, object]:
    value = values.get(name, {})
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    sys.exit(f"ERROR: {name} must be a table")


def merge_values(
    config: dict[str, object], secrets: dict[str, object]
) -> dict[str, object]:
    result = dict(config)
    for k, v in secrets.items():
        current = result.get(k)
        if isinstance(v, dict) and isinstance(current, dict):
            result[k] = merge_values(
                cast(dict[str, object], current),
                cast(dict[str, object], v),
            )
        else:
            result[k] = v
    return result


def network_dict(values: dict[str, object], name: str) -> dict[str, Network]:
    networks = {}
    for k, v in values.items():
        if not isinstance(v, dict):
            sys.exit(f"ERROR: {name}.{k} must be a table")
        table = cast(dict[str, object], v)
        networks[k] = Network(
            name=string_value(table, "name"),
            dhcp_start=string_value(table, "dhcp_start"),
            dhcp_end=string_value(table, "dhcp_end"),
            ip_address=string_value(table, "ip_address"),
            subnet=string_value(table, "subnet"),
            password=string_value(table, "password"),
        )
    return networks


def first_network(
    networks: dict[str, Network],
    name: str,
    *,
    default: Network | None = None,
) -> Network:
    if networks:
        if "x18" in networks:
            return networks["x18"]
        return next(iter(networks.values()))
    if default is not None:
        return default
    sys.exit(f"ERROR: {name} must contain at least one network")


def string_or_default(value: str, default: str) -> str:
    if value:
        return value
    return default


def require_value(name: str, value: str) -> str:
    if value and value != "TODO":
        return value
    sys.exit(f"ERROR: {name} is required")


def bool_value(values: dict[str, object], name: str, *, default: bool) -> bool:
    value = values.get(name, default)
    if isinstance(value, bool):
        return value
    sys.exit(f"ERROR: {name} must be a boolean")


def int_value(values: dict[str, object], name: str, *, default: int) -> int:
    value = values.get(name, default)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    sys.exit(f"ERROR: {name} must be an integer")


def string_value(
    values: dict[str, object],
    name: str,
    *,
    default: str = "",
) -> str:
    value = values.get(name)
    if value is None:
        value = default
    if isinstance(value, str):
        return os.path.expandvars(value)
    sys.exit(f"ERROR: {name} must be a string")


def shell_bool(value: bool) -> str:
    return "true" if value else "false"


def remote_command(config: Config, remote_script: str) -> str:
    values = {
        "SHOW_USER": config.user,
        "CODE_DIR": f"/home/{config.user}/code",
        "RECS_REPO": config.recs.url,
        "RECS_REFNAME": config.recs.refname,
        "TWITCHO_REPO": config.twitcho.url,
        "TWITCHO_REFNAME": config.twitcho.refname,
        "SHOWCO_REPO": config.showco.url,
        "SHOWCO_REFNAME": config.showco.refname,
        "SHOWCO_PORT": str(config.web_port),
        "X18": shell_bool(config.x18),
        "SWAP_WIFI": shell_bool(config.swap_wifi),
        "NETWORK_TOPOLOGY": config.network_topology,
        "TWITCHO_ENABLED": shell_bool(config.twitcho_enabled),
        "PRIVATE_WIFI_SSID": config.private_wifi_ssid,
        "PRIVATE_WIFI_PASSWORD": config.private_wifi_password,
        "EXTERNAL_WIFI_SSID": config.external_wifi_ssid,
        "EXTERNAL_WIFI_PASSWORD": config.external_wifi_password,
        "SHOWCO_PI_X18_SUBNET": config.x18_subnet,
        "SHOWCO_X18_HOST": config.x18_host,
        "X18_USB_DEVICE_NAME": config.x18_usb_device_name,
    }
    assignments = [f"{k}={shlex.quote(v)}" for k, v in values.items()]
    return " ".join([*assignments, "bash", shlex.quote(remote_script)])


def run_ssh(config: Config, target: str, command: str) -> None:
    run(
        ssh_command(config, target, command, allocate_tty=True),
    )


def run_scp(config: Config, source: Path, target: str) -> None:
    run(
        ["scp", "-P", str(config.ssh_port), str(source), target],
    )


def capture_ssh(config: Config, target: str, command: str) -> str:
    completed = run(
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
    result.extend(["-p", str(config.ssh_port), target, command])
    return result


def run(
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


def read_toml(path: Path) -> dict[str, object]:
    path = path.expanduser()
    if not path.exists():
        return {}
    try:
        parsed = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        sys.exit(f"ERROR: Cannot parse {path}: {e}")
    return {k: toml_value(v) for k, v in parsed.items()}


def toml_value(value: object) -> object:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, list) and all(isinstance(i, str) for i in value):
        return value
    if isinstance(value, list):
        return [toml_value(i) for i in value]
    if isinstance(value, dict):
        return {k: toml_value(v) for k, v in value.items()}
    return None


def script_dir() -> Path:
    return PROVISION_DIR


if __name__ == "__main__":
    sys.exit(main())
