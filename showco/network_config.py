from __future__ import annotations

import argparse
import enum
import os
import shlex
import subprocess
import sys
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

RunCommand = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


class NetworkTopology(enum.StrEnum):
    PUBLIC = enum.auto()
    PRIVATE = enum.auto()
    MIXED = enum.auto()


@dataclass(frozen=True)
class NetworkConfig:
    swap_wifi: bool
    network_topology: NetworkTopology | None
    twitcho_enabled: bool
    private_wifi_ssid: str
    private_wifi_password: str
    external_wifi_ssid: str
    external_wifi_password: str


@dataclass(frozen=True)
class WifiInterface:
    name: str
    usb: bool = False


@dataclass(frozen=True)
class WifiAssignment:
    primary: WifiInterface
    secondary: WifiInterface | None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Configure Raspberry Pi Wi-Fi")
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config_path(),
        help="default: scripts/config.toml",
    )
    parser.add_argument(
        "--secrets",
        type=Path,
        default=default_secrets_path(),
        help="default: scripts/secrets.toml",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print NetworkManager commands without running them",
    )
    args = parser.parse_args(argv)
    values = read_toml(args.config)
    values |= read_toml(args.secrets)
    return configure_network(
        config_from_values(values),
        dry_run=args.dry_run,
    )


def configure_network(
    config: NetworkConfig,
    *,
    dry_run: bool = False,
    run_command: RunCommand | None = None,
    sys_class_net: Path = Path("/sys/class/net"),
    output: TextIO = sys.stdout,
) -> int:
    run_command = run_command or run
    interfaces = detect_wifi_interfaces(run_command, sys_class_net)
    assignment = assign_wifi(interfaces, config.swap_wifi)
    topology = select_topology(config, any(i.usb for i in interfaces))
    commands = network_commands(config, assignment, topology)
    for command in commands:
        print(shell_command(command), file=output)
        if not dry_run:
            run_command(command)
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
        swap_wifi=bool_value(values, "swap_wifi", default=False),
        network_topology=topology_value(values.get("network_topology")),
        twitcho_enabled=bool_value(values, "twitcho_enabled", default=False),
        private_wifi_ssid=string_value(
            values,
            "private_wifi_ssid",
            legacy_name="SHOWCO_PI_ACCESS_POINT_SSID",
            default="showbox",
        ),
        private_wifi_password=string_value(values, "private_wifi_password"),
        external_wifi_ssid=string_value(values, "external_wifi_ssid"),
        external_wifi_password=string_value(values, "external_wifi_password"),
    )


def detect_wifi_interfaces(
    run_command: RunCommand,
    sys_class_net: Path = Path("/sys/class/net"),
) -> list[WifiInterface]:
    completed = run_command(["nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status"])
    if completed.returncode != 0:
        sys.exit(completed.stderr.strip() or "ERROR: nmcli device status failed")
    names = []
    for line in completed.stdout.splitlines():
        fields = line.split(":")
        if len(fields) >= 2 and fields[1] == "wifi":
            names.append(fields[0])
    return [
        WifiInterface(name, usb=is_usb_interface(name, sys_class_net)) for name in names
    ]


def assign_wifi(interfaces: list[WifiInterface], swap_wifi: bool) -> WifiAssignment:
    if not interfaces:
        sys.exit("ERROR: No Wi-Fi interfaces found")
    usb = [i for i in interfaces if i.usb]
    internal = [i for i in interfaces if not i.usb]
    primary = usb[0] if swap_wifi and usb else interfaces[0]
    remaining = [i for i in interfaces if i.name != primary.name]
    secondary = remaining[0] if remaining else None
    if not swap_wifi and internal:
        primary = internal[0]
        remaining = [i for i in interfaces if i.name != primary.name]
        secondary = remaining[0] if remaining else None
    return WifiAssignment(primary, secondary)


def select_topology(config: NetworkConfig, has_usb_wifi: bool) -> NetworkTopology:
    if config.network_topology is not None:
        return config.network_topology
    if not config.external_wifi_ssid:
        if config.twitcho_enabled:
            sys.exit("ERROR: external_wifi_ssid is required when twitcho is enabled")
        return NetworkTopology.PRIVATE
    if has_usb_wifi:
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
    commands = [["nmcli", "radio", "wifi", "on"]]
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


def is_usb_interface(name: str, sys_class_net: Path) -> bool:
    device = sys_class_net / name / "device"
    try:
        return "/usb" in str(device.resolve())
    except OSError:
        return False


def shell_command(command: Sequence[str]) -> str:
    return " ".join(shlex_quote(s) for s in command)


def shlex_quote(value: str) -> str:
    return shlex.quote(value)


def run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, check=False, text=True)


def default_config_path() -> Path:
    return repo_root() / "scripts/config.toml"


def default_secrets_path() -> Path:
    return repo_root() / "scripts/secrets.toml"


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


if __name__ == "__main__":
    sys.exit(main())
