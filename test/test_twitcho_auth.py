from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from showco.twitcho import auth


class TwitchoAuthTests(unittest.TestCase):
    def test_exchange_code_reports_http_error_response(self) -> None:
        with TemporaryDirectory() as directory:
            config_dir = Path(directory)
            with mock.patch(
                "showco.twitcho.auth.request_http",
                return_value=auth.HttpResponse(
                    status=400, text='{"message":"bad code"}'
                ),
            ):
                result = auth.exchange_code(env(config_dir))

            response = json.loads((config_dir / "oauth-response.json").read_text())

        self.assertEqual(result, 1)
        self.assertEqual(response, {"message": "bad code"})
        self.assertFalse((config_dir / "oauth-token").exists())

    def test_exchange_code_reports_non_json_response(self) -> None:
        with TemporaryDirectory() as directory:
            config_dir = Path(directory)
            with mock.patch(
                "showco.twitcho.auth.request_http",
                return_value=auth.HttpResponse(status=200, text="not json"),
            ):
                result = auth.exchange_code(env(config_dir))

            response = (config_dir / "oauth-response.json").read_text()

        self.assertEqual(result, 1)
        self.assertEqual(response, "not json\n")
        self.assertFalse((config_dir / "oauth-token").exists())


def env(config_dir: Path) -> dict[str, str]:
    return {
        "twitch_client_id": "client-id",
        "twitch_client_secret": "client-secret",
        "twitch_redirect_uri": "http://localhost/callback",
        "twitch_callback_url_or_code": "code",
        "twitch_config_dir": str(config_dir),
    }


if __name__ == "__main__":
    unittest.main()
