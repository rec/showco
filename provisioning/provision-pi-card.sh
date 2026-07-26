#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  showco/provisioning/provision-pi-card.sh

  showco/provisioning/provision-pi-card.sh \
    --boot /Volumes/bootfs \
    --ssh-key-file ~/.ssh/id_ed25519.pub \
    --password-hash '$y$j9T$...' \
    --wifi-ssid NAME \
    --wifi-password PASSWORD

  showco/provisioning/provision-pi-card.sh \
    --disk /dev/disk4 \
    --ssh-key-file ~/.ssh/id_ed25519.pub \
    --password-hash '$y$j9T$...' \
    --wifi-ssid NAME \
    --wifi-password PASSWORD

Options:
  --config PATH                default: doc/config.env
  --secrets PATH               default: doc/secrets.env
  --boot PATH                  mounted Raspberry Pi boot partition
  --disk DISK                  imaged Raspberry Pi SD card disk, mounted if needed
  --ssh-key-file PATH          public SSH key to install for the show user
  --password-hash HASH         Linux password hash for the show user, default: locked password login
  --wifi-ssid SSID             temporary first-boot Wi-Fi SSID, default: SHOWCO_PI_ACCESS_POINT_SSID
  --wifi-password PASSWORD     temporary first-boot Wi-Fi password, default: SHOWCO_PI_ACCESS_POINT_PASSWORD
  --hostname NAME              default: recs-stage
  --user NAME                  default: show
  --eth-address CIDR           default: 10.43.0.1/24
  --recs-repo URL              default: https://github.com/rec/recs.git
  --twitcho-repo URL           default: https://github.com/rec/twitcho.git
  --showco-repo URL            default: https://github.com/rec/showco.git
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

script_dir() {
  local source=${BASH_SOURCE[0]}
  while [[ -L "$source" ]]; do
    source=$(readlink "$source")
  done
  cd "$(dirname "$source")" >/dev/null
  pwd
}

repo_root() {
  cd "$(script_dir)/.." >/dev/null
  pwd
}

expand_path() {
  case "$1" in
    "~/"*) printf '%s/%s\n' "$HOME" "${1#"~/"}" ;;
    *) printf '%s\n' "$1" ;;
  esac
}

require_value() {
  local name=$1
  local value=$2
  [[ -n "$value" ]] || die "$name is required"
}

env_value() {
  local file=$1
  local name=$2
  [[ -f "$file" ]] || return 0

  bash -c 'source "$1"; printf "%s\n" "${!2-}"' bash "$file" "$name"
}

use_default() {
  local name=$1
  local value=$2
  if [[ -z "${!name}" && -n "$value" && "$value" != TODO ]]; then
    printf -v "$name" '%s' "$value"
  fi
}

copy_template() {
  local source=$1
  local target=$2
  python3 - "$source" "$target" <<'PY'
import os
import sys
from pathlib import Path

source = Path(sys.argv[1])
target = Path(sys.argv[2])
text = source.read_text()
for key, value in os.environ.items():
    text = text.replace(f"@{key}@", value)
target.write_text(text)
PY
}

disk_partitions() {
  local disk=$1
  diskutil list -plist "$disk" | python3 -c '
import plistlib
import sys

data = plistlib.loads(sys.stdin.buffer.read())
for disk in data.get("AllDisksAndPartitions", []):
    for partition in disk.get("Partitions", []):
        if identifier := partition.get("DeviceIdentifier"):
            print(f"/dev/{identifier}")
'
}

mount_point() {
  local device=$1
  diskutil info -plist "$device" | python3 -c '
import plistlib
import sys

data = plistlib.loads(sys.stdin.buffer.read())
print(data.get("MountPoint") or "")
'
}

is_raspberry_pi_boot() {
  local path=$1
  [[ -f "$path/config.txt" && -f "$path/cmdline.txt" ]] || return 1
}

mounted_boot_matches() {
  local volume
  for volume in /Volumes/bootfs /Volumes/boot /Volumes/BOOT; do
    if [[ -d "$volume" ]] && is_raspberry_pi_boot "$volume"; then
      printf '%s\n' "$volume"
    fi
  done
}

boot_matches_on_disk() {
  local disk=$1
  local partitions=()
  local line
  while IFS= read -r line; do
    partitions+=("$line")
  done < <(disk_partitions "$disk")
  [[ ${#partitions[@]} -gt 0 ]] || return 0

  local partition mount
  for partition in "${partitions[@]}"; do
    diskutil mount "$partition" >/dev/null 2>&1 || true
    mount=$(mount_point "$partition")
    if [[ -n "$mount" ]] && is_raspberry_pi_boot "$mount"; then
      printf '%s\n' "$mount"
    fi
  done
}

find_boot_on_disk() {
  local disk=$1
  local matches=()
  local line
  while IFS= read -r line; do
    matches+=("$line")
  done < <(boot_matches_on_disk "$disk")

  if [[ ${#matches[@]} -eq 0 ]]; then
    die "Could not find a Raspberry Pi boot partition on $disk"
  fi
  if [[ ${#matches[@]} -gt 1 ]]; then
    die "Found multiple Raspberry Pi boot partitions on $disk: ${matches[*]}"
  fi
  printf '%s\n' "${matches[0]}"
}

all_external_disks() {
  command -v diskutil >/dev/null || die "diskutil is required to auto-detect the Raspberry Pi boot partition"
  local disk
  while IFS= read -r disk; do
    if diskutil info -plist "$disk" | python3 -c '
import plistlib
import sys

data = plistlib.loads(sys.stdin.buffer.read())
if not data.get("Internal", True):
    print("external")
' | grep -q external; then
      printf '%s\n' "$disk"
    fi
  done < <(diskutil list -plist | python3 -c '
import plistlib
import sys

data = plistlib.loads(sys.stdin.buffer.read())
for identifier in data.get("WholeDisks", []):
    print(f"/dev/{identifier}")
')
}

find_boot_automatically() {
  local matches=()
  local disk match
  while IFS= read -r match; do
    matches+=("$match")
  done < <(mounted_boot_matches)

  if [[ ${#matches[@]} -eq 1 ]]; then
    printf '%s\n' "${matches[0]}"
    return
  fi
  if [[ ${#matches[@]} -gt 1 ]]; then
    die "Found multiple Raspberry Pi boot partitions: ${matches[*]}. Use --boot."
  fi

  while IFS= read -r disk; do
    while IFS= read -r match; do
      matches+=("$match")
    done < <(boot_matches_on_disk "$disk")
  done < <(all_external_disks)

  if [[ ${#matches[@]} -eq 0 ]]; then
    die "Could not auto-detect a Raspberry Pi boot partition. Use --boot or --disk."
  fi
  if [[ ${#matches[@]} -gt 1 ]]; then
    die "Found multiple Raspberry Pi boot partitions: ${matches[*]}. Use --boot."
  fi
  printf '%s\n' "${matches[0]}"
}

validate_boot() {
  local path=$1
  [[ -d "$path" ]] || die "$path does not exist or is not a directory"
  if ! is_raspberry_pi_boot "$path"; then
    die "$path is not a recognized Raspberry Pi boot partition"
  fi
}

config_file=$(repo_root)/doc/config.env
secrets_file=$(repo_root)/doc/secrets.env
boot=
disk=
ssh_key_file=~/.ssh/id_ed25519.pub
password_hash='*'
wifi_ssid=
wifi_password=
hostname=recs-stage
show_user=show
eth_address=10.43.0.1/24
recs_repo=https://github.com/rec/recs.git
twitcho_repo=https://github.com/rec/twitcho.git
showco_repo=https://github.com/rec/showco.git

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      config_file=$2
      shift 2
      ;;
    --secrets)
      secrets_file=$2
      shift 2
      ;;
    --boot)
      boot=$2
      shift 2
      ;;
    --disk)
      disk=$2
      shift 2
      ;;
    --ssh-key-file)
      ssh_key_file=$2
      shift 2
      ;;
    --password-hash)
      password_hash=$2
      shift 2
      ;;
    --wifi-ssid)
      wifi_ssid=$2
      shift 2
      ;;
    --wifi-password)
      wifi_password=$2
      shift 2
      ;;
    --hostname)
      hostname=$2
      shift 2
      ;;
    --user)
      show_user=$2
      shift 2
      ;;
    --eth-address)
      eth_address=$2
      shift 2
      ;;
    --recs-repo)
      recs_repo=$2
      shift 2
      ;;
    --twitcho-repo)
      twitcho_repo=$2
      shift 2
      ;;
    --showco-repo)
      showco_repo=$2
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
done

config_file=$(expand_path "$config_file")
secrets_file=$(expand_path "$secrets_file")
ssh_key_file=$(expand_path "$ssh_key_file")

use_default wifi_ssid "$(env_value "$config_file" SHOWCO_PI_ACCESS_POINT_SSID)"
use_default wifi_password "$(env_value "$secrets_file" SHOWCO_PI_ACCESS_POINT_PASSWORD)"

if [[ -n "$boot" && -n "$disk" ]]; then
  die "Use --boot or --disk, not both"
fi
if [[ -n "$disk" ]]; then
  boot=$(find_boot_on_disk "$disk")
fi
if [[ -z "$boot" ]]; then
  boot=$(find_boot_automatically)
fi
require_value --boot "$boot"
require_value --ssh-key-file "$ssh_key_file"
require_value --password-hash "$password_hash"
require_value --wifi-ssid "$wifi_ssid"
require_value --wifi-password "$wifi_password"

validate_boot "$boot"
[[ -f "$ssh_key_file" ]] || die "$ssh_key_file does not exist"

provisioning_dir=$(script_dir)
ssh_key=$(<"$ssh_key_file")

export HOSTNAME=$hostname
export SHOW_USER=$show_user
export PASSWORD_HASH=$password_hash
export SSH_PUBLIC_KEY=$ssh_key
export WIFI_SSID=$wifi_ssid
export WIFI_PASSWORD=$wifi_password
export ETH_ADDRESS=$eth_address
export RECS_REPO=$recs_repo
export TWITCHO_REPO=$twitcho_repo
export SHOWCO_REPO=$showco_repo

copy_template "$provisioning_dir/user-data.yml" "$boot/user-data"
copy_template "$provisioning_dir/network-config.yml" "$boot/network-config"
copy_template "$provisioning_dir/meta-data.yml" "$boot/meta-data"
cp "$provisioning_dir/pi-first-boot.sh" "$boot/pi-first-boot.sh"
chmod +x "$boot/pi-first-boot.sh"

echo "Provisioned $boot for $hostname."
