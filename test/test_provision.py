from __future__ import annotations

import argparse
import unittest

from showco.provision import provision


class ProvisionTests(unittest.TestCase):
    def test_wired_x18_uses_configured_x18_host(self) -> None:
        config = provision.config_from_args(
            args(),
            values(
                is_x18_wired=True,
                showco_x18_wired_ethernet_ip_address="10.43.0.18",
            ),
        )

        self.assertEqual(config.showco_x18_host, "10.43.0.18")

    def test_unwired_x18_omits_x18_host(self) -> None:
        config = provision.config_from_args(
            args(),
            values(is_x18_wired=False),
        )

        self.assertEqual(config.showco_x18_host, "")


def args() -> argparse.Namespace:
    return argparse.Namespace(
        host=None,
        user=None,
        port=None,
        hostname=None,
        recs_repo=None,
        twitcho_repo=None,
        showco_repo=None,
    )


def values(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "showco_pi_host": "recs-stage.local",
        "showco_pi_user": "tom",
        "showco_pi_ssh_port": "22",
        "recs_repo": "git@github.com:rec/recs.git",
        "twitcho_repo": "git@github.com:rec/twitcho.git",
        "showco_repo": "git@github.com:rec/showco.git",
    }
    result.update(overrides)
    return result


if __name__ == "__main__":
    unittest.main()
