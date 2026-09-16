#!/usr/bin/env bash
set -euo pipefail

PHASE_STARTED_SECONDS=0
UPGRADE_PACKAGES=${UPGRADE_PACKAGES:-false}
ARGON_ONE=${ARGON_ONE:-true}

install_base_packages() {
  local state_file="/var/lib/showco/system-packages.sha256"
  local desired_hash
  local installed_hash=
  local package
  local missing=()

  desired_hash=$(printf '%s\n' "$@" | sha256sum | awk '{print $1}')
  if [[ -f "$state_file" ]]; then
    installed_hash=$(sudo cat "$state_file")
  fi
  for package in "$@"; do
    if ! dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null \
      | grep -Fx installed >/dev/null; then
      missing+=("$package")
    fi
  done
  if [[ "$UPGRADE_PACKAGES" != true && "$desired_hash" == "$installed_hash" \
    && ${#missing[@]} -eq 0 ]]; then
    printf 'Base packages are already installed.\n'
    return
  fi

  sudo apt-get update
  if [[ "$UPGRADE_PACKAGES" == true ]]; then
    sudo apt-get upgrade -y
  fi
  if [[ ${#missing[@]} -gt 0 ]]; then
    sudo apt-get install -y "${missing[@]}"
  fi
  sudo install -d -m 0755 /var/lib/showco
  printf '%s\n' "$desired_hash" | sudo tee "$state_file" >/dev/null
}

install_argon_one() {
  if sudo test -f /etc/argon/argononed.py \
    && sudo systemctl is-enabled --quiet argononed.service; then
    printf 'Argon ONE software is already installed.\n'
    return
  fi
  curl --fail --location --silent --show-error https://download.argon40.com/argon1.sh \
    | bash
}

install_uv() {
  if sudo -H -u "$SHOW_USER" env PATH="/home/$SHOW_USER/.local/bin:$PATH" \
    bash -lc "uv --version >/dev/null 2>&1"; then
    return
  fi
  curl -LsSf https://astral.sh/uv/install.sh | sudo -H -u "$SHOW_USER" sh
}

install_python() {
  if sudo -H -u "$SHOW_USER" env PATH="/home/$SHOW_USER/.local/bin:$PATH" \
    bash -lc "uv python find 3.13 >/dev/null 2>&1"; then
    printf 'Python 3.13 is already installed.\n'
    return
  fi
  sudo -H -u "$SHOW_USER" env PATH="/home/$SHOW_USER/.local/bin:$PATH" \
    bash -lc "uv python install 3.13"
}

configure_locale() {
  if locale -a | grep -E -x 'en_US\.utf-?8' >/dev/null \
    && grep -F -x 'LANG=en_US.UTF-8' /etc/default/locale >/dev/null \
    && grep -F -x 'LC_CTYPE=en_US.UTF-8' /etc/default/locale >/dev/null; then
    export LANG=en_US.UTF-8
    export LC_CTYPE=en_US.UTF-8
    unset LC_ALL
    return
  fi
  sudo sed -i 's/^# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen
  sudo locale-gen en_US.UTF-8
  sudo update-locale LANG=en_US.UTF-8 LC_CTYPE=en_US.UTF-8
  export LANG=en_US.UTF-8
  export LC_CTYPE=en_US.UTF-8
  unset LC_ALL
}

configure_journal() {
  if sudo test -f /etc/systemd/journald.conf.d/showco.conf \
    && sudo grep -F -x '[Journal]' /etc/systemd/journald.conf.d/showco.conf >/dev/null \
    && sudo grep -F -x 'Storage=persistent' /etc/systemd/journald.conf.d/showco.conf >/dev/null \
    && sudo test -d /var/log/journal; then
    return
  fi
  sudo install -d -m 2755 /etc/systemd/journald.conf.d /var/log/journal
  printf '[Journal]\nStorage=persistent\n' \
    | sudo tee /etc/systemd/journald.conf.d/showco.conf >/dev/null
  sudo systemctl restart systemd-journald
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

fstab_mountpoint_for_uuid() {
  local uuid=$1
  awk -v source="UUID=$uuid" '$1 == source {print $2; exit}' /etc/fstab
}

mountpoint_is_in_fstab() {
  local target=$1
  awk -v target="$target" '$2 == target {found=1} END {exit !found}' /etc/fstab
}

mount_target_for_disk() {
  local uuid=$1
  local name=$2
  local existing
  local target
  local suffix

  existing=$(fstab_mountpoint_for_uuid "$uuid")
  if [[ -n "$existing" ]]; then
    printf '%s\n' "$existing"
    return
  fi

  target="/mnt/$name"
  suffix=2
  while mountpoint_is_in_fstab "$target"; do
    target="/mnt/$name-$suffix"
    suffix=$((suffix + 1))
  done
  printf '%s\n' "$target"
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
    target=$(mount_target_for_disk "$uuid" "$name")
    options=$(fstab_options "$fstype")
    sudo mkdir -p "$target"
    if [[ -z "$(fstab_mountpoint_for_uuid "$uuid")" ]]; then
      printf 'UUID=%s %s %s %s 0 2\n' "$uuid" "$target" "$fstype" "$options" \
        | sudo tee -a /etc/fstab >/dev/null
    fi
    sudo mount "$target"
    sudo chown "$SHOW_USER:$SHOW_USER" "$target" 2>/dev/null || true
    printf 'Mounted %s at %s\n' "$device" "$target"
  done < <(lsblk -Ppn -o NAME,FSTYPE,LABEL,UUID,MOUNTPOINT)
}

prepare_checkout_path() {
  local path=$1
  local backup

  if [[ ! -e "$path" || -d "$path/.git" ]]; then
    return
  fi

  backup="$path.broken.$(date +%Y%m%dT%H%M%S)"
  while [[ -e "$backup" ]]; do
    backup="$backup.$RANDOM"
  done
  printf 'Moving non-git checkout aside: %s -> %s\n' "$path" "$backup"
  sudo mv "$path" "$backup"
}

sync_repo() {
  local name=$1
  local url=$2
  local refname=$3
  local path="$ROOT/$name"
  local before=
  local after
  local changed=false

  prepare_checkout_path "$path"
  if [[ -d "$path/.git" ]]; then
    before=$(sudo -H -u "$SHOW_USER" git -C "$path" rev-parse HEAD)
    sudo -H -u "$SHOW_USER" git -C "$path" remote set-url origin "$url"
  else
    sudo -H -u "$SHOW_USER" git clone "$url" "$path"
    changed=true
  fi
  if [[ -n "$refname" && "$refname" != TODO ]]; then
    sudo -H -u "$SHOW_USER" git -C "$path" fetch origin "$refname"
    sudo -H -u "$SHOW_USER" git -C "$path" checkout --detach FETCH_HEAD
  else
    sudo -H -u "$SHOW_USER" git -C "$path" fetch origin \
      "+refs/heads/main:refs/remotes/origin/main"
    sudo -H -u "$SHOW_USER" git -C "$path" checkout --force main
    sudo -H -u "$SHOW_USER" git -C "$path" reset --hard origin/main
  fi

  after=$(sudo -H -u "$SHOW_USER" git -C "$path" rev-parse HEAD)
  if [[ "$before" != "$after" ]]; then
    changed=true
  fi
  if [[ "$changed" == true ]] || ! sudo -H -u "$SHOW_USER" \
    env PATH="/home/$SHOW_USER/.local/bin:$PATH" \
    bash -lc "cd '$path' && uv sync --locked --check"; then
    sudo -H -u "$SHOW_USER" env PATH="/home/$SHOW_USER/.local/bin:$PATH" \
      bash -lc "cd '$path' && uv sync --locked"
  else
    printf '%s environment is already synchronized.\n' "$name"
  fi
}
