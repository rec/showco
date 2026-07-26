#!/usr/bin/env bash
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$here/config.env"
source "$here/secrets.env"

require_value() {
  local name=$1
  local value=$2
  if [[ -z "$value" || "$value" == TODO ]]; then
    echo "Edit $here/config.env or $here/secrets.env and set $name first." >&2
    exit 1
  fi
}

require_value TWITCH_CLIENT_ID "$TWITCH_CLIENT_ID"
require_value TWITCH_CLIENT_SECRET "$TWITCH_CLIENT_SECRET"
require_value TWITCH_REDIRECT_URI "$TWITCH_REDIRECT_URI"
require_value TWITCH_CALLBACK_URL_OR_CODE "$TWITCH_CALLBACK_URL_OR_CODE"
require_value TWITCH_CONFIG_DIR "$TWITCH_CONFIG_DIR"
export TWITCH_CALLBACK_URL_OR_CODE

code=$(python3 - <<'PY'
import os
import urllib.parse

value = os.environ["TWITCH_CALLBACK_URL_OR_CODE"]
if value.startswith("http://") or value.startswith("https://"):
    url = urllib.parse.urlparse(value)
    params = urllib.parse.parse_qs(url.query)
    print(params["code"][0])
else:
    print(value)
PY
)

mkdir -p "$TWITCH_CONFIG_DIR"
response_file="$TWITCH_CONFIG_DIR/oauth-response.json"

curl -sS -X POST "https://id.twitch.tv/oauth2/token" \
  -d "client_id=$TWITCH_CLIENT_ID" \
  -d "client_secret=$TWITCH_CLIENT_SECRET" \
  -d "code=$code" \
  -d "grant_type=authorization_code" \
  -d "redirect_uri=$TWITCH_REDIRECT_URI" \
  >"$response_file"

RESPONSE_FILE=$response_file TWITCH_CONFIG_DIR=$TWITCH_CONFIG_DIR python3 - <<'PY'
import json
import os
from pathlib import Path

response_file = Path(os.environ["RESPONSE_FILE"])
config_dir = Path(os.environ["TWITCH_CONFIG_DIR"])
data = json.loads(response_file.read_text())

if "access_token" not in data:
    print(f"Twitch did not return an access token. Response saved to {response_file}")
    print(json.dumps(data, indent=2))
    raise SystemExit(1)

(config_dir / "oauth-token").write_text(data["access_token"] + "\n")
if refresh_token := data.get("refresh_token"):
    (config_dir / "refresh-token").write_text(refresh_token + "\n")

print(f"Saved full response to {response_file}")
print(f"Saved access token to {config_dir / 'oauth-token'}")
if data.get("refresh_token"):
    print(f"Saved refresh token to {config_dir / 'refresh-token'}")
print()
print("Run 3-validate-token.sh next.")
PY
