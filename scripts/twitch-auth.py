#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import secrets
import shlex
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

TWITCH_AUTHORIZE_URL = "https://id.twitch.tv/oauth2/authorize"
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
TWITCH_VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage Showco Twitch OAuth tokens")
    parser.add_argument(
        "--config",
        type=Path,
        default=script_dir() / "config.env",
        help="path to config.env",
    )
    parser.add_argument(
        "--secrets",
        type=Path,
        default=script_dir() / "secrets.env",
        help="path to secrets.env",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("authorize-url", help="open the Twitch authorization URL")
    subparsers.add_parser("exchange-code", help="exchange callback code for tokens")
    subparsers.add_parser("validate-token", help="validate the saved access token")
    args = parser.parse_args()

    config = read_env(args.config)
    if args.command == "authorize-url":
        return authorize_url(args.config, config)
    if args.command == "exchange-code":
        secrets_env = read_env(args.secrets)
        return exchange_code(config | secrets_env)
    if args.command == "validate-token":
        return validate_token(config)
    sys.exit(f"unknown command: {args.command}")


def authorize_url(config_path: Path, config: dict[str, str]) -> int:
    client_id = require_value(config, "TWITCH_CLIENT_ID", config_path)
    redirect_uri = require_value(config, "TWITCH_REDIRECT_URI", config_path)
    scopes = require_value(config, "TWITCH_SCOPES", config_path)
    state = secrets.token_urlsafe(24)
    write_env_value(config_path, "TWITCH_STATE", state)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "state": state,
        "force_verify": "true",
    }
    url = TWITCH_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)
    webbrowser.open(url)
    print(
        "After approving it, copy the full localhost callback URL from the browser\n"
        "address bar and paste it into TWITCH_CALLBACK_URL_OR_CODE in secrets.env."
    )
    return 0


def exchange_code(env: dict[str, str]) -> int:
    client_id = require_value(env, "TWITCH_CLIENT_ID")
    client_secret = require_value(env, "TWITCH_CLIENT_SECRET")
    redirect_uri = require_value(env, "TWITCH_REDIRECT_URI")
    callback = require_value(env, "TWITCH_CALLBACK_URL_OR_CODE")
    config_dir = Path(require_value(env, "TWITCH_CONFIG_DIR")).expanduser()
    config_dir.mkdir(parents=True, exist_ok=True)
    response_file = config_dir / "oauth-response.json"
    data = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": callback_code(callback),
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
    ).encode()
    request = urllib.request.Request(TWITCH_TOKEN_URL, data=data, method="POST")
    response_text = request_text(request)
    response_file.write_text(response_text + "\n")
    response = json.loads(response_text)

    if "access_token" not in response:
        message = (
            f"Twitch did not return an access token. Response saved to {response_file}"
        )
        print(message)
        print(json.dumps(response, indent=2))
        return 1

    (config_dir / "oauth-token").write_text(response["access_token"] + "\n")
    if refresh_token := response.get("refresh_token"):
        (config_dir / "refresh-token").write_text(refresh_token + "\n")

    print(f"Saved full response to {response_file}")
    print(f"Saved access token to {config_dir / 'oauth-token'}")
    if response.get("refresh_token"):
        print(f"Saved refresh token to {config_dir / 'refresh-token'}")
    print()
    print("Run twitch-auth.py validate-token next.")
    return 0


def validate_token(config: dict[str, str]) -> int:
    config_dir = Path(require_value(config, "TWITCH_CONFIG_DIR")).expanduser()
    token_file = config_dir / "oauth-token"
    if not token_file.exists():
        message = (
            f"No access token at {token_file}. Run twitch-auth.py exchange-code first."
        )
        sys.exit(message)
    token = token_file.read_text().strip()
    request = urllib.request.Request(
        TWITCH_VALIDATE_URL,
        headers={"Authorization": f"OAuth {token}"},
    )
    print(json.dumps(json.loads(request_text(request)), indent=4))
    return 0


def callback_code(value: str) -> str:
    if value.startswith(("http://", "https://")):
        url = urllib.parse.urlparse(value)
        params = urllib.parse.parse_qs(url.query)
        return params["code"][0]
    return value


def request_text(request: urllib.request.Request) -> str:
    try:
        with urllib.request.urlopen(request) as response:
            return response.read().decode()
    except urllib.error.HTTPError as e:
        return e.read().decode()


def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, separator, value = stripped.partition("=")
        if not separator:
            continue
        parsed = shlex.split(value, comments=False, posix=True)
        if len(parsed) != 1:
            sys.exit(f"Cannot parse {path}: {line}")
        values[name] = os.path.expandvars(parsed[0])
    return values


def write_env_value(path: Path, name: str, value: str) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    replacement = f'{name}="{double_quote_value(value)}"'
    for i, line in enumerate(lines):
        if line.startswith(f"{name}="):
            lines[i] = replacement
            break
    else:
        lines.append(replacement)
    path.write_text("\n".join(lines) + "\n")


def double_quote_value(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )


def require_value(env: dict[str, str], name: str, path: Path | None = None) -> str:
    value = env.get(name, "")
    if value and value != "TODO":
        return value
    if path is None:
        sys.exit(f"Set {name} first.")
    sys.exit(f"Edit {path} and set {name} first.")


def script_dir() -> Path:
    return Path(__file__).resolve().parent


if __name__ == "__main__":
    sys.exit(main())
