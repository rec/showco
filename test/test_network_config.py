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


def network_config(
    *,
    swap_wifi: bool = False,
    network_topology: NetworkTopology | None = None,
    twitcho_enabled: bool = False,
    private_wifi_ssid: str = "showbox",
    private_wifi_password: str = "",
    external_wifi_ssid: str = "",
    external_wifi_password: str = "",
) -> NetworkConfig:
    return NetworkConfig(
        swap_wifi=swap_wifi,
        network_topology=network_topology,
        twitcho_enabled=twitcho_enabled,
        private_wifi_ssid=private_wifi_ssid,
        private_wifi_password=private_wifi_password,
        external_wifi_ssid=external_wifi_ssid,
        external_wifi_password=external_wifi_password,
    )


if __name__ == "__main__":
    unittest.main()
