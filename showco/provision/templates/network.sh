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
    printf 'ssh_port = %s\n' "$SHOWCO_SSH_PORT"
    printf 'swap_wifi = %s\n' "$SWAP_WIFI"
    printf 'restrict_external_ingress = %s\n' "$RESTRICT_EXTERNAL_INGRESS"
    write_toml_string topology "$NETWORK_TOPOLOGY"
    printf '\n[networks.internal]\n'
    write_toml_string subnet "$SHOWCO_PI_X18_SUBNET"
    printf '\n[networks.internal.wifi]\n'
    write_toml_string name "$PRIVATE_WIFI_SSID"
    if [ -n "$PRIVATE_WIFI_IP_OFFSET" ]; then
      printf 'ip_address = %s\n' "$PRIVATE_WIFI_IP_OFFSET"
    fi
    printf '\n%s\n' "$EXTERNAL_WIFI_CONFIG"
    if [ "$X18" = true ]; then
      printf '\n[[mixers]]\n'
      write_toml_string name X18
      printf 'ip_address = %s\n' "$SHOWCO_X18_IP_OFFSET"
      printf 'port = %s\n' "$SHOWCO_X18_PORT"
    fi
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
    printf '[networks.internal.wifi]\n'
    write_toml_wifi_secret_values "$PRIVATE_WIFI_PASSWORD"
    printf '\n%s\n' "$EXTERNAL_WIFI_SECRETS"
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

  if [[ -z "$EXTERNAL_WIFI_CONFIG" ]]; then
    printf 'Skipping network configuration: networks.external.wifi is empty.\n'
    return
  fi
  if [[ -z "$PRIVATE_WIFI_PASSWORD" || "$PRIVATE_WIFI_PASSWORD" == TODO ]]; then
    printf 'Skipping network configuration: networks.internal.wifi.password is not set.\n'
    return
  fi

  config_file=$(mktemp)
  secrets_file=$(mktemp)
  write_network_config_files "$config_file" "$secrets_file"
  configuration_hash=$(printf '%s\0' \
    "$NETWORK_TOPOLOGY" "$X18" "$SWAP_WIFI" "$RESTRICT_EXTERNAL_INGRESS" \
    "$SHOWCO_SSH_PORT" "$SHOWCO_PI_X18_SUBNET" \
    "$SHOWCO_X18_HOST" "$SHOWCO_X18_PORT" "$PRIVATE_WIFI_IP_OFFSET" \
    "$PRIVATE_WIFI_SSID" "$PRIVATE_WIFI_PASSWORD" \
    "$EXTERNAL_WIFI_CONFIG" "$EXTERNAL_WIFI_SECRETS" "$STREAMO_ENABLED" \
    | sha256sum | awk '{print $1}')
  if [[ -f "$state_file" && "$(cat "$state_file")" == "$configuration_hash" ]]; then
    printf 'Network configuration is unchanged.\n'
    rm -f "$config_file" "$secrets_file"
    return
  fi
  set +e
  sudo -H -u "$SHOW_USER" env PATH="/home/$SHOW_USER/.local/bin:$PATH" \
    bash -lc \
      "cd '$ROOT/showco' && uv run --locked showco run network-config --config '$config_file' --secrets '$secrets_file' --ssh-peer '${SSH_CLIENT%% *}'"
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

configure_ingress_firewall() {
  local unit=/etc/systemd/system/showco-ingress.service
  local rules=/etc/showco/ingress.nft
  local private_interface
  local temporary

  if [[ "$RESTRICT_EXTERNAL_INGRESS" != true ]]; then
    if sudo test -f "$unit"; then
      sudo systemctl disable --now showco-ingress.service
    fi
    return
  fi
  if [[ "$NETWORK_TOPOLOGY" == public ]]; then
    printf 'Restricted external ingress requires a private hotspot.\n' >&2
    return 1
  fi
  if ! nmcli -t -f TYPE,STATE,CONNECTION device status \
    | grep -F -x 'wifi:connected:showco-private' >/dev/null; then
    printf 'Private Wi-Fi must be active before restricting ingress.\n' >&2
    return 1
  fi
  if [[ "$X18" == true ]]; then
    private_interface=br-x18
  else
    private_interface=$(nmcli -g connection.interface-name connection show showco-private)
  fi
  if [[ ! "$private_interface" =~ ^[a-zA-Z0-9_.-]+$ ]]; then
    printf 'Cannot identify the private Wi-Fi interface for the firewall.\n' >&2
    return 1
  fi
  if [[ ! "$SHOWCO_SSH_PORT" =~ ^[0-9]+$ ]] \
    || ((SHOWCO_SSH_PORT < 1 || SHOWCO_SSH_PORT > 65535)); then
    printf 'Invalid SSH port for the firewall.\n' >&2
    return 1
  fi

  temporary=$(mktemp)
  cat >"$temporary" <<EOF
table inet showco_ingress {
  chain input {
    type filter hook input priority -10; policy drop;
    iifname "lo" accept
    ct state established,related accept
    iifname "$private_interface" accept
    tcp dport $SHOWCO_SSH_PORT accept
    udp dport 5353 accept
    udp sport 67 udp dport 68 accept
    icmpv6 type { nd-neighbor-solicit, nd-router-advert, nd-neighbor-advert } accept
  }
}
EOF
  sudo nft --check --file "$temporary"
  sudo install -d -m 0755 /etc/showco
  sudo install -m 0644 "$temporary" "$rules"
  cat >"$temporary" <<'EOF'
[Unit]
Description=showCo private-network ingress policy
DefaultDependencies=no
Wants=network-pre.target
Before=network-pre.target
After=local-fs.target nftables.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=-/usr/sbin/nft delete table inet showco_ingress
ExecStart=/usr/sbin/nft -f /etc/showco/ingress.nft
ExecStop=-/usr/sbin/nft delete table inet showco_ingress

[Install]
WantedBy=multi-user.target
EOF
  sudo install -m 0644 "$temporary" "$unit"
  sudo systemctl daemon-reload
  sudo systemctl enable showco-ingress.service
  sudo systemctl restart showco-ingress.service
  sudo nft list table inet showco_ingress >/dev/null
  rm -f "$temporary"
}
