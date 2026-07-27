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

REMOTE_SCRIPT = r"""#!/usr/bin/env bash
set -euo pipefail

install_uv() {
  if sudo -H -u "$SHOW_USER" env PATH="/home/$SHOW_USER/.local/bin:$PATH" \
    bash -lc "command -v uv >/dev/null 2>&1"; then
    return
  fi
  curl -LsSf https://astral.sh/uv/install.sh | sudo -H -u "$SHOW_USER" sh
}

sync_repo() {
  local name=$1
  local url=$2
  local path="$CODE_DIR/$name"

  if [[ -d "$path/.git" ]]; then
    sudo -H -u "$SHOW_USER" git -C "$path" fetch --all --prune
    sudo -H -u "$SHOW_USER" git -C "$path" pull --ff-only
  else
    sudo -H -u "$SHOW_USER" git clone "$url" "$path"
  fi

  sudo -H -u "$SHOW_USER" env PATH="/home/$SHOW_USER/.local/bin:$PATH" \
    bash -lc "cd '$path' && uv sync"
}

showco_args() {
  local args=(
    --host 0.0.0.0
    --port "$SHOWCO_PORT"
  )
  if [[ -n "$SHOWCO_X18_HOST" && "$SHOWCO_X18_HOST" != TODO ]]; then
    args+=(
      --mixer-host "$SHOWCO_X18_HOST"
      --x18-host "$SHOWCO_X18_HOST"
      --x18-log-dir "/home/$SHOW_USER/recordings"
    )
  fi
  if [[ -f "/home/$SHOW_USER/.config/twitcho/config.json" ]]; then
    args+=(--twitcho-config "/home/$SHOW_USER/.config/twitcho/config.json")
  fi
  printf '%q ' "${args[@]}"
}

user_systemctl() {
  local uid
  uid=$(id -u "$SHOW_USER")
  sudo -H -u "$SHOW_USER" \
    env XDG_RUNTIME_DIR="/run/user/$uid" \
    systemctl --user "$@"
}

install_recs_service() {
  local quoted_args=
  local uid
  local args=()
  uid=$(id -u "$SHOW_USER")
  if [[ -n "$AUDIO_X18_USB_DEVICE_NAME" && "$AUDIO_X18_USB_DEVICE_NAME" != TODO ]]; then
    args+=(--include "$AUDIO_X18_USB_DEVICE_NAME")
  fi
  if [[ ${#args[@]} -gt 0 ]]; then
    quoted_args=$(printf '%q ' "${args[@]}")
  fi
  sudo -H -u "$SHOW_USER" \
    env XDG_RUNTIME_DIR="/run/user/$uid" \
    PATH="/home/$SHOW_USER/code/recs/.venv/bin:/home/$SHOW_USER/.local/bin:$PATH" \
    bash -lc "cd '$CODE_DIR/recs' && uv run recs daemon install $quoted_args"
}

install_showco_service() {
  local service_dir="/home/$SHOW_USER/.config/systemd/user"
  local service_file="$service_dir/showco.service"
  local command="/home/$SHOW_USER/code/showco/.venv/bin/showco $(showco_args)"

  sudo -H -u "$SHOW_USER" mkdir -p "$service_dir"
  sudo -H -u "$SHOW_USER" tee "$service_file" >/dev/null <<SERVICE
[Unit]
Description=showco local show control
After=default.target recs.service

[Service]
ExecStart=$command
Restart=always
RestartSec=5
WorkingDirectory=/home/$SHOW_USER/code/showco
StandardOutput=append:%h/.local/state/showco/showco.out.log
StandardError=append:%h/.local/state/showco/showco.err.log

[Install]
WantedBy=default.target
SERVICE
  user_systemctl daemon-reload
  user_systemctl enable showco.service
  user_systemctl restart showco.service
}

phase() {
  printf '\n==> %s\n' "$1"
}

main() {
  phase "checking user"
  id "$SHOW_USER" >/dev/null

  phase "installing base packages"
  packages=(
    alsa-utils
    ca-certificates
    curl
    ffmpeg
    git
    libegl1
    libportaudio2
    libsndfile1
    openssh-client
    python3
    python3-venv
    rsync
    sudo
  )
  printf 'Installing packages:\n'
  printf '  %s\n' "${packages[@]}"
  sudo apt-get update
  sudo apt-get install -y "${packages[@]}"

  phase "creating directories"
  sudo mkdir -p "$CODE_DIR"
  sudo chown "$SHOW_USER:$SHOW_USER" "$CODE_DIR"
  sudo -H -u "$SHOW_USER" mkdir -p \
    "/home/$SHOW_USER/.config/recs" \
    "/home/$SHOW_USER/.config/showco" \
    "/home/$SHOW_USER/.config/twitcho" \
    "/home/$SHOW_USER/.local/state/recs" \
    "/home/$SHOW_USER/.local/state/showco" \
    "/home/$SHOW_USER/.local/state/twitcho" \
    "/home/$SHOW_USER/recordings"

  phase "installing uv"
  install_uv

  phase "syncing repositories"
  sync_repo recs "$RECS_REPO"
  sync_repo twitcho "$TWITCHO_REPO"
  sync_repo showco "$SHOWCO_REPO"

  phase "enabling user service autostart"
  sudo loginctl enable-linger "$SHOW_USER"

  phase "installing recs service"
  install_recs_service

  phase "installing showco service"
  install_showco_service

  phase "writing next steps"
  cat >/tmp/PROVISIONING-NEXT-STEPS.txt <<'TEXT'
Provisioning completed.

Next manual steps:

1. Fill final twitcho config values if Twitch streaming is required.
2. Configure the final Pi access point.
3. Confirm the X18 USB device name.
4. Run the acceptance tests in showco/doc/acceptance-tests.md.
TEXT
  sudo install -o "$SHOW_USER" -g "$SHOW_USER" -m 0644 \
    /tmp/PROVISIONING-NEXT-STEPS.txt \
    "/home/$SHOW_USER/PROVISIONING-NEXT-STEPS.txt"
  rm -f /tmp/PROVISIONING-NEXT-STEPS.txt
}

main "$@"
"""


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
        showco_x18_host: str,
        audio_x18_usb_device_name: str,
        password: str,
    ) -> None:
        self.host = host
        self.user = user
        self.port = port
        self.recs_repo = recs_repo
        self.twitcho_repo = twitcho_repo
        self.showco_repo = showco_repo
        self.showco_port = showco_port
        self.showco_x18_host = showco_x18_host
        self.audio_x18_usb_device_name = audio_x18_usb_device_name
        self.password = password


def main() -> int:
    args = parse_args()
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
        print(f"Checking SSH connection to {ssh_target}...")
        run_ssh(
            config,
            ssh_target,
            "set -e; uname -a; id; command -v sudo; command -v apt-get",
        )

        print("Copying provisioning script...")
        run_scp(config, local_script, f"{ssh_target}:{remote_script}")

        print(f"Running provisioning on {ssh_target}...")
        run_ssh(config, ssh_target, remote_command(config, remote_script))
        run_ssh(config, ssh_target, f"rm -f {shlex.quote(remote_script)}")
    finally:
        local_script.unlink(missing_ok=True)

    print(f"Provisioned {ssh_target}.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Provision a reachable Raspberry Pi over SSH"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=script_dir() / "config.toml",
        help="default: scripts/config.toml",
    )
    parser.add_argument(
        "--secrets",
        type=Path,
        default=script_dir() / "secrets.toml",
        help="default: scripts/secrets.toml",
    )
    parser.add_argument("--host", help="default: SHOWCO_PI_HOST")
    parser.add_argument("--user", help="default: SHOWCO_PI_USER")
    parser.add_argument("--port", help="default: SHOWCO_PI_SSH_PORT")
    parser.add_argument("--recs-repo", help="default: RECS_REPO")
    parser.add_argument("--twitcho-repo", help="default: TWITCHO_REPO")
    parser.add_argument("--showco-repo", help="default: SHOWCO_REPO")
    return parser.parse_args()


def config_from_args(args: argparse.Namespace, env: dict[str, str]) -> Config:
    host = value_or_env(args.host, env, "SHOWCO_PI_HOST")
    user = value_or_env(args.user, env, "SHOWCO_PI_USER")
    port = value_or_env(args.port, env, "SHOWCO_PI_SSH_PORT", default="22")
    recs_repo = value_or_env(args.recs_repo, env, "RECS_REPO")
    twitcho_repo = value_or_env(args.twitcho_repo, env, "TWITCHO_REPO")
    showco_repo = value_or_env(args.showco_repo, env, "SHOWCO_REPO")
    showco_port = env.get("SHOWCO_PORT", "17352")

    return Config(
        host=require_value("SHOWCO_PI_HOST", host),
        user=require_value("SHOWCO_PI_USER", user),
        port=require_value("SHOWCO_PI_SSH_PORT", port),
        recs_repo=require_value("RECS_REPO", recs_repo),
        twitcho_repo=require_value("TWITCHO_REPO", twitcho_repo),
        showco_repo=require_value("SHOWCO_REPO", showco_repo),
        showco_port=require_value("SHOWCO_PORT", showco_port),
        showco_x18_host=env.get("SHOWCO_X18_WIRED_ETHERNET_IP_ADDRESS", ""),
        audio_x18_usb_device_name=env.get("AUDIO_X18_USB_DEVICE_NAME", ""),
        password=env.get("SHOWCO_PI_PASSWORD", ""),
    )


def value_or_env(
    value: str | None,
    env: dict[str, str],
    name: str,
    *,
    default: str = "",
) -> str:
    return value or env.get(name, default)


def require_value(name: str, value: str) -> str:
    if value and value != "TODO":
        return value
    sys.exit(f"ERROR: {name} is required")


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
        config,
        ["ssh", "-t", "-p", config.port, target, command],
        ["sshpass", "-e", "ssh", "-t", "-p", config.port, target, command],
    )


def run_scp(config: Config, source: Path, target: str) -> None:
    run(
        config,
        ["scp", "-P", config.port, str(source), target],
        ["sshpass", "-e", "scp", "-P", config.port, str(source), target],
    )


def run(config: Config, command: list[str], sshpass_command: list[str]) -> None:
    env = None
    if config.password and config.password != "TODO" and shutil.which("sshpass"):
        env = os.environ | {"SSHPASS": config.password}
        command = sshpass_command
    subprocess.run(command, check=True, env=env)


def read_toml(path: Path) -> dict[str, str]:
    path = path.expanduser()
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    try:
        parsed = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        sys.exit(f"ERROR: Cannot parse {path}: {e}")
    for name, value in parsed.items():
        if isinstance(value, str):
            values[name] = os.path.expandvars(value)
    return values


def script_dir() -> Path:
    return Path(__file__).resolve().parent


if __name__ == "__main__":
    sys.exit(main())
