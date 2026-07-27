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

configure_locale() {
  sudo sed -i 's/^# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen
  sudo locale-gen en_US.UTF-8
  sudo update-locale LANG=en_US.UTF-8 LC_CTYPE=en_US.UTF-8
}

home_disk() {
  local source
  local disk
  source=$(findmnt -n -o SOURCE --target "/home/$SHOW_USER" 2>/dev/null || true)
  if [[ -z "$source" ]]; then
    source=$(findmnt -n -o SOURCE --target /)
  fi
  disk=$(lsblk -no PKNAME "$source" 2>/dev/null | head -n1 || true)
  if [[ -n "$disk" ]]; then
    printf '/dev/%s\n' "$disk"
  else
    readlink -f "$source"
  fi
}

mounted_non_home_storage_exists() {
  local home
  local source
  local disk
  local target
  home=$(home_disk)
  while read -r source target; do
    disk=$(lsblk -no PKNAME "$source" 2>/dev/null | head -n1 || true)
    if [[ -z "$disk" ]]; then
      continue
    fi
    if [[ -n "$disk" ]]; then
      disk="/dev/$disk"
    fi
    if [[ -n "$disk" && "$disk" != "$home" ]]; then
      printf 'Found mounted non-home disk at %s: %s\n' "$target" "$source"
      return 0
    fi
  done < <(findmnt -rn -o SOURCE,TARGET)
  return 1
}

mount_name() {
  local device=$1
  local label=$2
  local name
  name=${label:-$(basename "$device")}
  name=$(printf '%s' "$name" | tr -cs '[:alnum:]._-' '_' | sed 's/^_*//;s/_*$//')
  if [[ -z "$name" ]]; then
    name=$(basename "$device")
  fi
  printf '%s\n' "$name"
}

fstab_options() {
  local fstype=$1
  local uid
  local gid
  uid=$(id -u "$SHOW_USER")
  gid=$(id -g "$SHOW_USER")
  case "$fstype" in
    exfat|vfat)
      printf 'defaults,nofail,x-systemd.device-timeout=10,uid=%s,gid=%s,umask=002\n' \
        "$uid" "$gid"
      ;;
    *)
      printf 'defaults,nofail,x-systemd.device-timeout=10\n'
      ;;
  esac
}

configure_storage_mounts() {
  local home
  local line
  local device
  local fstype
  local label
  local uuid
  local mountpoint
  local disk
  local name
  local target
  local options

  if mounted_non_home_storage_exists; then
    printf 'Leaving existing mounted non-home storage unchanged.\n'
    return
  fi

  printf 'No mounted non-home storage found. Looking for unmounted disks:\n'
  lsblk -f
  home=$(home_disk)
  while IFS= read -r line; do
    unset NAME FSTYPE LABEL UUID MOUNTPOINT
    eval "$line"
    device=${NAME:-}
    fstype=${FSTYPE:-}
    label=${LABEL:-}
    uuid=${UUID:-}
    mountpoint=${MOUNTPOINT:-}
    if [[ -z "$device" || -z "$fstype" || -z "$uuid" || -n "$mountpoint" ]]; then
      continue
    fi
    disk=$(lsblk -no PKNAME "$device" 2>/dev/null | head -n1 || true)
    if [[ -n "$disk" ]]; then
      disk="/dev/$disk"
    else
      disk=$(readlink -f "$device")
    fi
    if [[ "$disk" == "$home" ]]; then
      continue
    fi
    name=$(mount_name "$device" "$label")
    target="/mnt/$name"
    options=$(fstab_options "$fstype")
    sudo mkdir -p "$target"
    if ! grep -q "UUID=$uuid " /etc/fstab; then
      printf 'UUID=%s %s %s %s 0 2\n' "$uuid" "$target" "$fstype" "$options" \
        | sudo tee -a /etc/fstab >/dev/null
    fi
    sudo mount "$target"
    sudo chown "$SHOW_USER:$SHOW_USER" "$target" 2>/dev/null || true
    printf 'Mounted %s at %s\n' "$device" "$target"
  done < <(lsblk -Ppn -o NAME,FSTYPE,LABEL,UUID,MOUNTPOINT)
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
  local command="/home/$SHOW_USER/code/showco/.venv/bin/showco run $(showco_args)"

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

write_provisioning_report() {
  local report="/tmp/SHOWCO-PROVISIONING-REPORT.txt"
  {
    printf 'Showco provisioning report\n'
    date -Is
    printf '\nDisks discovered:\n'
    lsblk -f || true
    printf '\nMounted filesystems:\n'
    findmnt -rn -o SOURCE,TARGET,FSTYPE,OPTIONS || true
    printf '\nWi-Fi interfaces discovered:\n'
    if command -v nmcli >/dev/null 2>&1; then
      nmcli device status | awk '$2 == "wifi" {print}'
    else
      printf 'nmcli not installed\n'
    fi
    printf '\nWi-Fi device details:\n'
    if command -v iw >/dev/null 2>&1; then
      iw dev || true
    else
      printf 'iw not installed\n'
    fi
  } | tee "$report"
  sudo install -o "$SHOW_USER" -g "$SHOW_USER" -m 0644 \
    "$report" \
    "/home/$SHOW_USER/PROVISIONING-REPORT.txt"
  rm -f "$report"
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
    locales
    openssh-client
    python3
    python3-venv
    rsync
    sudo
    exfatprogs
  )
  printf 'Installing packages:\n'
  printf '  %s\n' "${packages[@]}"
  sudo apt-get update
  sudo apt-get install -y "${packages[@]}"

  phase "configuring locale"
  configure_locale

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

  phase "configuring storage mounts"
  configure_storage_mounts

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

  phase "writing provisioning report"
  write_provisioning_report

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
        is_x18_wired: bool,
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
        self.is_x18_wired = is_x18_wired
        self.showco_x18_host = showco_x18_host
        self.audio_x18_usb_device_name = audio_x18_usb_device_name
        self.password = password


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
        sys.exit("ERROR: gh is required to add the Pi SSH key to GitHub.")
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
        subprocess.run(
            ["gh", "ssh-key", "add", str(key_file), "--title", title],
            check=True,
        )
    finally:
        key_file.unlink(missing_ok=True)


def github_key_exists(public_key: str) -> bool:
    completed = subprocess.run(
        ["gh", "api", "user/keys", "--jq", ".[].key"],
        capture_output=True,
        check=True,
        text=True,
    )
    key = github_key_material(public_key)
    return any(
        github_key_material(line) == key for line in completed.stdout.splitlines()
    )


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
        password=string_value(env, "showco_pi_password"),
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


def capture_ssh(config: Config, target: str, command: str) -> str:
    completed = run(
        config,
        ["ssh", "-p", config.port, target, command],
        ["sshpass", "-e", "ssh", "-p", config.port, target, command],
        capture_output=True,
    )
    return completed.stdout.strip()


def run(
    config: Config,
    command: list[str],
    sshpass_command: list[str],
    *,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    env = None
    if config.password and config.password != "TODO":
        if not shutil.which("sshpass"):
            sys.exit("ERROR: showco_pi_password requires sshpass to be installed.")
        env = os.environ | {"SSHPASS": config.password}
        command = sshpass_command
    return subprocess.run(
        command,
        capture_output=capture_output,
        check=True,
        env=env,
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
