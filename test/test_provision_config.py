from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from provision_helpers import make_config, networks, values

from showco.provision import config, provision


class ProvisionConfigTests(unittest.TestCase):
    def test_lyte_defaults_to_disabled(self) -> None:
        parsed = make_config(values())

        self.assertFalse(parsed.lyte.enabled)
        self.assertEqual(
            parsed.lyte.installation_config,
            Path('patches/showco-installation.toml'),
        )

    def test_argon_one_defaults_to_enabled(self) -> None:
        parsed = make_config(values())

        self.assertTrue(parsed.argon_one)

    def test_argon_one_can_be_disabled(self) -> None:
        parsed = make_config(values(argon_one=False))

        self.assertFalse(parsed.argon_one)

    def test_git_repositories_default_to_github(self) -> None:
        configuration = values()
        del configuration['git']

        parsed = make_config(configuration)

        self.assertEqual(parsed.git.reccy.url, 'https://github.com/rec/reccy.git')
        self.assertEqual(parsed.git.recs.url, 'https://github.com/rec/recs.git')
        self.assertEqual(parsed.git.streamo.url, 'https://github.com/rec/streamo.git')
        self.assertEqual(parsed.git.showco.url, 'https://github.com/rec/showco.git')
        self.assertEqual(parsed.git.lyte.url, 'https://github.com/rec/lyte.git')

    def test_lyte_installation_config_must_be_within_lyte_checkout(self) -> None:
        with self.assertRaisesRegex(SystemExit, 'must be relative'):
            make_config(values(lyte={'installation_config': '/etc/lyte.toml'}))

    def test_wired_x18_uses_configured_x18_host(self) -> None:
        config = make_config(
            values(),
        )

        self.assertEqual(
            config.networks['internal']['wired']['x18'].ip_address,
            '10.0.0.18',
        )
        x18 = next(mixer for mixer in config.mixers if mixer.name == 'X18')
        self.assertEqual(x18.probe.host if x18.probe else '', '10.0.0.18')
        self.assertEqual(x18.osc.port if x18.osc else 0, 10024)

    def test_duplicate_mixer_names_are_rejected(self) -> None:
        with self.assertRaisesRegex(SystemExit, 'mixer names must be unique'):
            make_config(
                values(
                    mixers=[
                        {'name': 'X18'},
                        {'name': 'X18'},
                    ]
                )
            )

    def test_mixer_ip_address_and_port_must_be_provided_together(self) -> None:
        with self.assertRaisesRegex(SystemExit, 'must be provided together'):
            make_config(
                values(
                    mixers=[
                        {
                            'name': 'X18',
                            'ip_address': 18,
                        }
                    ]
                )
            )
        with self.assertRaisesRegex(SystemExit, 'must be provided together'):
            make_config(values(mixers=[{'name': 'X18', 'port': 10024}]))

    def test_mixer_endpoint_requires_a_host(self) -> None:
        with self.assertRaisesRegex(SystemExit, 'must not be empty'):
            make_config(
                values(
                    mixers=[
                        {
                            'name': 'Flow 8',
                            'probe': {'host': '', 'port': 10024},
                        }
                    ]
                )
            )

    def test_unwired_x18_omits_x18_host(self) -> None:
        config = make_config(
            values(networks=networks(x18=False)),
        )

        self.assertEqual(config.networks['internal']['wired'], {})

    def test_ssh_port_defaults_to_22(self) -> None:
        config = make_config(
            values(networks=networks(x18=False)),
        )

        self.assertEqual(config.network.ssh_port, 22)

    def test_web_port_is_integer(self) -> None:
        config = make_config(
            values(
                network={'web_port': 17353},
                networks=networks(x18=False),
            ),
        )

        self.assertEqual(config.network.web_port, 17353)

    def test_root_is_read_from_paths_table(self) -> None:
        parsed = make_config(values(paths={'root': '/srv/show-projects'}))

        self.assertEqual(parsed.paths.root, Path('/srv/show-projects'))

    def test_root_expands_environment_variables_and_home(self) -> None:
        with mock.patch.dict(
            'os.environ', {'SHOWCO_ROOT': '/srv/show-projects'}, clear=False
        ):
            environment_root = make_config(values(paths={'root': '$SHOWCO_ROOT'}))
        home_root = make_config(values(paths={'root': '~/show-projects'}))

        self.assertEqual(environment_root.paths.root, Path('/srv/show-projects'))
        self.assertEqual(home_root.paths.root, Path.home() / 'show-projects')

    def test_root_rejects_relative_path(self) -> None:
        with self.assertRaisesRegex(SystemExit, 'paths.root must be an absolute'):
            make_config(values(paths={'root': 'show-projects'}))

    def test_config_validation_reports_missing_private_wifi_password(self) -> None:
        config = make_config(
            values(
                networks=networks(
                    internal_wifi={'name': 'showbox'},
                    external_wifi={'name': 'Venue'},
                )
            ),
        )

        with self.assertRaises(SystemExit) as error:
            provision.validate_config(config)

        self.assertIn(
            'networks.internal.wifi.private.password is required',
            str(error.exception),
        )

    def test_config_validation_accepts_passwordless_external_network(self) -> None:
        config = make_config(
            values(
                networks=networks(
                    internal_wifi={
                        'name': 'showbox',
                        'password': 'private password',
                    },
                    external_wifi={
                        'name': 'Venue',
                    },
                )
            ),
        )

        provision.validate_config(config)

    def test_config_validation_rejects_invalid_private_wifi_password(self) -> None:
        config = make_config(
            values(
                networks=networks(
                    internal_wifi={'name': 'showbox', 'password': 'short'},
                    external_wifi={'name': 'Venue'},
                )
            )
        )

        with self.assertRaisesRegex(SystemExit, 'must be 8-63'):
            provision.validate_config(config)

    def test_local_checkout_directory_is_parent_of_showco_checkout(self) -> None:
        self.assertEqual(
            provision.local_checkout_directory(),
            Path(__file__).resolve().parents[2],
        )

    def test_persist_network_host_replaces_existing_host(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            path.write_text('[network]\nhost = "old.local"\nweb_port = 17352\n')

            provision.persist_network_host(path, 'bertrand.local')

            self.assertEqual(
                path.read_text(),
                '[network]\nhost = "bertrand.local"\nweb_port = 17352\n',
            )

    def test_persist_network_host_adds_missing_host(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            path.write_text('[network]\nweb_port = 17352\n')

            provision.persist_network_host(path, 'bertrand.local')

            self.assertEqual(
                path.read_text(),
                '[network]\nhost = "bertrand.local"\nweb_port = 17352\n',
            )

    def test_persist_paths_root_adds_paths_table(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            path.write_text('[network]\nhost = "bertrand.local"\n')

            provision.persist_paths_root(path, Path('/srv/show-projects'))

            self.assertEqual(
                path.read_text(),
                '[paths]\nroot = "/srv/show-projects"\n\n'
                '[network]\nhost = "bertrand.local"\n',
            )

    def test_read_toml_preserves_string_lists(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            path.write_text('[stream]\ntags = ["Live Music", "Music"]\n')

            values = config.read_toml(path)

        self.assertEqual(
            config.table_value(values, 'stream')['tags'],
            ['Live Music', 'Music'],
        )

    def test_network_secrets_merge_by_key(self) -> None:
        config_values = {
            'networks': {
                'external': {
                    'wifi': {
                        'venue': {'name': 'Venue'},
                    },
                },
            },
        }
        secrets = {
            'networks': {
                'external': {
                    'wifi': {
                        'venue': {'password': 'venue password'},
                    },
                },
            },
        }

        values = config.merge_values(config_values, secrets)

        self.assertEqual(
            values['networks']['external']['wifi']['venue'],
            {
                'name': 'Venue',
                'password': 'venue password',
            },
        )

    def test_load_values_merges_config_and_secrets_files(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / 'config.toml'
            secrets_path = Path(directory) / 'secrets.toml'
            config_path.write_text("[network]\nhost = 'pi'\n")
            secrets_path.write_text("[network]\npassword = 'secret'\n")

            values = config.load_values(config_path, secrets_path)

        self.assertEqual(values, {'network': {'host': 'pi', 'password': 'secret'}})
