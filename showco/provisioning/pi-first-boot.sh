#!/usr/bin/env bash
set -euo pipefail

source /etc/showco-provisioning.env

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

main() {
  mkdir -p "$CODE_DIR"
  chown "$SHOW_USER:$SHOW_USER" "$CODE_DIR"

  install_uv

  sudo -H -u "$SHOW_USER" mkdir -p \
    "/home/$SHOW_USER/.config/recs" \
    "/home/$SHOW_USER/.config/showco" \
    "/home/$SHOW_USER/.config/twitcho" \
    "/home/$SHOW_USER/.local/state/recs" \
    "/home/$SHOW_USER/.local/state/twitcho" \
    "/home/$SHOW_USER/recordings"

  sync_repo recs "$RECS_REPO"
  sync_repo twitcho "$TWITCHO_REPO"
  sync_repo showco "$SHOWCO_REPO"

  cat >/home/"$SHOW_USER"/PROVISIONING-NEXT-STEPS.txt <<'EOF'
Provisioning completed.

Next manual steps:

1. Fill final recs, twitcho, and showco config values.
2. Install user-level systemd services.
3. Configure the final Pi access point.
4. Confirm the X18 USB device name.
5. Run the acceptance tests in showco/doc/acceptance-tests.md.
EOF

  chown "$SHOW_USER:$SHOW_USER" /home/"$SHOW_USER"/PROVISIONING-NEXT-STEPS.txt
}

main "$@"
