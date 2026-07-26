#!/usr/bin/env bash
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$here/config.env"

require_value() {
  local name=$1
  local value=$2
  if [[ -z "$value" || "$value" == TODO ]]; then
    echo "Edit $here/config.env and set $name first." >&2
    exit 1
  fi
}

require_value TWITCH_CLIENT_ID "$TWITCH_CLIENT_ID"
require_value TWITCH_REDIRECT_URI "$TWITCH_REDIRECT_URI"
require_value TWITCH_SCOPES "$TWITCH_SCOPES"
export TWITCH_CLIENT_ID TWITCH_REDIRECT_URI TWITCH_SCOPES

state=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
if grep -q '^TWITCH_STATE=' "$here/config.env"; then
  TWITCH_STATE=$state perl -0pi -e 's/^TWITCH_STATE=.*/TWITCH_STATE="$ENV{TWITCH_STATE}"/m' "$here/config.env"
else
  printf 'TWITCH_STATE="%s"\n' "$state" >>"$here/config.env"
fi

url=$(TWITCH_STATE=$state python3 - <<'PY'
import os
import urllib.parse

params = {
    "response_type": "code",
    "client_id": os.environ["TWITCH_CLIENT_ID"],
    "redirect_uri": os.environ["TWITCH_REDIRECT_URI"],
    "scope": os.environ["TWITCH_SCOPES"],
    "state": os.environ["TWITCH_STATE"],
    "force_verify": "true",
}
print("https://id.twitch.tv/oauth2/authorize?" + urllib.parse.urlencode(params))
PY
)

open "$url"
cat <<'TEXT'
After approving it, copy the full localhost callback URL from the browser
address bar and paste it into TWITCH_CALLBACK_URL_OR_CODE in secrets.env.
TEXT
