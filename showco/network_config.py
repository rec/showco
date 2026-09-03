from __future__ import annotations

import enum
import ipaddress
import shlex
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from subprocess import CompletedProcess
from typing import Annotated, TextIO

import tyro
from pydantic import BaseModel
from reccy import subprocess

from . import machine_role
from .provision import config

RunCommand = Callable[[Sequence[str]], CompletedProcess[str]]
PROVISION_DIR = Path(__file__).resolve().parent / "provision"
DEFAULT_CONFIG_PATH = PROVISION_DIR / "config.toml"
DEFAULT_SECRETS_PATH = PROVISION_DIR / "secrets.toml"


class NetworkTopology(enum.StrEnum):
    PUBLIC = enum.auto()
    PRIVATE = enum.auto()
    MIXED = enum.auto()


PRIVATE_WIFI_CONNECTION = "showco-private"
X18_BRIDGE_CONNECTION = "showco-x18-bridge"
X18_BRIDGE_INTERFACE = "br-x18"
X18_ETHERNET_CONNECTION = "showco-x18-ethernet"
X18_ETHERNET_INTERFACE = "eth0"
X18_LEGACY_CONNECTION = "showco-x18"


class WifiInterface(BaseModel, frozen=True):
    name: str
    connected: bool = False
    connection: str = ""


class WifiAssignment(BaseModel, frozen=True):
    primary: WifiInterface
    secondary: WifiInterface | None


class NetworkConfigOptions(BaseModel, frozen=True):
    config_path: Annotated[
        Path,
        tyro.conf.arg(name="config"),
    ] = DEFAULT_CONFIG_PATH
    secrets: Path = DEFAULT_SECRETS_PATH
    dry_run: bool = False


def main(argv: list[str] | None = None) -> int:
    machine_role.require_target_machine("showco run network-config")
    options = tyro.cli(
        NetworkConfigOptions, args=argv, description="Configure Raspberry Pi Wi-Fi"
    )
    return configure_network_from_paths(options)


def configure_network_from_paths(options: NetworkConfigOptions) -> int:
    values = config.load_values(options.config_path, options.secrets)
    return configure_network(config.config_from_values(values), dry_run=options.dry_run)


def configure_network(
    provision_config: config.Config,
    *,
    dry_run: bool = False,
    run_command: RunCommand | None = None,
    output: TextIO = sys.stdout,
) -> int:
    run_command = run_command or run
    interfaces = detect_wifi_interfaces(run_command)
    assignment = assign_wifi(interfaces, provision_config.network.swap_wifi)
    topology = select_topology(provision_config, assignment.secondary is not None)
    commands = network_commands(provision_config, assignment, topology)
    for command in commands:
        print(shell_command(command), file=output)
        if not dry_run:
            check_command_result(run_command(command))
    return 0


def detect_wifi_interfaces(
    run_command: RunCommand,
) -> list[WifiInterface]:
    completed = run_command(
        ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"]
    )
    if completed.returncode != 0:
        sys.exit(completed.stderr.strip() or "ERROR: nmcli device status failed")
    return wifi_interfaces_from_status(completed.stdout)


def wifi_interfaces_from_status(status: str) -> list[WifiInterface]:
    interfaces = []
    for line in status.splitlines():
        fields = split_nmcli_terse_fields(line)
        if len(fields) >= 2 and fields[1] == "wifi":
            interfaces.append(
                WifiInterface(
                    name=fields[0],
                    connected=len(fields) >= 3 and fields[2].startswith("connected"),
                    connection=fields[3] if len(fields) >= 4 else "",
                )
            )
    return interfaces


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
    elif private_wifi := next(
        (
            i
            for i in ordered
            if not i.connected or i.connection == PRIVATE_WIFI_CONNECTION
        ),
        None,
    ):
        ordered.remove(private_wifi)
        ordered.insert(0, private_wifi)
    primary = ordered[0]
    secondary = ordered[1] if len(ordered) > 1 else None
    return WifiAssignment(primary=primary, secondary=secondary)


def select_topology(
    provision_config: config.Config, has_second_wifi: bool
) -> NetworkTopology:
    topology = topology_value(provision_config.network.topology)
    if topology is not None:
        return topology
    if not config.external_wifi(provision_config).name:
        if provision_config.twitch.enabled:
            sys.exit(
                "ERROR: networks.external.wifi.external.name is required "
                "when twitch.enabled is true"
            )
        return NetworkTopology.PRIVATE
    if has_second_wifi:
        return NetworkTopology.MIXED
    if provision_config.twitch.enabled:
        return NetworkTopology.PUBLIC
    return NetworkTopology.PRIVATE


def network_commands(
    provision_config: config.Config,
    assignment: WifiAssignment,
    topology: NetworkTopology,
) -> list[list[str]]:
    if topology == NetworkTopology.MIXED and assignment.secondary is None:
        sys.exit("ERROR: mixed network topology requires a secondary Wi-Fi interface")
    if (
        topology != NetworkTopology.PUBLIC
        and assignment.primary.connected
        and assignment.primary.connection != PRIVATE_WIFI_CONNECTION
    ):
        sys.exit(
            "ERROR: no unconnected Wi-Fi interface is available for the private hotspot"
        )
    if topology in (NetworkTopology.PUBLIC, NetworkTopology.MIXED):
        require_external_network(provision_config)
    commands = []
    x18_network = config.x18(provision_config)
    if x18_network is not None and topology == NetworkTopology.PUBLIC:
        commands.append(x18_ethernet_command(provision_config))
    if topology == NetworkTopology.PUBLIC:
        return commands
    if x18_network is not None:
        commands.append(x18_bridge_command(provision_config, assignment.primary))
        return commands
    commands = [private_wifi_command(provision_config, assignment.primary)]
    if config.internal_wifi(provision_config).password:
        commands.extend(
            [
                nmcli_command(
                    "connection",
                    "modify",
                    PRIVATE_WIFI_CONNECTION,
                    *private_wifi_security_arguments(),
                ),
                nmcli_command("connection", "up", PRIVATE_WIFI_CONNECTION),
            ]
        )
    commands.append(
        nmcli_command(
            "connection",
            "modify",
            PRIVATE_WIFI_CONNECTION,
            "connection.autoconnect",
            "yes",
        )
    )
    return commands


def private_wifi_command(
    provision_config: config.Config, interface: WifiInterface
) -> list[str]:
    network = config.internal_wifi(provision_config)
    command = [
        *nmcli_command("device", "wifi", "hotspot"),
        "ifname",
        interface.name,
        "con-name",
        PRIVATE_WIFI_CONNECTION,
        "ssid",
        config.string_or_default(network.name, "showbox"),
    ]
    if network.password:
        command.extend(["password", network.password])
    return command


def private_wifi_security_arguments() -> list[str]:
    return [
        "802-11-wireless-security.key-mgmt",
        "wpa-psk",
        "802-11-wireless-security.proto",
        "rsn",
        "802-11-wireless-security.pairwise",
        "ccmp",
        "802-11-wireless-security.group",
        "ccmp",
        "802-11-wireless-security.pmf",
        "disable",
    ]


def nmcli_command(*arguments: str) -> list[str]:
    return ["sudo", "nmcli", *arguments]


def x18_ethernet_command(provision_config: config.Config) -> list[str]:
    x18_network = config.x18(provision_config)
    if x18_network is None:
        sys.exit("ERROR: networks.internal.wired.x18 is required")
    connection = X18_LEGACY_CONNECTION
    interface = X18_ETHERNET_INTERFACE
    address = x18_pi_ethernet_address(
        config.string_or_default(x18_network.subnet, "10.43.0.0/24")
    )
    script = "\n".join(
        [
            f"nmcli connection show {shlex_quote(connection)} >/dev/null 2>&1 || "
            "sudo nmcli connection add "
            f"type ethernet ifname {shlex_quote(interface)} "
            f"con-name {shlex_quote(connection)}",
            "sudo nmcli connection modify "
            f"{shlex_quote(connection)} "
            f"ifname {shlex_quote(interface)} "
            "ipv4.method manual "
            f"ipv4.addresses {shlex_quote(address)} "
            "ipv6.method disabled "
            "connection.autoconnect yes",
            f"sudo nmcli connection up {shlex_quote(connection)}",
        ]
    )
    return ["sh", "-c", script]


def x18_bridge_command(
    provision_config: config.Config, wifi_interface: WifiInterface
) -> list[str]:
    network = config.internal_wifi(provision_config)
    bridge_address = x18_bridge_address(provision_config)
    bridge_connection = shlex_quote(X18_BRIDGE_CONNECTION)
    bridge_interface = shlex_quote(X18_BRIDGE_INTERFACE)
    ethernet_connection = shlex_quote(X18_ETHERNET_CONNECTION)
    ethernet_interface = shlex_quote(X18_ETHERNET_INTERFACE)
    legacy_connection = shlex_quote(X18_LEGACY_CONNECTION)
    wifi_connection = shlex_quote(PRIVATE_WIFI_CONNECTION)
    wifi_name = shlex_quote(wifi_interface.name)
    wifi_ssid = shlex_quote(config.string_or_default(network.name, "showbox"))
    script = [
        "set -e",
        "rollback() {",
        "  status=$?",
        "  if nmcli connection show showco-private-rollback >/dev/null 2>&1; then "
        "sudo nmcli connection up showco-private-rollback || true; fi",
        "  exit $status",
        "}",
        f"if nmcli connection show {wifi_connection} >/dev/null 2>&1; then "
        "sudo nmcli connection delete showco-private-rollback "
        ">/dev/null 2>&1 || true; "
        f"sudo nmcli connection clone {wifi_connection} showco-private-rollback; fi",
        "trap rollback ERR",
        f"if ! nmcli connection show {bridge_connection} >/dev/null 2>&1; then "
        "sudo nmcli connection add "
        f"type bridge ifname {bridge_interface} con-name {bridge_connection}; fi",
        "sudo nmcli connection modify "
        f"{bridge_connection} ifname {bridge_interface} "
        "ipv4.method shared "
        f"ipv4.addresses {shlex_quote(bridge_address)} "
        "ipv6.method disabled bridge.stp no connection.autoconnect yes",
        f"if ! nmcli connection show {ethernet_connection} >/dev/null 2>&1; then "
        "sudo nmcli connection add "
        f"type ethernet ifname {ethernet_interface} con-name {ethernet_connection} "
        f"controller {bridge_interface}; fi",
        "sudo nmcli connection modify "
        f"{ethernet_connection} ifname {ethernet_interface} "
        f"connection.controller {bridge_interface} "
        "connection.autoconnect yes",
        f"if nmcli connection show {wifi_connection} >/dev/null 2>&1; then "
        f"sudo nmcli connection delete {wifi_connection}; fi",
        "sudo nmcli connection add "
        f"type wifi ifname {wifi_name} con-name {wifi_connection} "
        f"controller {bridge_interface} ssid {wifi_ssid}",
        "sudo nmcli connection modify "
        f"{wifi_connection} ifname {wifi_name} "
        f"connection.controller {bridge_interface} "
        "802-11-wireless.mode ap "
        "connection.autoconnect yes",
    ]
    if network.password:
        script.append(
            "sudo nmcli connection modify "
            f"{wifi_connection} {' '.join(private_wifi_security_arguments())} "
            f"802-11-wireless-security.psk {shlex_quote(network.password)}"
        )
    script.extend(
        [
            f"sudo nmcli connection up {bridge_connection}",
            f"sudo nmcli connection up {ethernet_connection}",
            f"sudo nmcli connection up {wifi_connection}",
            "trap - ERR",
            "sudo nmcli connection delete showco-private-rollback "
            ">/dev/null 2>&1 || true",
            f"if nmcli connection show {legacy_connection} >/dev/null 2>&1; then "
            f"sudo nmcli connection delete {legacy_connection}; fi",
        ]
    )
    return ["bash", "-c", "\n".join(script)]


def x18_bridge_address(provision_config: config.Config) -> str:
    x18_network = config.x18(provision_config)
    if x18_network is None:
        sys.exit("ERROR: networks.internal.wired.x18 is required")
    subnet = config.string_or_default(x18_network.subnet, "10.43.0.0/24")
    network = ip_network(subnet)
    address = config.string_or_default(
        config.internal_wifi(provision_config).ip_address,
        x18_pi_ethernet_address(subnet),
    )
    try:
        host = ipaddress.ip_interface(address).ip
    except ValueError:
        sys.exit(
            "ERROR: networks.internal.wifi.private.ip_address must be an IP address"
        )
    if host not in network:
        sys.exit(
            "ERROR: networks.internal.wifi.private.ip_address must be within "
            "networks.internal.wired.x18.subnet"
        )
    return f"{host}/{network.prefixlen}"


def x18_pi_ethernet_address(subnet: str) -> str:
    network = ip_network(subnet)
    hosts = network.hosts()
    try:
        address = next(hosts)
    except StopIteration:
        sys.exit("ERROR: networks.internal.wired.x18.subnet has no usable host address")
    return f"{address}/{network.prefixlen}"


def ip_network(subnet: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    try:
        return ipaddress.ip_network(subnet, strict=False)
    except ValueError:
        sys.exit("ERROR: networks.internal.wired.x18.subnet must be a valid IP subnet")


def require_external_network(provision_config: config.Config) -> None:
    if not config.external_wifi(provision_config).name:
        sys.exit("ERROR: networks.external.wifi.external.name is required")


def topology_value(value: object) -> NetworkTopology | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        sys.exit("ERROR: network.topology must be a string")
    try:
        return NetworkTopology(value)
    except ValueError:
        sys.exit("ERROR: network.topology must be public, private, mixed, or empty")


def shell_command(command: Sequence[str]) -> str:
    return " ".join(shlex_quote(s) for s in command)


def shlex_quote(value: str) -> str:
    return shlex.quote(value)


def run(command: Sequence[str]) -> CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, check=False, text=True)


def check_command_result(completed: CompletedProcess[str]) -> None:
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
