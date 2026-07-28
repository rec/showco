#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import secrets
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

import tyro
from pydantic import BaseModel

PROVISION_DIR = Path(__file__).resolve().parent.parent / "provision"
DEFAULT_CONFIG_PATH = PROVISION_DIR / "config.toml"
DEFAULT_SECRETS_PATH = PROVISION_DIR / "secrets.toml"
TWITCH_AUTHORIZE_URL = "https://id.twitch.tv/oauth2/authorize"
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
TWITCH_VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"


class HttpResponse(BaseModel, frozen=True):
    status: int
    text: str


class AuthOptions(BaseModel, frozen=True):
    config: Path
    secrets: Path


def main(argv: list[str] | None = None) -> int:
    return tyro.extras.subcommand_cli_from_dict(
        {
            "authorize-url": authorize_url_command,
            "exchange-code": exchange_code_command,
            "validate-token": validate_token_command,
        },
        args=argv,
        description="Manage Showco Twitch OAuth tokens",
        sort_subcommands=True,
    )


def auth_options(
    config: Path = DEFAULT_CONFIG_PATH,
    secrets: Path = DEFAULT_SECRETS_PATH,
) -> AuthOptions:
    return AuthOptions(config=config, secrets=secrets)


def authorize_url_command(
    config: Path = DEFAULT_CONFIG_PATH,
    secrets: Path = DEFAULT_SECRETS_PATH,
) -> int:
    options = auth_options(config, secrets)
    return authorize_url(options.config, read_toml(options.config))


def exchange_code_command(
    config: Path = DEFAULT_CONFIG_PATH,
    secrets: Path = DEFAULT_SECRETS_PATH,
) -> int:
    options = auth_options(config, secrets)
    return exchange_code(read_toml(options.config) | read_toml(options.secrets))


def validate_token_command(
    config: Path = DEFAULT_CONFIG_PATH,
    secrets: Path = DEFAULT_SECRETS_PATH,
) -> int:
    options = auth_options(config, secrets)
    return validate_token(read_toml(options.config))


def authorize_url(config_path: Path, config: dict[str, str]) -> int:
    client_id = require_value(config, "twitch_client_id", config_path)
    redirect_uri = require_value(config, "twitch_redirect_uri", config_path)
    scopes = require_value(config, "twitch_scopes", config_path)
    state = secrets.token_urlsafe(24)
    write_toml_value(config_path, "twitch_state", state)
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
        "address bar and paste it into twitch_callback_url_or_code in secrets.toml."
    )
    return 0


def exchange_code(env: dict[str, str]) -> int:
    client_id = require_value(env, "twitch_client_id")
    client_secret = require_value(env, "twitch_client_secret")
    redirect_uri = require_value(env, "twitch_redirect_uri")
    callback = require_value(env, "twitch_callback_url_or_code")
    config_dir = Path(require_value(env, "twitch_config_dir")).expanduser()
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
    http_response = request_http(request)
    response_file.write_text(http_response.text + "\n")
    try:
        response = json.loads(http_response.text)
    except json.JSONDecodeError:
        print(
            "Twitch returned non-JSON token response. "
            f"Response saved to {response_file}"
        )
        return 1
    if http_response.status < 200 or http_response.status >= 300:
        print(
            f"Twitch token request failed with HTTP {http_response.status}. "
            f"Response saved to {response_file}"
        )
        print(json.dumps(response, indent=2))
        return 1

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
    print("Run showco twitcho validate-token next.")
    return 0


def validate_token(config: dict[str, str]) -> int:
    config_dir = Path(require_value(config, "twitch_config_dir")).expanduser()
    token_file = config_dir / "oauth-token"
    if not token_file.exists():
        message = (
            f"No access token at {token_file}. Run showco twitcho exchange-code first."
        )
        sys.exit(message)
    token = token_file.read_text().strip()
    request = urllib.request.Request(
        TWITCH_VALIDATE_URL,
        headers={"Authorization": f"OAuth {token}"},
    )
    http_response = request_http(request)
    if http_response.status < 200 or http_response.status >= 300:
        sys.exit(f"Twitch token validation failed with HTTP {http_response.status}.")
    try:
        response = json.loads(http_response.text)
    except json.JSONDecodeError:
        sys.exit("Twitch returned non-JSON token validation response.")
    print(json.dumps(response, indent=4))
    return 0


def callback_code(value: str) -> str:
    if value.startswith(("http://", "https://")):
        url = urllib.parse.urlparse(value)
        params = urllib.parse.parse_qs(url.query)
        return params["code"][0]
    return value


def request_http(request: urllib.request.Request) -> HttpResponse:
    try:
        with urllib.request.urlopen(request) as response:
            return HttpResponse(status=response.status, text=response.read().decode())
    except urllib.error.HTTPError as e:
        return HttpResponse(status=e.code, text=e.read().decode())


def read_toml(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    try:
        parsed = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        sys.exit(f"Cannot parse {path}: {e}")
    for name, value in parsed.items():
        if isinstance(value, str):
            values[name] = os.path.expandvars(value)
    return values


def write_toml_value(path: Path, name: str, value: str) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    replacement = f"{name} = {json.dumps(value)}"
    for i, line in enumerate(lines):
        if line.startswith(f"{name} ="):
            lines[i] = replacement
            break
    else:
        lines.append(replacement)
    path.write_text("\n".join(lines) + "\n")


def require_value(env: dict[str, str], name: str, path: Path | None = None) -> str:
    value = env.get(name, "")
    if value and value != "TODO":
        return value
    if path is None:
        sys.exit(f"Set {name} first.")
    sys.exit(f"Edit {path} and set {name} first.")


def provision_dir() -> Path:
    return PROVISION_DIR


if __name__ == "__main__":
    sys.exit(main())
