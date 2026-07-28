from __future__ import annotations

import enum
import ipaddress
import os
import shlex
import subprocess
import sys
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

import tyro
from pydantic import BaseModel

import showco

RunCommand = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
PROVISION_DIR = Path(__file__).resolve().parent / "provision"
DEFAULT_CONFIG_PATH = PROVISION_DIR / "config.toml"
DEFAULT_SECRETS_PATH = PROVISION_DIR / "secrets.toml"


class NetworkTopology(enum.StrEnum):
    PUBLIC = enum.auto()
    PRIVATE = enum.auto()
    MIXED = enum.auto()


class NetworkConfig(BaseModel, frozen=True):
    is_x18_wired: bool
    swap_wifi: bool
    network_topology: NetworkTopology | None
    twitcho_enabled: bool
    private_wifi_ssid: str
    private_wifi_password: str
    external_wifi_ssid: str
    external_wifi_password: str
    x18_ethernet_subnet: str


class WifiInterface(BaseModel, frozen=True):
    name: str


class WifiAssignment(BaseModel, frozen=True):
    primary: WifiInterface
    secondary: WifiInterface | None


class NetworkOptions(BaseModel, frozen=True):
    config: Path
    secrets: Path
    dry_run: bool


def main(argv: list[str] | None = None) -> int:
    options = tyro.cli(
        network_options,
        args=argv,
        description="Configure Raspberry Pi Wi-Fi",
    )
    values = read_toml(options.config)
    values |= read_toml(options.secrets)
    return configure_network(
        config_from_values(values),
        dry_run=options.dry_run,
    )


def network_options(
    config: Path = DEFAULT_CONFIG_PATH,
    secrets: Path = DEFAULT_SECRETS_PATH,
    dry_run: bool = False,
) -> NetworkOptions:
    return NetworkOptions(config=config, secrets=secrets, dry_run=dry_run)


def configure_network(
    config: NetworkConfig,
    *,
    dry_run: bool = False,
    run_command: RunCommand | None = None,
    output: TextIO = sys.stdout,
) -> int:
    run_command = run_command or run
    interfaces = detect_wifi_interfaces(run_command)
    assignment = assign_wifi(interfaces, config.swap_wifi)
    topology = select_topology(config, assignment.secondary is not None)
    commands = network_commands(config, assignment, topology)
    for command in commands:
        print(shell_command(command), file=output)
        if not dry_run:
            check_command_result(run_command(command))
    return 0


def read_toml(path: Path) -> dict[str, object]:
    path = path.expanduser()
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        sys.exit(f"ERROR: Cannot parse {path}: {e}")


def config_from_values(values: dict[str, object]) -> NetworkConfig:
    return NetworkConfig(
        is_x18_wired=bool_value(values, "is_x18_wired", default=True),
        swap_wifi=bool_value(values, "swap_wifi", default=False),
        network_topology=topology_value(values.get("network_topology")),
        twitcho_enabled=bool_value(values, "twitcho_enabled", default=False),
        private_wifi_ssid=string_value(
            values,
            "private_wifi_ssid",
            legacy_name="showco_pi_access_point_ssid",
            default="showbox",
        ),
        private_wifi_password=string_value(values, "private_wifi_password"),
        external_wifi_ssid=string_value(values, "external_wifi_ssid"),
        external_wifi_password=string_value(values, "external_wifi_password"),
        x18_ethernet_subnet=string_value(
            values,
            "showco_pi_x18_ethernet_subnet",
            default="10.43.0.0/24",
        ),
    )


def detect_wifi_interfaces(
    run_command: RunCommand,
) -> list[WifiInterface]:
    completed = run_command(["nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status"])
    if completed.returncode != 0:
        sys.exit(completed.stderr.strip() or "ERROR: nmcli device status failed")
    names = []
    for line in completed.stdout.splitlines():
        fields = split_nmcli_terse_fields(line)
        if len(fields) >= 2 and fields[1] == "wifi":
            names.append(fields[0])
    return [WifiInterface(name=name) for name in names]


def split_nmcli_terse_fields(line: str) -> list[str]:
    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for c in line:
        if escaped:
            current.append(c)
            escaped = False
        elif c == "\\":
            escaped = True
        elif c == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(c)
    if escaped:
        current.append("\\")
    fields.append("".join(current))
    return fields


def assign_wifi(interfaces: list[WifiInterface], swap_wifi: bool) -> WifiAssignment:
    if not interfaces:
        sys.exit("ERROR: No Wi-Fi interfaces found")
    ordered = list(interfaces)
    if swap_wifi and len(ordered) > 1:
        ordered[0], ordered[1] = ordered[1], ordered[0]
    primary = ordered[0]
    secondary = ordered[1] if len(ordered) > 1 else None
    return WifiAssignment(primary=primary, secondary=secondary)


def select_topology(config: NetworkConfig, has_second_wifi: bool) -> NetworkTopology:
    if config.network_topology is not None:
        return config.network_topology
    if not config.external_wifi_ssid:
        if config.twitcho_enabled:
            sys.exit("ERROR: external_wifi_ssid is required when twitcho is enabled")
        return NetworkTopology.PRIVATE
    if has_second_wifi:
        return NetworkTopology.MIXED
    if config.twitcho_enabled:
        return NetworkTopology.PUBLIC
    return NetworkTopology.PRIVATE


def network_commands(
    config: NetworkConfig,
    assignment: WifiAssignment,
    topology: NetworkTopology,
) -> list[list[str]]:
    if topology == NetworkTopology.MIXED and assignment.secondary is None:
        sys.exit("ERROR: mixed network topology requires a secondary Wi-Fi interface")
    if topology in (NetworkTopology.PUBLIC, NetworkTopology.MIXED):
        require_external_network(config)
    commands = []
    if config.is_x18_wired:
        commands.append(x18_ethernet_command(config))
    commands.append(["nmcli", "radio", "wifi", "on"])
    if topology == NetworkTopology.PUBLIC:
        commands.append(external_wifi_command(config, assignment.primary))
        if assignment.secondary:
            commands.append(disconnect_command(assignment.secondary))
    elif topology == NetworkTopology.PRIVATE:
        commands.append(private_wifi_command(config, assignment.primary))
        if assignment.secondary:
            commands.append(disconnect_command(assignment.secondary))
    else:
        commands.append(private_wifi_command(config, assignment.primary))
        if assignment.secondary is None:
            sys.exit(
                "ERROR: mixed network topology requires a secondary Wi-Fi interface"
            )
        commands.append(external_wifi_command(config, assignment.secondary))
    return commands


def private_wifi_command(config: NetworkConfig, interface: WifiInterface) -> list[str]:
    command = [
        "nmcli",
        "device",
        "wifi",
        "hotspot",
        "ifname",
        interface.name,
        "con-name",
        "showco-private",
        "ssid",
        config.private_wifi_ssid,
    ]
    if config.private_wifi_password:
        command.extend(["password", config.private_wifi_password])
    return command


def external_wifi_command(config: NetworkConfig, interface: WifiInterface) -> list[str]:
    command = [
        "nmcli",
        "device",
        "wifi",
        "connect",
        config.external_wifi_ssid,
    ]
    if config.external_wifi_password:
        command.extend(["password", config.external_wifi_password])
    command.extend(["ifname", interface.name, "name", "showco-external"])
    return command


def disconnect_command(interface: WifiInterface) -> list[str]:
    return ["nmcli", "device", "disconnect", interface.name]


def x18_ethernet_command(config: NetworkConfig) -> list[str]:
    connection = "showco-x18"
    interface = "eth0"
    address = x18_pi_ethernet_address(config.x18_ethernet_subnet)
    script = "\n".join(
        [
            f"nmcli connection show {shlex_quote(connection)} >/dev/null 2>&1 || "
            "nmcli connection add "
            f"type ethernet ifname {shlex_quote(interface)} "
            f"con-name {shlex_quote(connection)}",
            "nmcli connection modify "
            f"{shlex_quote(connection)} "
            f"ifname {shlex_quote(interface)} "
            "ipv4.method manual "
            f"ipv4.addresses {shlex_quote(address)} "
            "ipv6.method disabled "
            "connection.autoconnect yes",
            f"nmcli connection up {shlex_quote(connection)}",
        ]
    )
    return ["sh", "-c", script]


def x18_pi_ethernet_address(subnet: str) -> str:
    try:
        network = ipaddress.ip_network(subnet, strict=False)
    except ValueError:
        sys.exit("ERROR: showco_pi_x18_ethernet_subnet must be a valid IP subnet")
    hosts = network.hosts()
    try:
        address = next(hosts)
    except StopIteration:
        sys.exit("ERROR: showco_pi_x18_ethernet_subnet has no usable host address")
    return f"{address}/{network.prefixlen}"


def require_external_network(config: NetworkConfig) -> None:
    if not config.external_wifi_ssid:
        sys.exit("ERROR: external_wifi_ssid is required for this network topology")


def topology_value(value: object) -> NetworkTopology | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        sys.exit("ERROR: network_topology must be a string")
    try:
        return NetworkTopology(value)
    except ValueError:
        sys.exit("ERROR: network_topology must be public, private, mixed, or empty")


def bool_value(values: dict[str, object], name: str, *, default: bool) -> bool:
    value = values.get(name, default)
    if isinstance(value, bool):
        return value
    sys.exit(f"ERROR: {name} must be a boolean")


def string_value(
    values: dict[str, object],
    name: str,
    *,
    legacy_name: str | None = None,
    default: str = "",
) -> str:
    value = values.get(name)
    if value is None and legacy_name:
        value = values.get(legacy_name)
    if value is None:
        return default
    if not isinstance(value, str):
        sys.exit(f"ERROR: {name} must be a string")
    return os.path.expandvars(value)


def shell_command(command: Sequence[str]) -> str:
    return " ".join(shlex_quote(s) for s in command)


def shlex_quote(value: str) -> str:
    return shlex.quote(value)


def run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return showco.run(command, capture_output=True, check=False, text=True)


def check_command_result(completed: subprocess.CompletedProcess[str]) -> None:
    if completed.returncode == 0:
        return
    output = f"{completed.stdout}{completed.stderr}".strip()
    message = f"ERROR: command failed: {shell_command(completed.args)}"
    if output:
        message = f"{message}\n{output}"
    sys.exit(message)


def default_config_path() -> Path:
    return DEFAULT_CONFIG_PATH


def default_secrets_path() -> Path:
    return DEFAULT_SECRETS_PATH


def provision_dir() -> Path:
    return PROVISION_DIR


if __name__ == "__main__":
    sys.exit(main())
