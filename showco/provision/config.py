from __future__ import annotations

import os
import sys
import tomllib
from functools import cached_property
from ipaddress import IPv4Address, IPv4Network, ip_network
from pathlib import Path

from pydantic import BaseModel, Field
from typing_extensions import TypeIs

from ..mixer import MixerSpec, MixerSpecs


class GitRepo(BaseModel, frozen=True):
    url: str
    refname: str = ""


class Network(BaseModel, frozen=True):
    name: str = ""
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


class Paths(BaseModel, frozen=True):
    root: Path


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


class Lyte(BaseModel, frozen=True):
    enabled: bool = False
    daemon_config: Path = Path("patches/wearable-daemon.toml")


class Git(BaseModel, frozen=True):
    reccy: GitRepo
    recs: GitRepo
    twitcho: GitRepo
    showco: GitRepo
    lyte: GitRepo


class Config(BaseModel, frozen=True):
    network: NetworkConfig
    paths: Paths
    networks: dict[str, dict[str, dict[str, Network]]]
    mixers: list[MixerSpec] = Field(default_factory=list)
    twitch: Twitch
    lyte: Lyte
    git: Git
    accept_changed_host_key: bool = True

    @cached_property
    def ssh_target(self) -> str:
        return f"{self.network.user}@{self.network.host}"


def config_from_values(
    values: dict[str, object],
    *,
    host: str | None = None,
    user: str | None = None,
    port: int | None = None,
    root: Path | None = None,
    reccy_repo: str | None = None,
    recs_repo: str | None = None,
    twitcho_repo: str | None = None,
    showco_repo: str | None = None,
    lyte_repo: str | None = None,
    lyte_enabled: bool | None = None,
    lyte_daemon_config: Path | None = None,
) -> Config:
    network = table_value(values, "network")
    network_config = NetworkConfig(
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
    )
    paths = table_value(values, "paths")
    git = table_value(values, "git")
    network_values = table_value(values, "networks")
    mixers = mixer_specs(values.get("mixers", []), internal_subnet(network_values))
    result = Config(
        network=network_config,
        paths=Paths(
            root=path_value(
                root,
                paths,
                default=Path("/home") / network_config.user / "code",
            )
        ),
        networks=networks_value(network_values, mixers),
        mixers=mixers,
        twitch=twitch_value(table_value(values, "twitch")),
        lyte=lyte_value(
            table_value(values, "lyte"),
            enabled=lyte_enabled,
            daemon_config=lyte_daemon_config,
        ),
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
            lyte=git_repo("lyte", table_value(git, "lyte"), override=lyte_repo),
        ),
        accept_changed_host_key=bool_value(
            values, "accept_changed_host_key", default=True
        ),
    )
    return result


def mixer_specs(value: object, subnet: str) -> list[MixerSpec]:
    if not isinstance(value, list):
        sys.exit("ERROR: mixers must be an array of tables")
    resolved = []
    for index, mixer in enumerate(value):
        if not is_toml_table(mixer):
            resolved.append(mixer)
            continue
        ip_address = mixer.get("ip_address")
        port = mixer.get("port")
        if (ip_address is None) != (port is None):
            sys.exit(
                f"ERROR: mixers[{index}].ip_address and mixers[{index}].port "
                "must be provided together"
            )
        resolved_mixer = dict(mixer)
        if ip_address is not None:
            host = address_at_offset(
                subnet,
                ip_address_value(ip_address, f"mixers[{index}].ip_address"),
                f"mixers[{index}].ip_address",
            )
            resolved_mixer["ip_address"] = host
            for name in ("probe", "osc"):
                endpoint = resolved_mixer.get(name)
                if is_toml_table(endpoint):
                    resolved_mixer[name] = {
                        **endpoint,
                        "host": host,
                        "port": port,
                    }
        resolved.append(resolved_mixer)
    try:
        return MixerSpecs.model_validate({"mixers": resolved}).mixers
    except ValueError as error:
        sys.exit(f"ERROR: invalid mixers: {error}")


def networks_value(
    values: dict[str, object], mixers: list[MixerSpec]
) -> dict[str, dict[str, dict[str, Network]]]:
    internal = table_value(values, "internal")
    external = table_value(values, "external")
    subnet = internal_subnet(values)
    x18_mixer = next((mixer for mixer in mixers if mixer.name == "X18"), None)
    wired = {}
    if x18_mixer is not None and x18_mixer.ip_address:
        wired["x18"] = Network(
            name="x18",
            ip_address=x18_mixer.ip_address,
            subnet=subnet,
        )
    return {
        "internal": {
            "wired": wired,
            "wifi": {
                "private": network_value(
                    table_value(internal, "wifi"),
                    "networks.internal.wifi",
                    subnet,
                )
            },
        },
        "external": {
            "wifi": {
                "external": network_value(
                    table_value(external, "wifi"),
                    "networks.external.wifi",
                    "",
                )
            }
        },
    }


def network_value(values: dict[str, object], name: str, subnet: str) -> Network:
    ip_address = values.get("ip_address")
    return Network(
        name=string_value(values, "name"),
        ip_address=(
            address_at_offset(
                subnet,
                ip_address_value(ip_address, f"{name}.ip_address"),
                f"{name}.ip_address",
            )
            if ip_address is not None
            else ""
        ),
        password=string_value(values, "password"),
    )


def internal_subnet(values: dict[str, object]) -> str:
    return require_value(
        "networks.internal.subnet",
        string_value(table_value(values, "internal"), "subnet"),
    )


def ip_address_value(value: object, name: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    sys.exit(f"ERROR: {name} must be an integer")


def address_at_offset(subnet: str, offset: int, name: str) -> str:
    try:
        network = ip_network(subnet, strict=False)
    except ValueError:
        sys.exit("ERROR: networks.internal.subnet must be a valid IPv4 subnet")
    if not isinstance(network, IPv4Network):
        sys.exit("ERROR: networks.internal.subnet must be an IPv4 subnet")
    address = IPv4Address(int(network.network_address) + offset)
    if address not in network or address in (
        network.network_address,
        network.broadcast_address,
    ):
        sys.exit(f"ERROR: {name} must identify a usable address in {network}")
    return str(address)


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


def lyte_value(
    values: dict[str, object], *, enabled: bool | None, daemon_config: Path | None
) -> Lyte:
    daemon_config = daemon_config or Path(
        string_value(values, "daemon_config", default="patches/wearable-daemon.toml")
    )
    if daemon_config.is_absolute() or ".." in daemon_config.parts:
        sys.exit("ERROR: lyte.daemon_config must be relative to the Lyte checkout")
    return Lyte(
        enabled=bool_value(values, "enabled", default=False)
        if enabled is None
        else enabled,
        daemon_config=daemon_config,
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


def load_values(config_path: Path, secrets_path: Path) -> dict[str, object]:
    return merge_values(read_toml(config_path), read_toml(secrets_path))


def merge_values(
    config: dict[str, object], secrets: dict[str, object]
) -> dict[str, object]:
    result = dict(config)
    for k, v in secrets.items():
        current = result.get(k)
        if is_toml_table(v) and is_toml_table(current):
            result[k] = merge_values(current, v)
        else:
            result[k] = v
    return result


def git_repo(name: str, values: dict[str, object], *, override: str | None) -> GitRepo:
    url = override or string_value(
        values,
        "url",
        default=f"https://github.com/rec/{name}.git",
    )
    return GitRepo(
        url=require_value(f"git.{name}.url", url),
        refname=string_value(values, "refname"),
    )


def table_value(values: dict[str, object], name: str) -> dict[str, object]:
    value = values.get(name, {})
    if is_toml_table(value):
        return value
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


def path_value(value: Path | None, values: dict[str, object], *, default: Path) -> Path:
    configured = value or Path(string_value(values, "root", default=str(default)))
    path = configured.expanduser()
    if not path.is_absolute():
        sys.exit("ERROR: paths.root must be an absolute path")
    return path


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
    if is_string_list(value):
        return value
    sys.exit(f"ERROR: {name} must be a list of strings")


def is_toml_table(value: object) -> TypeIs[dict[str, object]]:
    return isinstance(value, dict) and all(isinstance(k, str) for k in value)


def is_string_list(value: object) -> TypeIs[list[str]]:
    return isinstance(value, list) and all(isinstance(i, str) for i in value)


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
