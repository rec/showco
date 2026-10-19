from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from showco.provision import config
from showco.streamo import auth


class StreamoAuthTests(unittest.TestCase):
    def test_exchange_code_saves_access_token_under_renamed_setting(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path, secrets_path = config_paths(root)
            with (
                mock.patch('showco.streamo.auth.Path.home', return_value=root),
                mock.patch(
                    'showco.streamo.auth.request_http',
                    return_value=auth.HttpResponse(
                        status=200, text='{"access_token":"test-token"}'
                    ),
                ),
            ):
                self.assertEqual(auth.exchange_code(config_path, secrets_path), 0)
            values = config.read_toml(secrets_path)['stream']
        self.assertEqual(values['access_token'], 'test-token')
        self.assertNotIn('oath_token', values)

    def test_exchange_code_reports_http_error_response(self) -> None:
        with TemporaryDirectory() as directory:
            directory_path = Path(directory)
            config_path, secrets_path = config_paths(directory_path)
            with (
                mock.patch(
                    'showco.streamo.auth.request_http',
                    return_value=auth.HttpResponse(
                        status=400, text='{"message":"bad code"}'
                    ),
                ),
                mock.patch(
                    'showco.streamo.auth.Path.home', return_value=directory_path
                ),
            ):
                result = auth.exchange_code(config_path, secrets_path)

            response = json.loads(
                (directory_path / '.config/streamo/oauth-response.json').read_text()
            )

        self.assertEqual(result, 1)
        self.assertEqual(response, {'message': 'bad code'})
        self.assertFalse((directory_path / '.config/streamo/oauth-token').exists())

    def test_exchange_code_reports_non_json_response(self) -> None:
        with TemporaryDirectory() as directory:
            directory_path = Path(directory)
            config_path, secrets_path = config_paths(directory_path)
            with (
                mock.patch(
                    'showco.streamo.auth.request_http',
                    return_value=auth.HttpResponse(status=200, text='not json'),
                ),
                mock.patch(
                    'showco.streamo.auth.Path.home', return_value=directory_path
                ),
            ):
                result = auth.exchange_code(config_path, secrets_path)

            response = (
                directory_path / '.config/streamo/oauth-response.json'
            ).read_text()

        self.assertEqual(result, 1)
        self.assertEqual(response, 'not json\n')
        self.assertFalse((directory_path / '.config/streamo/oauth-token').exists())

    def test_read_toml_preserves_string_lists(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            path.write_text('[stream]\ntags = ["Live Music", "Music"]\n')

            values = config.read_toml(path)

        self.assertEqual(
            config.table_value(values, 'stream')['tags'],
            ['Live Music', 'Music'],
        )

    def test_write_toml_value_updates_section_value(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            path.write_text('[stream]\nstate = "old"\n')

            auth.write_toml_value(path, 'stream', 'state', 'new')

            values = config.read_toml(path)

        self.assertEqual(config.table_value(values, 'stream')['state'], 'new')


def config_paths(directory: Path) -> tuple[Path, Path]:
    config_path = directory / 'config.toml'
    secrets_path = directory / 'secrets.toml'
    config_path.write_text(
        '[stream]\n'
        'client_id = "client-id"\n'
        'redirect_uri = "http://localhost/callback"\n'
    )
    secrets_path.write_text(
        '[stream]\nclient_secret = "client-secret"\ncallback_url_or_code = "code"\n'
    )
    return config_path, secrets_path


if __name__ == '__main__':
    unittest.main()


def test_token_files_and_directory_are_private_even_when_replacing_existing_files(
    tmp_path: Path,
) -> None:
    config_path, secrets_path = config_paths(tmp_path)
    directory = tmp_path / '.config/streamo'
    directory.mkdir(parents=True)
    directory.chmod(0o755)
    for name in ['oauth-response.json', 'oauth-token', 'refresh-token']:
        path = directory / name
        path.write_text('old')
        path.chmod(0o644)
    secrets_path.chmod(0o644)
    with (
        mock.patch.object(auth.Path, 'home', return_value=tmp_path),
        mock.patch.object(
            auth,
            'request_http',
            return_value=auth.HttpResponse(
                status=200,
                text='{"access_token":"test","refresh_token":"test-refresh"}',
            ),
        ),
    ):
        assert auth.exchange_code(config_path, secrets_path) == 0
    assert directory.stat().st_mode & 0o777 == 0o700
    for path in [secrets_path, *directory.iterdir()]:
        assert path.stat().st_mode & 0o777 == 0o600
