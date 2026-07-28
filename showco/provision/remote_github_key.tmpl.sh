set -e
{
if ! command -v ssh-keygen >/dev/null 2>&1 || ! command -v ssh-keyscan >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y openssh-client
fi
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
if [ ! -f "$HOME/.ssh/id_ed25519" ]; then
  ssh-keygen -t ed25519 -N '' -C {comment} -f "$HOME/.ssh/id_ed25519" >/dev/null
fi
chmod 600 "$HOME/.ssh/id_ed25519"
if [ ! -f "$HOME/.ssh/id_ed25519.pub" ]; then
  ssh-keygen -y -f "$HOME/.ssh/id_ed25519" > "$HOME/.ssh/id_ed25519.pub"
fi
touch "$HOME/.ssh/known_hosts"
chmod 600 "$HOME/.ssh/known_hosts"
ssh-keygen -F github.com -f "$HOME/.ssh/known_hosts" >/dev/null 2>&1 || ssh-keyscan github.com >> "$HOME/.ssh/known_hosts"
} >&2
cat "$HOME/.ssh/id_ed25519.pub"
