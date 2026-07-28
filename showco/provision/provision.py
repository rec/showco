#!/usr/bin/env python3
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

import tyro
from pydantic import BaseModel

import showco

PROVISION_DIR = Path(__file__).resolve().parent
REMOTE_SCRIPT_TEMPLATE = "provision_locally.tmpl.sh"
REMOTE_SCRIPT = (PROVISION_DIR / REMOTE_SCRIPT_TEMPLATE).read_text()
REMOTE_GITHUB_KEY_TEMPLATE = "remote_github_key.tmpl.sh"


class Config(BaseModel, frozen=True):
    host: str
    user: str
    port: str
    recs_repo: str
    twitcho_repo: str
    showco_repo: str
    showco_port: str
    is_x18_wired: bool
    showco_x18_host: str
    x18_usb_device_name: str


class ProvisionOptions(BaseModel, frozen=True):
    config: Path
    secrets: Path
    host: str | None
    user: str | None
    port: str | None
    recs_repo: str | None
    twitcho_repo: str | None
    showco_repo: str | None


def main(argv: list[str] | None = None) -> int:
    options = tyro.cli(
        provision_options,
        args=argv,
        description="Provision a reachable Raspberry Pi over SSH",
    )
    env = read_toml(options.config)
    env |= read_toml(options.secrets)
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
    print(f"Checking SSH connection to {ssh_target}...")
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
    port: str | None = None,
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
    host = value_or_env(args.host, env, "showco_pi_host")
    user = value_or_env(
        args.user,
        env,
        "showco_pi_user",
        default=os.environ.get("USER", ""),
    )
    port = value_or_env(args.port, env, "showco_pi_ssh_port", default="22")
    recs_repo = value_or_env(args.recs_repo, env, "recs_repo")
    twitcho_repo = value_or_env(args.twitcho_repo, env, "twitcho_repo")
    showco_repo = value_or_env(args.showco_repo, env, "showco_repo")
    showco_port = string_value(env, "showco_port", default="17352")
    is_x18_wired = bool_value(env, "is_x18_wired", default=True)
    showco_x18_host = ""
    if is_x18_wired:
        showco_x18_host = require_value(
            "showco_x18_wired_ethernet_ip_address",
            string_value(env, "showco_x18_wired_ethernet_ip_address"),
        )

    return Config(
        host=require_value("showco_pi_host", host),
        user=require_value("showco_pi_user or USER", user),
        port=require_value("showco_pi_ssh_port", port),
        recs_repo=require_value("recs_repo", recs_repo),
        twitcho_repo=require_value("twitcho_repo", twitcho_repo),
        showco_repo=require_value("showco_repo", showco_repo),
        showco_port=require_value("showco_port", showco_port),
        is_x18_wired=is_x18_wired,
        showco_x18_host=showco_x18_host,
        x18_usb_device_name=string_value(env, "x18_usb_device_name"),
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


def require_value(name: str, value: str) -> str:
    if value and value != "TODO":
        return value
    sys.exit(f"ERROR: {name} is required")


def bool_value(values: dict[str, object], name: str, *, default: bool) -> bool:
    value = values.get(name, default)
    if isinstance(value, bool):
        return value
    sys.exit(f"ERROR: {name} must be a boolean")


def string_value(values: dict[str, object], name: str, *, default: str = "") -> str:
    value = values.get(name, default)
    if isinstance(value, str):
        return os.path.expandvars(value)
    sys.exit(f"ERROR: {name} must be a string")


def remote_command(config: Config, remote_script: str) -> str:
    values = {
        "SHOW_USER": config.user,
        "CODE_DIR": f"/home/{config.user}/code",
        "RECS_REPO": config.recs_repo,
        "TWITCHO_REPO": config.twitcho_repo,
        "SHOWCO_REPO": config.showco_repo,
        "SHOWCO_PORT": config.showco_port,
        "SHOWCO_X18_HOST": config.showco_x18_host,
        "X18_USB_DEVICE_NAME": config.x18_usb_device_name,
    }
    assignments = [f"{k}={shlex.quote(v)}" for k, v in values.items()]
    return " ".join([*assignments, "bash", shlex.quote(remote_script)])


def run_ssh(config: Config, target: str, command: str) -> None:
    run(
        ["ssh", "-t", "-p", config.port, target, command],
    )


def run_scp(config: Config, source: Path, target: str) -> None:
    run(
        ["scp", "-P", config.port, str(source), target],
    )


def capture_ssh(config: Config, target: str, command: str) -> str:
    completed = run(
        ["ssh", "-p", config.port, target, command],
        capture_output=True,
    )
    return completed.stdout.strip()


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
    values: dict[str, object] = {}
    try:
        parsed = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        sys.exit(f"ERROR: Cannot parse {path}: {e}")
    for name, value in parsed.items():
        if isinstance(value, str):
            values[name] = os.path.expandvars(value)
        elif isinstance(value, bool):
            values[name] = value
    return values


def script_dir() -> Path:
    return PROVISION_DIR


if __name__ == "__main__":
    sys.exit(main())
