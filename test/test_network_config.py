from __future__ import annotations

import subprocess
import unittest
from collections.abc import Sequence
from io import StringIO

import tyro

from showco import network_config
from showco.provision.config import Config, config_from_values


class NetworkConfigTests(unittest.TestCase):
    def test_network_config_options_accept_existing_flags(self) -> None:
        options = tyro.cli(
            network_config.NetworkConfigOptions,
            args=[
                "--config",
                "/config.toml",
                "--secrets",
                "/secrets.toml",
                "--dry-run",
            ],
        )

        self.assertEqual(str(options.config_path), "/config.toml")
        self.assertEqual(str(options.secrets), "/secrets.toml")
        self.assertTrue(options.dry_run)

    def test_default_topology_without_external_network_is_private(self) -> None:
        config = make_network_config(external_wifi_name="")

        self.assertEqual(
            network_config.select_topology(config, True),
            network_config.NetworkTopology.PRIVATE,
        )

    def test_default_topology_with_second_wifi_and_external_network_is_mixed(
        self,
    ) -> None:
        config = make_network_config(external_wifi_name="Venue")

        self.assertEqual(
            network_config.select_topology(config, True),
            network_config.NetworkTopology.MIXED,
        )

    def test_default_topology_without_second_wifi_and_twitcho_is_private(self) -> None:
        config = make_network_config(external_wifi_name="Venue")

        self.assertEqual(
            network_config.select_topology(config, False),
            network_config.NetworkTopology.PRIVATE,
        )

    def test_default_topology_with_twitcho_and_one_wifi_is_public(self) -> None:
        config = make_network_config(external_wifi_name="Venue", twitch_enabled=True)

        self.assertEqual(
            network_config.select_topology(config, False),
            network_config.NetworkTopology.PUBLIC,
        )

    def test_twitcho_without_external_network_is_error(self) -> None:
        config = make_network_config(external_wifi_name="", twitch_enabled=True)

        with self.assertRaises(SystemExit):
            network_config.select_topology(config, False)

    def test_swap_wifi_makes_second_interface_primary(self) -> None:
        assignment = network_config.assign_wifi(
            [
                network_config.WifiInterface(name="wlan0"),
                network_config.WifiInterface(name="wlan1"),
            ],
            swap_wifi=True,
        )

        self.assertEqual(assignment.primary.name, "wlan1")
        self.assertEqual(
            assignment.secondary.name if assignment.secondary else "", "wlan0"
        )

    def test_unconnected_wifi_is_preferred_for_private_hotspot(self) -> None:
        assignment = network_config.assign_wifi(
            [
                network_config.WifiInterface(name="wlan0"),
                network_config.WifiInterface(name="wlan1", connected=True),
            ],
            swap_wifi=False,
        )

        self.assertEqual(assignment.primary.name, "wlan0")
        self.assertEqual(
            assignment.secondary.name if assignment.secondary else "", "wlan1"
        )

    def test_existing_private_hotspot_is_reused(self) -> None:
        assignment = network_config.assign_wifi(
            [
                network_config.WifiInterface(
                    name="wlan0", connected=True, connection="Livebox"
                ),
                network_config.WifiInterface(
                    name="wlan1",
                    connected=True,
                    connection=network_config.PRIVATE_WIFI_CONNECTION,
                ),
            ],
            swap_wifi=False,
        )

        self.assertEqual(assignment.primary.name, "wlan1")

    def test_status_parser_marks_connected_wifi_interfaces(self) -> None:
        interfaces = network_config.wifi_interfaces_from_status(
            "wlan0:wifi:disconnected:\nwlan1:wifi:connected:showco-private\n"
        )

        self.assertEqual(
            interfaces,
            [
                network_config.WifiInterface(name="wlan0"),
                network_config.WifiInterface(
                    name="wlan1",
                    connected=True,
                    connection="showco-private",
                ),
            ],
        )

    def test_private_hotspot_rejects_only_connected_wifi_interfaces(self) -> None:
        config = make_network_config(topology=network_config.NetworkTopology.PRIVATE)
        assignment = network_config.assign_wifi(
            [network_config.WifiInterface(name="wlan0", connected=True)],
            swap_wifi=False,
        )

        with self.assertRaisesRegex(SystemExit, "no unconnected Wi-Fi interface"):
            network_config.network_commands(
                config, assignment, network_config.NetworkTopology.PRIVATE
            )

    def test_swap_cannot_select_connected_wifi_for_private_hotspot(self) -> None:
        config = make_network_config(topology=network_config.NetworkTopology.PRIVATE)
        assignment = network_config.assign_wifi(
            [
                network_config.WifiInterface(name="wlan0"),
                network_config.WifiInterface(name="wlan1", connected=True),
            ],
            swap_wifi=True,
        )

        with self.assertRaisesRegex(SystemExit, "no unconnected Wi-Fi interface"):
            network_config.network_commands(
                config, assignment, network_config.NetworkTopology.PRIVATE
            )

    def test_x18_bridge_includes_ethernet_and_wifi_ports(self) -> None:
        commands = network_config.network_commands(
            make_network_config(
                topology=network_config.NetworkTopology.PRIVATE,
                private_wifi_password="private password",
            ),
            network_config.assign_wifi(
                [
                    network_config.WifiInterface(name="wlan0"),
                    network_config.WifiInterface(name="wlan1"),
                ],
                swap_wifi=False,
            ),
            network_config.NetworkTopology.PRIVATE,
        )

        self.assertEqual(commands[0][:2], ["bash", "-c"])
        script = commands[0][2]
        self.assertIn("trap rollback ERR", script)
        self.assertIn("showco-private-rollback", script)
        self.assertIn(
            "802-11-wireless-security.key-mgmt wpa-psk "
            "802-11-wireless-security.proto rsn "
            "802-11-wireless-security.pairwise ccmp "
            "802-11-wireless-security.group ccmp "
            "802-11-wireless-security.pmf disable",
            script,
        )
        self.assertLess(
            script.index("\nsudo nmcli connection up showco-private\n"),
            script.index("sudo nmcli connection delete showco-x18"),
        )

    def test_mixed_topology_leaves_connected_external_wifi_unchanged(self) -> None:
        commands = network_config.network_commands(
            make_network_config(
                x18=False,
                external_wifi_name="Livebox-F13E",
                topology=network_config.NetworkTopology.MIXED,
                private_wifi_password="private password",
            ),
            network_config.assign_wifi(
                [
                    network_config.WifiInterface(name="wlan0"),
                    network_config.WifiInterface(name="wlan1", connected=True),
                ],
                swap_wifi=False,
            ),
            network_config.NetworkTopology.MIXED,
        )

        self.assertEqual(
            commands,
            [
                [
                    "sudo",
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
                    "password",
                    "private password",
                ],
                [
                    "sudo",
                    "nmcli",
                    "connection",
                    "modify",
                    "showco-private",
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
                ],
                [
                    "sudo",
                    "nmcli",
                    "connection",
                    "up",
                    "showco-private",
                ],
                [
                    "sudo",
                    "nmcli",
                    "connection",
                    "modify",
                    "showco-private",
                    "connection.autoconnect",
                    "yes",
                ],
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

        network_config.configure_network(
            make_network_config(), dry_run=True, run_command=run_command, output=output
        )

        self.assertEqual(
            commands,
            [
                [
                    "nmcli",
                    "-t",
                    "-f",
                    "DEVICE,TYPE,STATE,CONNECTION",
                    "device",
                    "status",
                ]
            ],
        )
        self.assertIn("ifname wlan1", output.getvalue())
        self.assertIn("sudo nmcli connection up showco-x18", output.getvalue())

    def test_configuration_stops_when_command_fails(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            if command[:5] == ["sudo", "nmcli", "device", "wifi", "hotspot"]:
                return subprocess.CompletedProcess(command, 10, "", "hotspot failed")
            return subprocess.CompletedProcess(
                command,
                0,
                "wlan0:wifi:disconnected\n",
                "",
            )

        with self.assertRaisesRegex(SystemExit, "hotspot failed"):
            network_config.configure_network(
                make_network_config(x18=False),
                dry_run=False,
                run_command=run_command,
            )

        self.assertEqual(
            commands,
            [
                [
                    "nmcli",
                    "-t",
                    "-f",
                    "DEVICE,TYPE,STATE,CONNECTION",
                    "device",
                    "status",
                ],
                [
                    "sudo",
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
            ],
        )

    def test_detects_wifi_interfaces_with_escaped_nmcli_delimiters(self) -> None:
        commands: list[list[str]] = []

        def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(list(command))
            return subprocess.CompletedProcess(
                command,
                0,
                "wl\\:an0:wifi:disconnected\neth0:ethernet:connected\n",
                "",
            )

        output = StringIO()

        network_config.configure_network(
            make_network_config(x18=False),
            dry_run=True,
            run_command=run_command,
            output=output,
        )

        self.assertIn("ifname wl:an0", output.getvalue())

    def test_x18_can_be_disabled(self) -> None:
        commands = network_config.network_commands(
            make_network_config(
                x18=False,
                topology=network_config.NetworkTopology.PRIVATE,
                private_wifi_password="private password",
            ),
            network_config.assign_wifi(
                [network_config.WifiInterface(name="wlan0")], swap_wifi=False
            ),
            network_config.NetworkTopology.PRIVATE,
        )

        self.assertEqual(
            commands[0],
            [
                "sudo",
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
                "password",
                "private password",
            ],
        )
        self.assertEqual(
            commands[1],
            [
                "sudo",
                "nmcli",
                "connection",
                "modify",
                "showco-private",
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
            ],
        )

    def test_x18_pi_ethernet_address_uses_first_subnet_host(self) -> None:
        self.assertEqual(
            network_config.x18_pi_ethernet_address("10.43.0.0/24"),
            "10.43.0.1/24",
        )

    def test_x18_bridge_address_uses_private_wifi_address(self) -> None:
        config = make_network_config(private_wifi_ip_address="10.43.0.1")

        self.assertEqual(network_config.x18_bridge_address(config), "10.43.0.1/24")

    def test_x18_bridge_address_rejects_other_subnet(self) -> None:
        config = make_network_config(private_wifi_ip_address="10.42.0.1")

        with self.assertRaisesRegex(SystemExit, "must be within"):
            network_config.x18_bridge_address(config)

    def test_config_reads_enabled_from_twitch_table(self) -> None:
        config = config_from_values(
            {
                "network": {"host": "recs-stage.local", "user": "tom"},
                "networks": {
                    "internal": {
                        "wifi": {"private": {"name": "showbox"}},
                    },
                },
                "twitch": {"enabled": True},
                "git": {
                    "reccy": {"url": "https://github.com/rec/reccy.git"},
                    "recs": {"url": "https://github.com/rec/recs.git"},
                    "twitcho": {"url": "https://github.com/rec/twitcho.git"},
                    "lyte": {"url": "https://github.com/rec/lyte.git"},
                    "showco": {"url": "https://github.com/rec/showco.git"},
                },
            }
        )

        self.assertTrue(config.twitch.enabled)


def make_network_config(
    *,
    x18: bool = True,
    swap_wifi: bool = False,
    topology: network_config.NetworkTopology | None = None,
    twitch_enabled: bool = False,
    private_wifi_name: str = "showbox",
    private_wifi_ip_address: str = "",
    private_wifi_password: str = "",
    external_wifi_name: str = "",
    external_wifi_password: str = "",
    x18_subnet: str = "10.43.0.0/24",
) -> Config:
    wired: dict[str, object] = {}
    if x18:
        wired["x18"] = {"name": "x18", "subnet": x18_subnet}
    return config_from_values(
        {
            "network": {
                "host": "recs-stage.local",
                "user": "tom",
                "swap_wifi": swap_wifi,
                "topology": topology.value if topology is not None else "",
            },
            "networks": {
                "internal": {
                    "wired": wired,
                    "wifi": {
                        "private": {
                            "name": private_wifi_name,
                            "ip_address": private_wifi_ip_address,
                            "password": private_wifi_password,
                        },
                    },
                },
                "external": {
                    "wifi": {
                        "external": {
                            "name": external_wifi_name,
                            "password": external_wifi_password,
                        },
                    },
                },
            },
            "twitch": {"enabled": twitch_enabled},
            "git": {
                "reccy": {"url": "https://github.com/rec/reccy.git"},
                "recs": {"url": "https://github.com/rec/recs.git"},
                "twitcho": {"url": "https://github.com/rec/twitcho.git"},
                "lyte": {"url": "https://github.com/rec/lyte.git"},
                "showco": {"url": "https://github.com/rec/showco.git"},
            },
        }
    )


if __name__ == "__main__":
    unittest.main()
