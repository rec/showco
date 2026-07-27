from __future__ import annotations

import subprocess
import unittest
from collections.abc import Sequence
from io import StringIO

from showco.network_config import (
    NetworkConfig,
    NetworkTopology,
    WifiInterface,
    assign_wifi,
    configure_network,
    network_commands,
    select_topology,
    x18_pi_ethernet_address,
)


class NetworkConfigTests(unittest.TestCase):
    def test_default_topology_without_external_network_is_private(self) -> None:
        config = network_config(external_wifi_ssid="")

        self.assertEqual(select_topology(config, True), NetworkTopology.PRIVATE)

    def test_default_topology_with_second_wifi_and_external_network_is_mixed(
        self,
    ) -> None:
        config = network_config(external_wifi_ssid="Venue")

        self.assertEqual(select_topology(config, True), NetworkTopology.MIXED)

    def test_default_topology_without_second_wifi_and_twitcho_is_private(self) -> None:
        config = network_config(external_wifi_ssid="Venue")

        self.assertEqual(select_topology(config, False), NetworkTopology.PRIVATE)

    def test_default_topology_with_twitcho_and_one_wifi_is_public(self) -> None:
        config = network_config(external_wifi_ssid="Venue", twitcho_enabled=True)

        self.assertEqual(select_topology(config, False), NetworkTopology.PUBLIC)

    def test_twitcho_without_external_network_is_error(self) -> None:
        config = network_config(external_wifi_ssid="", twitcho_enabled=True)

        with self.assertRaises(SystemExit):
            select_topology(config, False)

    def test_swap_wifi_makes_second_interface_primary(self) -> None:
        assignment = assign_wifi(
            [
                WifiInterface("wlan0"),
                WifiInterface("wlan1"),
            ],
            swap_wifi=True,
        )

        self.assertEqual(assignment.primary.name, "wlan1")
        self.assertEqual(
            assignment.secondary.name if assignment.secondary else "", "wlan0"
        )

    def test_private_topology_starts_access_point_and_disconnects_secondary(
        self,
    ) -> None:
        commands = network_commands(
            network_config(network_topology=NetworkTopology.PRIVATE),
            assign_wifi(
                [
                    WifiInterface("wlan0"),
                    WifiInterface("wlan1"),
                ],
                swap_wifi=False,
            ),
            NetworkTopology.PRIVATE,
        )

        self.assertEqual(
            commands,
            [
                [
                    "sh",
                    "-c",
                    "\n".join(
                        [
                            "nmcli connection show showco-x18 >/dev/null 2>&1 || "
                            "nmcli connection add type ethernet ifname eth0 "
                            "con-name showco-x18",
                            "nmcli connection modify showco-x18 ifname eth0 "
                            "ipv4.method manual ipv4.addresses 10.43.0.1/24 "
                            "ipv6.method disabled connection.autoconnect yes",
                            "nmcli connection up showco-x18",
                        ]
                    ),
                ],
                ["nmcli", "radio", "wifi", "on"],
                [
                    "nmcli",
                    "device",
                    "wifi",
                    "hotspot",
                    "ifname",
                    "wlan0",
                    "con-name",
                    "showco-private",
                    "ssid",
                    "showbox",
                ],
                ["nmcli", "device", "disconnect", "wlan1"],
            ],
        )

    def test_dry_run_prints_commands_without_running_configuration(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            return subprocess.CompletedProcess(
                command,
                0,
                "wlan0:wifi:connected\nwlan1:wifi:disconnected\n",
                "",
            )

        output = StringIO()

        configure_network(
            network_config(), dry_run=True, run_command=run_command, output=output
        )

        self.assertEqual(
            commands,
            [["nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status"]],
        )
        self.assertIn("nmcli radio wifi on", output.getvalue())
        self.assertIn("nmcli connection up showco-x18", output.getvalue())

    def test_configuration_stops_when_command_fails(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command == ["nmcli", "radio", "wifi", "on"]:
                return subprocess.CompletedProcess(command, 10, "", "radio failed")
            return subprocess.CompletedProcess(
                command,
                0,
                "wlan0:wifi:connected\n",
                "",
            )

        with self.assertRaisesRegex(SystemExit, "radio failed"):
            configure_network(
                network_config(is_x18_wired=False),
                dry_run=False,
                run_command=run_command,
            )

        self.assertEqual(
            commands,
            [
                ["nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status"],
                ["nmcli", "radio", "wifi", "on"],
            ],
        )

    def test_x18_wired_can_be_disabled(self) -> None:
        commands = network_commands(
            network_config(
                is_x18_wired=False,
                network_topology=NetworkTopology.PRIVATE,
            ),
            assign_wifi([WifiInterface("wlan0")], swap_wifi=False),
            NetworkTopology.PRIVATE,
        )

        self.assertEqual(commands[0], ["nmcli", "radio", "wifi", "on"])

    def test_x18_pi_ethernet_address_uses_first_subnet_host(self) -> None:
        self.assertEqual(
            x18_pi_ethernet_address("10.43.0.0/24"),
            "10.43.0.1/24",
        )


def network_config(
    *,
    is_x18_wired: bool = True,
    swap_wifi: bool = False,
    network_topology: NetworkTopology | None = None,
    twitcho_enabled: bool = False,
    private_wifi_ssid: str = "showbox",
    private_wifi_password: str = "",
    external_wifi_ssid: str = "",
    external_wifi_password: str = "",
    x18_ethernet_subnet: str = "10.43.0.0/24",
) -> NetworkConfig:
    return NetworkConfig(
        is_x18_wired=is_x18_wired,
        swap_wifi=swap_wifi,
        network_topology=network_topology,
        twitcho_enabled=twitcho_enabled,
        private_wifi_ssid=private_wifi_ssid,
        private_wifi_password=private_wifi_password,
        external_wifi_ssid=external_wifi_ssid,
        external_wifi_password=external_wifi_password,
        x18_ethernet_subnet=x18_ethernet_subnet,
    )


if __name__ == "__main__":
    unittest.main()
