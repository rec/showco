showco_args() {
  local args=(
    --host 0.0.0.0
    --port "$SHOWCO_PORT"
  )
  args+=(--mixers-config "/home/$SHOW_USER/.config/showco/mixers.toml")
  if [[ "$STREAMO_ENABLED" == true ]]; then
    args+=(--streamo-enabled)
  fi
  if [[ "$LYTE_ENABLED" == true ]]; then
    args+=(--lyte-enabled)
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

service_is_current() {
  local name=$1
  local input=$2
  local state_file="/home/$SHOW_USER/.local/state/showco/$name-service.sha256"
  local hash

  hash=$(printf '%s' "$input" | sha256sum | awk '{print $1}')
  [[ -f "$state_file" && "$(cat "$state_file")" == "$hash" ]] \
    && user_systemctl is-active --quiet "$name.service"
}

record_service_state() {
  local name=$1
  local input=$2
  local state_file="/home/$SHOW_USER/.local/state/showco/$name-service.sha256"

  printf '%s' "$input" | sha256sum | awk '{print $1}' \
    | sudo -H -u "$SHOW_USER" tee "$state_file" >/dev/null
}

service_input() {
  printf '%s\n%s\n%s\n' "$(sha256sum "$0" | awk '{print $1}')" "$ROOT" "$1"
}

install_recs_service() {
  local quoted_args=
  local osc_nodes=
  local uid
  local args=()
  local input
  uid=$(id -u "$SHOW_USER")
  while IFS= read -r device_name; do
    [[ -n "$device_name" ]] && args+=(--include "$device_name")
  done <<<"$RECS_AUDIO_DEVICE_NAMES"
  while IFS= read -r device_name; do
    [[ -n "$device_name" ]] && args+=(--midi-include "$device_name")
  done <<<"$RECS_MIDI_INPUT_NAMES"
  if [[ -n "$RECS_OSC_NODES_TOML" ]]; then
    osc_nodes="/home/$SHOW_USER/.config/recs/mixers.toml"
    sudo -H -u "$SHOW_USER" mkdir -p "${osc_nodes%/*}"
    sudo -H -u "$SHOW_USER" sh -c 'printf "%s" "$1" > "$2"' sh "$RECS_OSC_NODES_TOML" "$osc_nodes"
    args+=(--osc-nodes "$osc_nodes")
  fi
  if [[ ${#args[@]} -gt 0 ]]; then
    quoted_args=$(printf '%q ' "${args[@]}")
  fi
  input=$(service_input "$(git -C "$ROOT/recs" rev-parse HEAD)
$RECS_AUDIO_DEVICE_NAMES
$RECS_MIDI_INPUT_NAMES
$RECS_OSC_NODES_TOML")
  if service_is_current recs "$input"; then
    printf 'recs service is already installed.\n'
    return
  fi
  sudo -H -u "$SHOW_USER" \
    env XDG_RUNTIME_DIR="/run/user/$uid" \
    PATH="$ROOT/recs/.venv/bin:/home/$SHOW_USER/.local/bin:$PATH" \
    bash -lc "cd '$ROOT/recs' && uv run --locked recs daemon install $quoted_args"
  user_systemctl restart recs.service
  record_service_state recs "$input"
}

install_showco_service() {
  local uid
  local input
  uid=$(id -u "$SHOW_USER")
  input=$(service_input "$(git -C "$ROOT/showco" rev-parse HEAD)
$SHOWCO_PORT
$STREAMO_ENABLED
$LYTE_ENABLED
$SHOWCO_MIXERS_TOML")
  if service_is_current showco "$input"; then
    printf 'showCo service is already installed.\n'
    return
  fi
  sudo -H -u "$SHOW_USER" \
    env XDG_RUNTIME_DIR="/run/user/$uid" \
    PATH="$ROOT/showco/.venv/bin:/home/$SHOW_USER/.local/bin:$PATH" \
    bash -lc "mkdir -p /home/$SHOW_USER/.config/showco && printf '%s' \"$SHOWCO_MIXERS_TOML\" > /home/$SHOW_USER/.config/showco/mixers.toml && cd '$ROOT/showco' && uv run --locked showco run install-service --root '$ROOT' $(showco_args)"
  user_systemctl restart showco.service
  record_service_state showco "$input"
}

migrate_streamo_configuration() {
  local source="/home/$SHOW_USER/.config/twitcho/config.json"
  local target="/home/$SHOW_USER/.config/streamo/config.toml"
  local quoted_source
  local quoted_target
  local uid
  if [[ "$STREAMO_ENABLED" != true || -f "$target" || ! -f "$source" ]]; then
    return
  fi
  quoted_source=$(printf '%q' "$source")
  quoted_target=$(printf '%q' "$target")
  uid=$(id -u "$SHOW_USER")
  sudo -H -u "$SHOW_USER" \
    env XDG_RUNTIME_DIR="/run/user/$uid" \
    PATH="$ROOT/showco/.venv/bin:/home/$SHOW_USER/.local/bin:$PATH" \
    bash -lc "cd '$ROOT/showco' && uv run --locked showco run streamo-config --source $quoted_source --target $quoted_target"
  printf 'Converted Twitcho configuration to streamO TOML.\n'
}

uninstall_twitcho_service() {
  local command="$ROOT/twitcho/.venv/bin/twitcho"
  if [[ ! -x "$command" ]]; then
    return
  fi
  "$command" daemon stop >/dev/null 2>&1 || true
  "$command" daemon uninstall >/dev/null 2>&1 || true
}

install_streamo_service() {
  local config_path
  local quoted_config
  local uid
  local input
  if [[ "$STREAMO_ENABLED" != true ]]; then
    printf 'streamO service is disabled.\n'
    return
  fi
  config_path="/home/$SHOW_USER/.config/streamo/config.toml"
  if [[ ! -f "$config_path" ]]; then
    printf 'ERROR: streamO configuration does not exist: %s\n' "$config_path" >&2
    return 1
  fi
  quoted_config=$(printf '%q' "$config_path")
  uid=$(id -u "$SHOW_USER")
  input=$(service_input "$(git -C "$ROOT/streamo" rev-parse HEAD)
$(sha256sum "$config_path" | awk '{print $1}')")
  if service_is_current streamo "$input"; then
    printf 'streamO service is already installed.\n'
    return
  fi
  sudo -H -u "$SHOW_USER" \
    env XDG_RUNTIME_DIR="/run/user/$uid" \
    PATH="$ROOT/streamo/.venv/bin:/home/$SHOW_USER/.local/bin:$PATH" \
    bash -lc "cd '$ROOT/streamo' && uv run --locked streamo daemon install --config $quoted_config"
  user_systemctl restart streamo.service
  record_service_state streamo "$input"
}

install_lyte_service() {
  local config_path
  local quoted_config
  local uid
  local input
  if [[ "$LYTE_ENABLED" != true ]]; then
    printf 'lyte service is disabled.\n'
    return
  fi
  config_path="$ROOT/lyte/$LYTE_INSTALLATION_CONFIG"
  if [[ ! -f "$config_path" ]]; then
    printf 'ERROR: lyte installation configuration does not exist: %s\n' "$config_path" >&2
    return 1
  fi
  quoted_config=$(printf '%q' "$config_path")
  uid=$(id -u "$SHOW_USER")
  input=$(service_input "$(git -C "$ROOT/lyte" rev-parse HEAD)
$(sha256sum "$config_path" | awk '{print $1}')")
  if service_is_current lyte "$input"; then
    printf 'lyte service is already installed.\n'
    return
  fi
  sudo -H -u "$SHOW_USER" \
    env XDG_RUNTIME_DIR="/run/user/$uid" \
    PATH="$ROOT/lyte/.venv/bin:/home/$SHOW_USER/.local/bin:$PATH" \
    bash -lc "cd '$ROOT/lyte' && uv run --locked lyte installation install $quoted_config"
  user_systemctl restart lyte.service
  record_service_state lyte "$input"
}
