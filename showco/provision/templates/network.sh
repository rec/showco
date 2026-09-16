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
    printf '\n[stream]\n'
    printf 'enabled = %s\n' "$STREAMO_ENABLED"
    printf '\n[lyte]\n'
    printf 'enabled = %s\n' "$LYTE_ENABLED"
    write_toml_string installation_config "$LYTE_INSTALLATION_CONFIG"
    printf '\n[git.reccy]\n'
    write_toml_string url "$RECCY_REPO"
    write_toml_string refname "$RECCY_REFNAME"
    printf '\n[git.recs]\n'
    write_toml_string url "$RECS_REPO"
    write_toml_string refname "$RECS_REFNAME"
    printf '\n[git.streamo]\n'
    write_toml_string url "$STREAMO_REPO"
    write_toml_string refname "$STREAMO_REFNAME"
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
    "$EXTERNAL_WIFI_SSID" "$EXTERNAL_WIFI_PASSWORD" "$STREAMO_ENABLED" \
    | sha256sum | awk '{print $1}')
  if [[ -f "$state_file" && "$(cat "$state_file")" == "$configuration_hash" ]]; then
    printf 'Network configuration is unchanged.\n'
    rm -f "$config_file" "$secrets_file"
    return
  fi
  set +e
  sudo -H -u "$SHOW_USER" env PATH="/home/$SHOW_USER/.local/bin:$PATH" \
    bash -lc \
      "cd '$ROOT/showco' && uv run --locked showco run network-config --config '$config_file' --secrets '$secrets_file'"
  status=$?
  set -e
  rm -f "$config_file" "$secrets_file"
  if [[ "$status" -eq 0 ]]; then
    printf '%s\n' "$configuration_hash" \
      | sudo -H -u "$SHOW_USER" tee "$state_file" >/dev/null
    sudo chmod 600 "$state_file"
  fi
  return "$status"
}
