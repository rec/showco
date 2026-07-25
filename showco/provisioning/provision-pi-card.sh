#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
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
  --boot PATH                  mounted Raspberry Pi boot partition
  --disk DISK                  imaged Raspberry Pi SD card disk, mounted if needed
  --ssh-key-file PATH          public SSH key to install for the show user
  --password-hash HASH         Linux password hash for the show user
  --wifi-ssid SSID             temporary first-boot Wi-Fi SSID
  --wifi-password PASSWORD     temporary first-boot Wi-Fi password
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

require_value() {
  local name=$1
  local value=$2
  [[ -n "$value" ]] || die "$name is required"
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

data = plistlib.load(sys.stdin.buffer)
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

data = plistlib.load(sys.stdin.buffer)
print(data.get("MountPoint") or "")
'
}

is_raspberry_pi_boot() {
  local path=$1
  [[ -f "$path/config.txt" && -f "$path/cmdline.txt" ]] || return 1
}

find_boot_on_disk() {
  local disk=$1
  local partitions=()
  local line
  while IFS= read -r line; do
    partitions+=("$line")
  done < <(disk_partitions "$disk")
  [[ ${#partitions[@]} -gt 0 ]] || die "Cannot find partitions on $disk"

  local matches=()
  local partition mount
  for partition in "${partitions[@]}"; do
    diskutil mount "$partition" >/dev/null 2>&1 || true
    mount=$(mount_point "$partition")
    if [[ -n "$mount" ]] && is_raspberry_pi_boot "$mount"; then
      matches+=("$mount")
    fi
  done

  if [[ ${#matches[@]} -eq 0 ]]; then
    die "Could not find a Raspberry Pi boot partition on $disk"
  fi
  if [[ ${#matches[@]} -gt 1 ]]; then
    die "Found multiple Raspberry Pi boot partitions on $disk: ${matches[*]}"
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

boot=
disk=
ssh_key_file=
password_hash=
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

if [[ -n "$boot" && -n "$disk" ]]; then
  die "Use --boot or --disk, not both"
fi
if [[ -n "$disk" ]]; then
  boot=$(find_boot_on_disk "$disk")
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
