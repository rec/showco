from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from showco.provision import config
from showco.streamo import auth


class StreamoAuthTests(unittest.TestCase):
    def test_exchange_code_reports_http_error_response(self) -> None:
        with TemporaryDirectory() as directory:
            directory_path = Path(directory)
            config_path, secrets_path = config_paths(directory_path)
            with (
                mock.patch(
                    "showco.streamo.auth.request_http",
                    return_value=auth.HttpResponse(
                        status=400, text='{"message":"bad code"}'
                    ),
                ),
                mock.patch(
                    "showco.streamo.auth.Path.home", return_value=directory_path
                ),
            ):
                result = auth.exchange_code(config_path, secrets_path)

            response = json.loads(
                (directory_path / ".config/streamo/oauth-response.json").read_text()
            )

        self.assertEqual(result, 1)
        self.assertEqual(response, {"message": "bad code"})
        self.assertFalse((directory_path / ".config/streamo/oauth-token").exists())

    def test_exchange_code_reports_non_json_response(self) -> None:
        with TemporaryDirectory() as directory:
            directory_path = Path(directory)
            config_path, secrets_path = config_paths(directory_path)
            with (
                mock.patch(
                    "showco.streamo.auth.request_http",
                    return_value=auth.HttpResponse(status=200, text="not json"),
                ),
                mock.patch(
                    "showco.streamo.auth.Path.home", return_value=directory_path
                ),
            ):
                result = auth.exchange_code(config_path, secrets_path)

            response = (
                directory_path / ".config/streamo/oauth-response.json"
            ).read_text()

        self.assertEqual(result, 1)
        self.assertEqual(response, "not json\n")
        self.assertFalse((directory_path / ".config/streamo/oauth-token").exists())

    def test_read_toml_preserves_string_lists(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('[stream]\ntags = ["Live Music", "Music"]\n')

            values = config.read_toml(path)

        self.assertEqual(
            config.table_value(values, "stream")["tags"],
            ["Live Music", "Music"],
        )

    def test_write_toml_value_updates_section_value(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('[stream]\nstate = "old"\n')

            auth.write_toml_value(path, "stream", "state", "new")

            values = config.read_toml(path)

        self.assertEqual(config.table_value(values, "stream")["state"], "new")


def config_paths(directory: Path) -> tuple[Path, Path]:
    config_path = directory / "config.toml"
    secrets_path = directory / "secrets.toml"
    config_path.write_text(
        "[stream]\n"
        'client_id = "client-id"\n'
        'redirect_uri = "http://localhost/callback"\n'
    )
    secrets_path.write_text(
        '[stream]\nclient_secret = "client-secret"\ncallback_url_or_code = "code"\n'
    )
    return config_path, secrets_path


if __name__ == "__main__":
    unittest.main()
