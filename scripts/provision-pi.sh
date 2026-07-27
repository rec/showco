#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  showco/scripts/provision-pi.sh

  showco/scripts/provision-pi.sh \
    --host recs-stage.local \
    --user show \
    --port 22

Options:
  --config PATH                default: scripts/config.env
  --secrets PATH               default: scripts/secrets.env
  --host HOST                  default: SHOWCO_PI_HOST
  --user USER                  default: SHOWCO_PI_USER
  --port PORT                  default: SHOWCO_PI_SSH_PORT
  --recs-repo URL              default: RECS_REPO
  --twitcho-repo URL           default: TWITCHO_REPO
  --showco-repo URL            default: SHOWCO_REPO
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

expand_path() {
  case "$1" in
    "~/"*) printf '%s/%s\n' "$HOME" "${1#"~/"}" ;;
    *) printf '%s\n' "$1" ;;
  esac
}

require_value() {
  local name=$1
  local value=$2
  [[ -n "$value" && "$value" != TODO ]] || die "$name is required"
}

quote_env() {
  local name=$1
  local value=$2
  printf '%s=%q' "$name" "$value"
}

ssh_command() {
  if [[ -n "${SHOWCO_PI_PASSWORD:-}" && "$SHOWCO_PI_PASSWORD" != TODO ]] && command -v sshpass >/dev/null; then
    SSHPASS=$SHOWCO_PI_PASSWORD sshpass -e ssh -t -p "$pi_port" "$ssh_target" "$@"
  else
    ssh -t -p "$pi_port" "$ssh_target" "$@"
  fi
}

scp_command() {
  if [[ -n "${SHOWCO_PI_PASSWORD:-}" && "$SHOWCO_PI_PASSWORD" != TODO ]] && command -v sshpass >/dev/null; then
    SSHPASS=$SHOWCO_PI_PASSWORD sshpass -e scp -P "$pi_port" "$@"
  else
    scp -P "$pi_port" "$@"
  fi
}

write_remote_script() {
  local target=$1
  cat >"$target" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

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
    openssh-client
    python3
    python3-venv
    rsync
    sudo
  )
  printf 'Installing packages:\n'
  printf '  %s\n' "${packages[@]}"
  sudo apt-get update
  sudo apt-get install -y "${packages[@]}"

  phase "creating directories"
  sudo mkdir -p "$CODE_DIR"
  sudo chown "$SHOW_USER:$SHOW_USER" "$CODE_DIR"
  sudo -H -u "$SHOW_USER" mkdir -p \
    "/home/$SHOW_USER/.config/recs" \
    "/home/$SHOW_USER/.config/showco" \
    "/home/$SHOW_USER/.config/twitcho" \
    "/home/$SHOW_USER/.local/state/recs" \
    "/home/$SHOW_USER/.local/state/twitcho" \
    "/home/$SHOW_USER/recordings"

  phase "installing uv"
  install_uv

  phase "syncing repositories"
  sync_repo recs "$RECS_REPO"
  sync_repo twitcho "$TWITCHO_REPO"
  sync_repo showco "$SHOWCO_REPO"

  phase "writing next steps"
  cat >/tmp/PROVISIONING-NEXT-STEPS.txt <<'TEXT'
Provisioning completed.

Next manual steps:

1. Fill final recs, twitcho, and showco config values.
2. Install recs and showco user-level systemd services.
3. Configure the final Pi access point.
4. Confirm the X18 USB device name.
5. Run the acceptance tests in showco/doc/acceptance-tests.md.
TEXT
  sudo install -o "$SHOW_USER" -g "$SHOW_USER" -m 0644 \
    /tmp/PROVISIONING-NEXT-STEPS.txt \
    "/home/$SHOW_USER/PROVISIONING-NEXT-STEPS.txt"
  rm -f /tmp/PROVISIONING-NEXT-STEPS.txt
}

main "$@"
EOF
}

config_file=$(script_dir)/config.env
secrets_file=$(script_dir)/secrets.env
pi_host=
pi_user=
pi_port=
recs_repo=
twitcho_repo=
showco_repo=

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
    --host)
      pi_host=$2
      shift 2
      ;;
    --user)
      pi_user=$2
      shift 2
      ;;
    --port)
      pi_port=$2
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

source "$config_file"
if [[ -f "$secrets_file" ]]; then
  source "$secrets_file"
fi

pi_host=${pi_host:-${SHOWCO_PI_HOST:-}}
pi_user=${pi_user:-${SHOWCO_PI_USER:-}}
pi_port=${pi_port:-${SHOWCO_PI_SSH_PORT:-22}}
recs_repo=${recs_repo:-${RECS_REPO:-}}
twitcho_repo=${twitcho_repo:-${TWITCHO_REPO:-}}
showco_repo=${showco_repo:-${SHOWCO_REPO:-}}

require_value SHOWCO_PI_HOST "$pi_host"
require_value SHOWCO_PI_USER "$pi_user"
require_value SHOWCO_PI_SSH_PORT "$pi_port"
require_value RECS_REPO "$recs_repo"
require_value TWITCHO_REPO "$twitcho_repo"
require_value SHOWCO_REPO "$showco_repo"

ssh_target="$pi_user@$pi_host"
remote_script=/tmp/showco-provision-pi.sh
local_script=$(mktemp -t showco-provision-pi.XXXXXX)
trap 'rm -f "$local_script"' EXIT

write_remote_script "$local_script"

echo "Checking SSH connection to $ssh_target..."
ssh_command "set -e; uname -a; id; command -v sudo; command -v apt-get"

echo "Copying provisioning script..."
scp_command "$local_script" "$ssh_target:$remote_script"

echo "Running provisioning on $ssh_target..."
remote_command="$(
  quote_env SHOW_USER "$pi_user"
  printf ' '
  quote_env CODE_DIR "/home/$pi_user/code"
  printf ' '
  quote_env RECS_REPO "$recs_repo"
  printf ' '
  quote_env TWITCHO_REPO "$twitcho_repo"
  printf ' '
  quote_env SHOWCO_REPO "$showco_repo"
  printf ' bash %q' "$remote_script"
)"
ssh_command "$remote_command"
ssh_command "rm -f $remote_script"

echo "Provisioned $ssh_target."
