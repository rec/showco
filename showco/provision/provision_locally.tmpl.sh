#!/usr/bin/env bash
set -euo pipefail

NETWORK_CHANGED=false

install_uv() {
  if sudo -H -u "$SHOW_USER" env PATH="/home/$SHOW_USER/.local/bin:$PATH" \
    bash -lc "uv --version >/dev/null 2>&1"; then
    return
  fi
  curl -LsSf https://astral.sh/uv/install.sh | sudo -H -u "$SHOW_USER" sh
}

configure_locale() {
  sudo sed -i 's/^# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen
  sudo locale-gen en_US.UTF-8
  sudo update-locale LANG=en_US.UTF-8 LC_CTYPE=en_US.UTF-8
  export LANG=en_US.UTF-8
  export LC_CTYPE=en_US.UTF-8
  unset LC_ALL
}

configure_journal() {
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
  local upstream
  local remote
  local branch

  prepare_checkout_path "$path"
  if [[ -d "$path/.git" ]]; then
    before=$(sudo -H -u "$SHOW_USER" git -C "$path" rev-parse HEAD)
    upstream=$(sudo -H -u "$SHOW_USER" git -C "$path" rev-parse \
      --abbrev-ref --symbolic-full-name '@{upstream}')
    remote=${upstream%%/*}
    branch=${upstream#*/}
    sudo -H -u "$SHOW_USER" git -C "$path" fetch "$remote" \
      "+refs/heads/$branch:refs/remotes/$remote/$branch"
    sudo -H -u "$SHOW_USER" git -C "$path" reset --hard "$remote/$branch"
  else
    sudo -H -u "$SHOW_USER" git clone "$url" "$path"
    changed=true
  fi
  if [[ -n "$refname" && "$refname" != TODO ]]; then
    sudo -H -u "$SHOW_USER" git -C "$path" checkout "$refname"
  fi

  after=$(sudo -H -u "$SHOW_USER" git -C "$path" rev-parse HEAD)
  if [[ "$before" != "$after" ]]; then
    changed=true
  fi
  if [[ "$changed" == true ]] || ! sudo -H -u "$SHOW_USER" \
    env PATH="/home/$SHOW_USER/.local/bin:$PATH" \
    bash -lc "cd '$path' && uv sync --frozen --check"; then
    sudo -H -u "$SHOW_USER" env PATH="/home/$SHOW_USER/.local/bin:$PATH" \
      bash -lc "cd '$path' && uv sync --frozen"
  else
    printf '%s environment is already synchronized.\n' "$name"
  fi
}

toml_string() {
  local value=$1
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  value=${value//$'\n'/\\n}
  printf '"%s"' "$value"
}

write_toml_string() {
  local name=$1
  local value=$2
  printf '%s = ' "$name"
  toml_string "$value"
  printf '\n'
}

write_toml_network_values() {
  local name=$1
  local ip_address=$2
  local subnet=$3
  if [ -n "$name" ]; then
    write_toml_string name "$name"
  fi
  if [ -n "$ip_address" ]; then
    write_toml_string ip_address "$ip_address"
  fi
  if [ -n "$subnet" ]; then
    write_toml_string subnet "$subnet"
  fi
}

write_toml_wifi_secret_values() {
  local password=$1
  write_toml_string password "$password"
}

write_network_config_files() {
  local config_file=$1
  local secrets_file=$2

  {
    printf '[network]\n'
    write_toml_string host "$SHOWCO_HOST"
    write_toml_string user "$SHOW_USER"
    printf 'web_port = %s\n' "$SHOWCO_PORT"
    printf 'swap_wifi = %s\n' "$SWAP_WIFI"
    write_toml_string topology "$NETWORK_TOPOLOGY"
    if [ "$X18" = true ]; then
      printf '\n[networks.internal.wired.x18]\n'
      write_toml_network_values x18 "$SHOWCO_X18_HOST" "$SHOWCO_PI_X18_SUBNET"
    fi
    printf '\n[networks.internal.wifi.private]\n'
    write_toml_network_values "$PRIVATE_WIFI_SSID" "" ""
    printf '\n[networks.external.wifi.external]\n'
    write_toml_network_values "$EXTERNAL_WIFI_SSID" "" ""
    printf '\n[twitch]\n'
    printf 'enabled = %s\n' "$TWITCHO_ENABLED"
    printf '\n[lyte]\n'
    printf 'enabled = %s\n' "$LYTE_ENABLED"
    write_toml_string daemon_config "$LYTE_DAEMON_CONFIG"
    printf '\n[git.reccy]\n'
    write_toml_string url "$RECCY_REPO"
    write_toml_string refname "$RECCY_REFNAME"
    printf '\n[git.recs]\n'
    write_toml_string url "$RECS_REPO"
    write_toml_string refname "$RECS_REFNAME"
    printf '\n[git.twitcho]\n'
    write_toml_string url "$TWITCHO_REPO"
    write_toml_string refname "$TWITCHO_REFNAME"
    printf '\n[git.lyte]\n'
    write_toml_string url "$LYTE_REPO"
    write_toml_string refname "$LYTE_REFNAME"
    printf '\n[git.showco]\n'
    write_toml_string url "$SHOWCO_REPO"
    write_toml_string refname "$SHOWCO_REFNAME"
  } >"$config_file"
  {
    printf '[networks.internal.wifi.private]\n'
    write_toml_wifi_secret_values "$PRIVATE_WIFI_PASSWORD"
    printf '\n[networks.external.wifi.external]\n'
    write_toml_wifi_secret_values "$EXTERNAL_WIFI_PASSWORD"
  } >"$secrets_file"
  sudo chown "$SHOW_USER:$SHOW_USER" "$config_file" "$secrets_file"
  sudo chmod 600 "$config_file" "$secrets_file"
}

configure_network() {
  local config_file
  local secrets_file
  local state_file="/home/$SHOW_USER/.config/showco/network-config.sha256"
  local configuration_hash
  local status

  if [[ -z "$EXTERNAL_WIFI_SSID" || "$EXTERNAL_WIFI_SSID" == TODO ]]; then
    printf 'Skipping network configuration: networks.external.wifi.external.name is not set.\n'
    return
  fi
  if [[ -z "$PRIVATE_WIFI_PASSWORD" || "$PRIVATE_WIFI_PASSWORD" == TODO ]]; then
    printf 'Skipping network configuration: networks.internal.wifi.private.password is not set.\n'
    return
  fi

  config_file=$(mktemp)
  secrets_file=$(mktemp)
  write_network_config_files "$config_file" "$secrets_file"
  configuration_hash=$(printf '%s\0' \
    "$NETWORK_TOPOLOGY" "$X18" "$SWAP_WIFI" "$SHOWCO_PI_X18_SUBNET" \
    "$SHOWCO_X18_HOST" "$PRIVATE_WIFI_SSID" "$PRIVATE_WIFI_PASSWORD" \
    "$EXTERNAL_WIFI_SSID" "$EXTERNAL_WIFI_PASSWORD" "$TWITCHO_ENABLED" \
    | sha256sum | awk '{print $1}')
  if [[ -f "$state_file" && "$(cat "$state_file")" == "$configuration_hash" ]]; then
    printf 'Network configuration is unchanged.\n'
    rm -f "$config_file" "$secrets_file"
    return
  fi
  set +e
  sudo -H -u "$SHOW_USER" env PATH="/home/$SHOW_USER/.local/bin:$PATH" \
    bash -lc \
      "cd '$ROOT/showco' && uv run --frozen showco run network-config --config '$config_file' --secrets '$secrets_file'"
  status=$?
  set -e
  rm -f "$config_file" "$secrets_file"
  if [[ "$status" -eq 0 ]]; then
    printf '%s\n' "$configuration_hash" \
      | sudo -H -u "$SHOW_USER" tee "$state_file" >/dev/null
    sudo chmod 600 "$state_file"
    NETWORK_CHANGED=true
  fi
  return "$status"
}

showco_args() {
  local args=(
    --host 0.0.0.0
    --port "$SHOWCO_PORT"
  )
  if [[ -n "$SHOWCO_X18_HOST" && "$SHOWCO_X18_HOST" != TODO ]]; then
    args+=(
      --mixer-host "$SHOWCO_X18_HOST"
      --mixer-port 10024
      --mixer-protocol udp
    )
  fi
  if [[ "$TWITCHO_ENABLED" == true ]]; then
    args+=(--twitcho-enabled)
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
  local osc_nodes=
  local uid
  local args=()
  local device_name
  local device_names
  uid=$(id -u "$SHOW_USER")
  if [[ -n "$X18_USB_DEVICE_NAME" && "$X18_USB_DEVICE_NAME" != TODO ]]; then
    IFS=/ read -r -a device_names <<<"$X18_USB_DEVICE_NAME"
    for device_name in "${device_names[@]}"; do
      args+=(--include "$device_name")
    done
  fi
  if [[ -n "$SHOWCO_X18_HOST" && "$SHOWCO_X18_HOST" != TODO ]]; then
    osc_nodes="/home/$SHOW_USER/.config/recs/x18.toml"
    sudo -H -u "$SHOW_USER" mkdir -p "${osc_nodes%/*}"
    sudo -H -u "$SHOW_USER" sh -c '
      printf "[[nodes]]\\nname = \\\"x18\\\"\\nhost = \\\"%s\\\"\\nport = 10024\\n\\n[[nodes.subscriptions]]\\npath = \\\"/xremote\\\"\\nresubscribe_period = 10\\n" "$1" > "$2"
    ' sh "$SHOWCO_X18_HOST" "$osc_nodes"
    args+=(--osc-nodes "$osc_nodes")
  fi
  if [[ ${#args[@]} -gt 0 ]]; then
    quoted_args=$(printf '%q ' "${args[@]}")
  fi
  sudo -H -u "$SHOW_USER" \
    env XDG_RUNTIME_DIR="/run/user/$uid" \
    PATH="$ROOT/recs/.venv/bin:/home/$SHOW_USER/.local/bin:$PATH" \
    bash -lc "cd '$ROOT/recs' && uv run --frozen recs daemon install $quoted_args"
}

install_showco_service() {
  local uid
  uid=$(id -u "$SHOW_USER")
  sudo -H -u "$SHOW_USER" \
    env XDG_RUNTIME_DIR="/run/user/$uid" \
    PATH="$ROOT/showco/.venv/bin:/home/$SHOW_USER/.local/bin:$PATH" \
    bash -lc "cd '$ROOT/showco' && uv run --frozen showco run install-service --root '$ROOT' $(showco_args)"
}

install_twitcho_service() {
  local config_path
  local quoted_config
  local uid
  if [[ "$TWITCHO_ENABLED" != true ]]; then
    printf 'Twitcho service is disabled.\n'
    return
  fi
  config_path="/home/$SHOW_USER/.config/twitcho/config.json"
  if [[ ! -f "$config_path" ]]; then
    printf 'ERROR: Twitcho configuration does not exist: %s\n' "$config_path" >&2
    return 1
  fi
  quoted_config=$(printf '%q' "$config_path")
  uid=$(id -u "$SHOW_USER")
  sudo -H -u "$SHOW_USER" \
    env XDG_RUNTIME_DIR="/run/user/$uid" \
    PATH="$ROOT/twitcho/.venv/bin:/home/$SHOW_USER/.local/bin:$PATH" \
    bash -lc "cd '$ROOT/twitcho' && uv run --frozen twitcho daemon install --config $quoted_config"
}

install_lyte_service() {
  local config_path
  local quoted_config
  local uid
  if [[ "$LYTE_ENABLED" != true ]]; then
    printf 'Lyte MIDI service is disabled.\n'
    return
  fi
  config_path="$ROOT/lyte/$LYTE_DAEMON_CONFIG"
  if [[ ! -f "$config_path" ]]; then
    printf 'ERROR: Lyte daemon configuration does not exist: %s\n' "$config_path" >&2
    return 1
  fi
  quoted_config=$(printf '%q' "$config_path")
  uid=$(id -u "$SHOW_USER")
  sudo -H -u "$SHOW_USER" \
    env XDG_RUNTIME_DIR="/run/user/$uid" \
    PATH="$ROOT/lyte/.venv/bin:/home/$SHOW_USER/.local/bin:$PATH" \
    bash -lc "cd '$ROOT/lyte' && uv run --frozen lyte daemon install --config $quoted_config"
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
    printf '\nLyte:\n'
    printf 'enabled: %s\n' "$LYTE_ENABLED"
    printf 'daemon config: %s\n' "$LYTE_DAEMON_CONFIG"
    if [[ -d "$ROOT/lyte/.git" ]]; then
      printf 'checkout: '
      git -C "$ROOT/lyte" rev-parse --short HEAD
    else
      printf 'checkout: missing\n'
    fi
    if [[ "$LYTE_ENABLED" == true ]]; then
      printf 'lyte service: '
      user_systemctl is-active lyte.service || true
    else
      printf 'lyte service: disabled\n'
    fi
    printf '\nTwitcho:\n'
    printf 'enabled: %s\n' "$TWITCHO_ENABLED"
    if [[ "$TWITCHO_ENABLED" == true ]]; then
      printf 'twitcho service: '
      user_systemctl is-active twitcho.service || true
    else
      printf 'twitcho service: disabled\n'
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

  phase "configuring locale"
  configure_locale

  phase "configuring persistent journal"
  configure_journal

  phase "installing base packages"
  packages=(
    alsa-utils
    ca-certificates
    curl
    emacs
    ffmpeg
    git
    libegl1
    libportaudio2
    libsndfile1
    locales
    network-manager
    openssh-client
    python3
    python3-venv
    rsync
    sudo
    tmux
    exfatprogs
  )
  printf 'Installing packages:\n'
  printf '  %s\n' "${packages[@]}"
  sudo apt-get update
  sudo apt-get upgrade -y
  sudo apt-get install -y "${packages[@]}"

  phase "creating directories"
  sudo mkdir -p "$ROOT"
  sudo chown "$SHOW_USER:$SHOW_USER" "$ROOT"
  sudo -H -u "$SHOW_USER" mkdir -p \
    "/home/$SHOW_USER/.config/recs" \
    "/home/$SHOW_USER/.config/showco" \
    "/home/$SHOW_USER/.config/twitcho" \
    "/home/$SHOW_USER/.config/lyte" \
    "/home/$SHOW_USER/.local/state/recs" \
    "/home/$SHOW_USER/.local/state/showco" \
    "/home/$SHOW_USER/.local/state/twitcho" \
    "/home/$SHOW_USER/.local/state/lyte" \
    "/home/$SHOW_USER/recordings"
  printf 'target\n' | sudo -H -u "$SHOW_USER" tee \
    "/home/$SHOW_USER/.config/showco/machine-role" >/dev/null

  phase "configuring storage mounts"
  configure_storage_mounts

  phase "installing uv"
  install_uv

  phase "installing Lyte Python"
  sudo -H -u "$SHOW_USER" env PATH="/home/$SHOW_USER/.local/bin:$PATH" \
    bash -lc "uv python install 3.13"

  phase "syncing repositories"
  sync_repo reccy "$RECCY_REPO" "$RECCY_REFNAME"
  sync_repo recs "$RECS_REPO" "$RECS_REFNAME"
  sync_repo twitcho "$TWITCHO_REPO" "$TWITCHO_REFNAME"
  sync_repo lyte "$LYTE_REPO" "$LYTE_REFNAME"
  sync_repo showco "$SHOWCO_REPO" "$SHOWCO_REFNAME"

  phase "enabling user service autostart"
  sudo loginctl enable-linger "$SHOW_USER"

  phase "installing recs service"
  install_recs_service

  phase "installing Twitcho service"
  install_twitcho_service

  phase "installing showco service"
  install_showco_service

  phase "installing Lyte MIDI service"
  install_lyte_service

  phase "writing provisioning report"
  write_provisioning_report

  phase "writing next steps"
  cat >/tmp/PROVISIONING-NEXT-STEPS.txt <<'TEXT'
Provisioning completed.

Next manual steps:

1. Fill final twitcho config values if Twitch streaming is required.
2. Configure and enable Lyte if lighting control is required.
3. Fill Wi-Fi password values and rerun provisioning if network configuration was skipped.
4. Confirm the X18 USB device name.
5. Run the acceptance tests in showco/doc/acceptance-tests.md.
TEXT
  sudo install -o "$SHOW_USER" -g "$SHOW_USER" -m 0644 \
    /tmp/PROVISIONING-NEXT-STEPS.txt \
    "/home/$SHOW_USER/PROVISIONING-NEXT-STEPS.txt"
  rm -f /tmp/PROVISIONING-NEXT-STEPS.txt

  phase "configuring network"
  configure_network

  phase "rebooting"
  if [[ "$NETWORK_CHANGED" == true || -f /var/run/reboot-required ]]; then
    sudo touch /run/showco-provision-reboot-required
    printf 'Reboot required.\n'
  else
    printf 'Reboot not required.\n'
  fi
}

main "$@"
