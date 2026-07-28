#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

REMOTE_SCRIPT_TEMPLATE = "provision_locally.tmpl.sh"
REMOTE_SCRIPT = (Path(__file__).resolve().parent / REMOTE_SCRIPT_TEMPLATE).read_text()


class Config:
    def __init__(
        self,
        *,
        host: str,
        user: str,
        port: str,
        recs_repo: str,
        twitcho_repo: str,
        showco_repo: str,
        showco_port: str,
        is_x18_wired: bool,
        showco_x18_host: str,
        audio_x18_usb_device_name: str,
    ) -> None:
        self.host = host
        self.user = user
        self.port = port
        self.recs_repo = recs_repo
        self.twitcho_repo = twitcho_repo
        self.showco_repo = showco_repo
        self.showco_port = showco_port
        self.is_x18_wired = is_x18_wired
        self.showco_x18_host = showco_x18_host
        self.audio_x18_usb_device_name = audio_x18_usb_device_name


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    env = read_toml(args.config)
    env |= read_toml(args.secrets)
    config = config_from_args(args, env)
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
        subprocess.run(
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
        completed = subprocess.run(
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
    return "\n".join(
        [
            "set -e",
            "{",
            "if ! command -v ssh-keygen >/dev/null 2>&1 || "
            "! command -v ssh-keyscan >/dev/null 2>&1; then",
            "  sudo apt-get update",
            "  sudo apt-get install -y openssh-client",
            "fi",
            'mkdir -p "$HOME/.ssh"',
            'chmod 700 "$HOME/.ssh"',
            'if [ ! -f "$HOME/.ssh/id_ed25519" ]; then',
            "  ssh-keygen -t ed25519 -N '' "
            f'-C {comment} -f "$HOME/.ssh/id_ed25519" >/dev/null',
            "fi",
            'chmod 600 "$HOME/.ssh/id_ed25519"',
            'if [ ! -f "$HOME/.ssh/id_ed25519.pub" ]; then',
            '  ssh-keygen -y -f "$HOME/.ssh/id_ed25519" > "$HOME/.ssh/id_ed25519.pub"',
            "fi",
            'touch "$HOME/.ssh/known_hosts"',
            'chmod 600 "$HOME/.ssh/known_hosts"',
            'ssh-keygen -F github.com -f "$HOME/.ssh/known_hosts" >/dev/null 2>&1 '
            '|| ssh-keyscan github.com >> "$HOME/.ssh/known_hosts"',
            "} >&2",
            'cat "$HOME/.ssh/id_ed25519.pub"',
        ]
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Provision a reachable Raspberry Pi over SSH"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=script_dir() / "config.toml",
        help="default: showco/provision/config.toml",
    )
    parser.add_argument(
        "--secrets",
        type=Path,
        default=script_dir() / "secrets.toml",
        help="default: showco/provision/secrets.toml",
    )
    parser.add_argument("--host", help="default: showco_pi_host")
    parser.add_argument("--user", help="default: showco_pi_user, then USER")
    parser.add_argument("--port", help="default: showco_pi_ssh_port")
    parser.add_argument("--recs-repo", help="default: recs_repo")
    parser.add_argument("--twitcho-repo", help="default: twitcho_repo")
    parser.add_argument("--showco-repo", help="default: showco_repo")
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace, env: dict[str, object]) -> Config:
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
        audio_x18_usb_device_name=string_value(env, "audio_x18_usb_device_name"),
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
        "AUDIO_X18_USB_DEVICE_NAME": config.audio_x18_usb_device_name,
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
    return subprocess.run(
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
    return Path(__file__).resolve().parent


if __name__ == "__main__":
    sys.exit(main())
