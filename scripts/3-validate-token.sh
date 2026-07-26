#!/usr/bin/env bash
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$here/config.env"

token_file="$TWITCH_CONFIG_DIR/oauth-token"
if [[ ! -f "$token_file" ]]; then
  echo "No access token at $token_file. Run 2-exchange-code.sh first." >&2
  exit 1
fi

token=$(<"$token_file")

curl -sS "https://id.twitch.tv/oauth2/validate" \
  -H "Authorization: OAuth $token" \
  | python3 -m json.tool
