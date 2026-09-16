write_provisioning_report() {
  local report="/tmp/SHOWCO-PROVISIONING-REPORT.txt"
  {
    printf 'showCo provisioning report\n'
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
    printf '\nlyte:\n'
    printf 'enabled: %s\n' "$LYTE_ENABLED"
    printf 'installation config: %s\n' "$LYTE_INSTALLATION_CONFIG"
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
    printf '\nstreamO:\n'
    printf 'enabled: %s\n' "$STREAMO_ENABLED"
    if [[ "$STREAMO_ENABLED" == true ]]; then
      printf 'streamo service: '
      user_systemctl is-active streamo.service || true
    else
      printf 'streamo service: disabled\n'
    fi
  } | tee "$report"
  sudo install -o "$SHOW_USER" -g "$SHOW_USER" -m 0644 \
    "$report" \
    "/home/$SHOW_USER/PROVISIONING-REPORT.txt"
  rm -f "$report"
}

phase() {
  if (( PHASE_STARTED_SECONDS > 0 )); then
    printf '    completed in %ss\n' "$((SECONDS - PHASE_STARTED_SECONDS))"
  fi
  printf '\n==> %s\n' "$1"
  PHASE_STARTED_SECONDS=$SECONDS
}

main() {
  sudo rm -f /run/showco-provision-reboot-required

  phase "checking user"
  id "$SHOW_USER" >/dev/null

  phase "configuring locale"
  configure_locale

  phase "configuring persistent journal"
  configure_journal

  phase "installing base packages"
  packages=(
    alsa-utils
    build-essential
    ca-certificates
    curl
    emacs
    ffmpeg
    git
    libegl1
    libasound2-dev
    libportaudio2
    libsndfile1
    locales
    network-manager
    openssh-client
    rsync
    sudo
    tmux
    exfatprogs
  )
  printf 'Installing packages:\n'
  printf '  %s\n' "${packages[@]}"
  install_base_packages "${packages[@]}"

  if [[ "$ARGON_ONE" == true ]]; then
    phase "installing Argon ONE software"
    install_argon_one
  fi

  phase "creating directories"
  sudo mkdir -p "$ROOT"
  sudo chown "$SHOW_USER:$SHOW_USER" "$ROOT"
  sudo -H -u "$SHOW_USER" mkdir -p \
    "/home/$SHOW_USER/.config/recs" \
    "/home/$SHOW_USER/.config/showco" \
    "/home/$SHOW_USER/.config/streamo" \
    "/home/$SHOW_USER/.config/lyte" \
    "/home/$SHOW_USER/.local/state/recs" \
    "/home/$SHOW_USER/.local/state/showco" \
    "/home/$SHOW_USER/.local/state/streamo" \
    "/home/$SHOW_USER/.local/state/lyte" \
    "/home/$SHOW_USER/recordings"
  printf 'target\n' | sudo -H -u "$SHOW_USER" tee \
    "/home/$SHOW_USER/.config/showco/machine-role" >/dev/null

  phase "configuring storage mounts"
  configure_storage_mounts

  phase "installing uv"
  install_uv

  phase "configuring GitHub URLs"
  sudo -H -u "$SHOW_USER" git config --global url."https://github.com/".insteadOf \
    "ssh://git@github.com/"

  phase "installing Python 3.13"
  install_python

  phase "syncing repositories"
  sync_repo reccy "$RECCY_REPO" "$RECCY_REFNAME"
  sync_repo recs "$RECS_REPO" "$RECS_REFNAME"
  sync_repo streamo "$STREAMO_REPO" "$STREAMO_REFNAME"
  sync_repo lyte "$LYTE_REPO" "$LYTE_REFNAME"
  sync_repo showco "$SHOWCO_REPO" "$SHOWCO_REFNAME"

  phase "migrating Twitcho service"
  uninstall_twitcho_service
  migrate_streamo_configuration

  phase "enabling user service autostart"
  sudo loginctl enable-linger "$SHOW_USER"

  phase "installing recs service"
  install_recs_service

  phase "installing streamO service"
  install_streamo_service

  phase "installing showco service"
  install_showco_service

  phase "installing lyte service"
  install_lyte_service

  phase "writing provisioning report"
  write_provisioning_report

  phase "writing next steps"
  cat >/tmp/PROVISIONING-NEXT-STEPS.txt <<'TEXT'
Provisioning completed.

Next manual steps:

1. Fill final streamo config values if Stream streaming is required.
2. Configure and enable lyte if lighting control is required.
3. Fill Wi-Fi password values and rerun provisioning if network configuration was skipped.
4. Confirm the X18 USB device name.
5. Run the installation checks in showco/doc/README.md, Before the performance.
TEXT
  sudo install -o "$SHOW_USER" -g "$SHOW_USER" -m 0644 \
    /tmp/PROVISIONING-NEXT-STEPS.txt \
    "/home/$SHOW_USER/PROVISIONING-NEXT-STEPS.txt"
  rm -f /tmp/PROVISIONING-NEXT-STEPS.txt

  phase "configuring network"
  configure_network

  phase "rebooting"
  if [[ -f /var/run/reboot-required ]]; then
    sudo touch /run/showco-provision-reboot-required
    printf 'Reboot required.\n'
  else
    printf 'Reboot not required.\n'
  fi
  printf 'Provisioning completed in %ss\n' "$SECONDS"
}

main "$@"
