from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import cast

from pydantic import BaseModel, Field


class GitRepo(BaseModel, frozen=True):
    url: str
    refname: str = ""


class Network(BaseModel, frozen=True):
    name: str = ""
    dhcp_start: str = ""
    dhcp_end: str = ""
    ip_address: str = ""
    subnet: str = ""
    password: str = ""


class NetworkConfig(BaseModel, frozen=True):
    host: str = ""
    user: str = ""
    ssh_port: int = 22
    web_port: int = 17352
    swap_wifi: bool = False
    topology: str = ""


class Usb(BaseModel, frozen=True):
    x18_device_name: str = ""


class Twitch(BaseModel, frozen=True):
    enabled: bool = False
    account: str = ""
    stream_title: str = ""
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    chat_messages: list[str] = Field(default_factory=list)
    stream_marker_descriptions: list[str] = Field(default_factory=list)
    id: str = ""
    oauth_token_scopes: list[str] = Field(default_factory=list)
    client_id: str = ""
    redirect_uri: str = ""
    scopes: str = ""
    config_dir: str = ""
    state: str = ""
    stream_key: str = ""
    client_secret: str = ""
    oath_token: str = ""
    callback_url_or_code: str = ""


class Git(BaseModel, frozen=True):
    reccy: GitRepo
    recs: GitRepo
    twitcho: GitRepo
    showco: GitRepo


class Config(BaseModel, frozen=True):
    network: NetworkConfig
    networks: dict[str, dict[str, dict[str, Network]]]
    usb: Usb
    twitch: Twitch
    git: Git


def config_from_values(
    values: dict[str, object],
    *,
    host: str | None = None,
    user: str | None = None,
    port: int | None = None,
    reccy_repo: str | None = None,
    recs_repo: str | None = None,
    twitcho_repo: str | None = None,
    showco_repo: str | None = None,
) -> Config:
    network = table_value(values, "network")
    git = table_value(values, "git")
    return Config(
        network=NetworkConfig(
            host=require_value("network.host", value_or_env(host, network, "host")),
            user=require_value(
                "network.user or USER",
                value_or_env(
                    user,
                    network,
                    "user",
                    default=os.environ.get("USER", ""),
                ),
            ),
            ssh_port=value_or_int(port, network, "ssh_port", default=22),
            web_port=int_value(network, "web_port", default=17352),
            swap_wifi=bool_value(network, "swap_wifi", default=False),
            topology=string_value(network, "topology"),
        ),
        networks=networks_value(table_value(values, "networks")),
        usb=Usb(
            x18_device_name=string_value(table_value(values, "usb"), "x18_device_name")
        ),
        twitch=twitch_value(table_value(values, "twitch")),
        git=Git(
            reccy=git_repo("reccy", table_value(git, "reccy"), override=reccy_repo),
            recs=git_repo("recs", table_value(git, "recs"), override=recs_repo),
            twitcho=git_repo(
                "twitcho",
                table_value(git, "twitcho"),
                override=twitcho_repo,
            ),
            showco=git_repo(
                "showco",
                table_value(git, "showco"),
                override=showco_repo,
            ),
        ),
    )


def networks_value(
    values: dict[str, object],
) -> dict[str, dict[str, dict[str, Network]]]:
    return {
        k: network_kind_dict(table_value(values, k), f"networks.{k}") for k in values
    }


def network_kind_dict(
    values: dict[str, object], name: str
) -> dict[str, dict[str, Network]]:
    return {k: network_dict(table_value(values, k), f"{name}.{k}") for k in values}


def network_dict(values: dict[str, object], name: str) -> dict[str, Network]:
    networks = {}
    for k, v in values.items():
        if not isinstance(v, dict):
            sys.exit(f"ERROR: {name}.{k} must be a table")
        table = cast(dict[str, object], v)
        networks[k] = Network(
            name=string_value(table, "name"),
            dhcp_start=string_value(table, "dhcp_start"),
            dhcp_end=string_value(table, "dhcp_end"),
            ip_address=string_value(table, "ip_address"),
            subnet=string_value(table, "subnet"),
            password=string_value(table, "password"),
        )
    return networks


def first_network(
    networks: dict[str, Network],
    name: str,
    *,
    key: str = "",
    default: Network | None = None,
) -> Network:
    if key and key in networks:
        return networks[key]
    if networks:
        return next(iter(networks.values()))
    if default is not None:
        return default
    sys.exit(f"ERROR: {name} must contain at least one network")


def networks_at(config: Config, owner: str, connection: str) -> dict[str, Network]:
    return config.networks.get(owner, {}).get(connection, {})


def internal_wifi(config: Config) -> Network:
    return first_network(
        networks_at(config, "internal", "wifi"),
        "networks.internal.wifi",
        key="private",
        default=Network(name="showbox"),
    )


def external_wifi(config: Config) -> Network:
    return first_network(
        networks_at(config, "external", "wifi"),
        "networks.external.wifi",
        key="external",
        default=Network(),
    )


def x18(config: Config) -> Network | None:
    networks = networks_at(config, "internal", "wired")
    if not networks:
        return None
    return first_network(networks, "networks.internal.wired", key="x18")


def string_or_default(value: str, default: str) -> str:
    if value:
        return value
    return default


def twitch_value(values: dict[str, object]) -> Twitch:
    return Twitch(
        enabled=bool_value(values, "enabled", default=False),
        account=string_value(values, "account"),
        stream_title=string_value(values, "stream_title"),
        category=string_value(values, "category"),
        tags=string_list_value(values, "tags"),
        chat_messages=string_list_value(values, "chat_messages"),
        stream_marker_descriptions=string_list_value(
            values, "stream_marker_descriptions"
        ),
        id=string_value(values, "id"),
        oauth_token_scopes=string_list_value(values, "oauth_token_scopes"),
        client_id=string_value(values, "client_id"),
        redirect_uri=string_value(values, "redirect_uri"),
        scopes=string_value(values, "scopes"),
        config_dir=string_value(values, "config_dir"),
        state=string_value(values, "state"),
        stream_key=string_value(values, "stream_key"),
        client_secret=string_value(values, "client_secret"),
        oath_token=string_value(values, "oath_token"),
        callback_url_or_code=string_value(values, "callback_url_or_code"),
    )


def read_toml(path: Path) -> dict[str, object]:
    path = path.expanduser()
    if not path.exists():
        return {}
    try:
        parsed = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        sys.exit(f"ERROR: Cannot parse {path}: {e}")
    return {k: toml_value(v) for k, v in parsed.items()}


def merge_values(
    config: dict[str, object], secrets: dict[str, object]
) -> dict[str, object]:
    result = dict(config)
    for k, v in secrets.items():
        current = result.get(k)
        if isinstance(v, dict) and isinstance(current, dict):
            result[k] = merge_values(
                cast(dict[str, object], current),
                cast(dict[str, object], v),
            )
        else:
            result[k] = v
    return result


def git_repo(name: str, values: dict[str, object], *, override: str | None) -> GitRepo:
    url = override or string_value(values, "url")
    return GitRepo(
        url=require_value(f"git.{name}.url", url),
        refname=string_value(values, "refname"),
    )


def table_value(values: dict[str, object], name: str) -> dict[str, object]:
    value = values.get(name, {})
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    sys.exit(f"ERROR: {name} must be a table")


def value_or_env(
    value: str | None,
    env: dict[str, object],
    name: str,
    *,
    default: str = "",
) -> str:
    if value:
        return value
    return string_value(env, name, default=default)


def value_or_int(
    value: int | None,
    env: dict[str, object],
    name: str,
    *,
    default: int,
) -> int:
    if value is not None:
        return value
    return int_value(env, name, default=default)


def require_value(name: str, value: str) -> str:
    if value and value != "TODO":
        return value
    sys.exit(f"ERROR: {name} is required")


def bool_value(values: dict[str, object], name: str, *, default: bool) -> bool:
    value = values.get(name, default)
    if isinstance(value, bool):
        return value
    sys.exit(f"ERROR: {name} must be a boolean")


def int_value(values: dict[str, object], name: str, *, default: int) -> int:
    value = values.get(name, default)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    sys.exit(f"ERROR: {name} must be an integer")


def string_value(
    values: dict[str, object],
    name: str,
    *,
    default: str = "",
) -> str:
    value = values.get(name)
    if value is None:
        value = default
    if isinstance(value, str):
        return os.path.expandvars(value)
    sys.exit(f"ERROR: {name} must be a string")


def string_list_value(values: dict[str, object], name: str) -> list[str]:
    value = values.get(name, [])
    if isinstance(value, list) and all(isinstance(i, str) for i in value):
        return cast(list[str], value)
    sys.exit(f"ERROR: {name} must be a list of strings")


def toml_value(value: object) -> object:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, list) and all(isinstance(i, str) for i in value):
        return value
    if isinstance(value, list):
        return [toml_value(i) for i in value]
    if isinstance(value, dict):
        return {k: toml_value(v) for k, v in value.items()}
    return None
