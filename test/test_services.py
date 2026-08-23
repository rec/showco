from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from reccy.models import Platform, ServicePaths, StatusResult

from showco import services


class ServicesTests(unittest.TestCase):
    def test_registry_includes_managed_services(self) -> None:
        self.assertEqual(services.SERVICES["lyte"], services.LYTE_SERVICE)
        self.assertEqual(services.SERVICES["twitcho"].name, "twitcho")

    def test_showco_args_include_optional_services(self) -> None:
        self.assertEqual(
            services.showco_args(
                "0.0.0.0",
                17352,
                Path("/home/tom/.config/showco/mixers.toml"),
                True,
            ),
            [
                "--host",
                "0.0.0.0",
                "--port",
                "17352",
                "--mixers-config",
                "/home/tom/.config/showco/mixers.toml",
                "--twitcho-enabled",
            ],
        )

    def test_install_showco_service_uses_reccy_controller(self) -> None:
        with TemporaryDirectory() as directory:
            paths = ServicePaths(
                metadata=Path(directory) / "daemon.json",
                service=Path(directory) / "showco.service",
                status=Path(directory) / "status.json",
                log=Path(directory) / "showco.log",
                control_endpoint=Path(directory) / "gui.sock",
            )
            controller = mock.Mock()
            controller.install.return_value = StatusResult(installed=True, running=True)
            with (
                mock.patch(
                    "showco.services.paths.current_platform",
                    return_value=Platform.linux,
                ),
                mock.patch(
                    "showco.services.paths.service_paths",
                    return_value=paths,
                ),
                mock.patch(
                    "showco.services.service.ServiceController",
                    return_value=controller,
                ),
            ):
                result = services.install_showco_service(
                    root=Path("/srv/show-projects"),
                    host="0.0.0.0",
                    port=17352,
                )

        self.assertEqual(result, 0)
        metadata = controller.install.call_args.args[0]
        self.assertEqual(
            metadata.argv[:5], ["run", "--host", "0.0.0.0", "--port", "17352"]
        )

    def test_showco_daemon_uses_reccy_service_lifecycle(self) -> None:
        daemon = services.ShowcoDaemon(platform=Platform.linux)

        self.assertEqual(daemon.name, "showco")

    def test_refreshes_legacy_recs_service_metadata(self) -> None:
        controller = mock.Mock()
        controller.paths.metadata.read_text.return_value = json.dumps(
            {
                "argv": ["--silent", "--include", "Mic"],
                "platform": "linux",
                "gui_endpoint": "/tmp/recs-gui.sock",
            }
        )
        controller.install.return_value = StatusResult(installed=True, running=True)
        with mock.patch("showco.services.service_controller", return_value=controller):
            result = services.refresh_service_definition("recs")

        self.assertTrue(result.running)
        self.assertEqual(
            controller.install.call_args.args[0].model_dump(),
            {
                "version": 1,
                "argv": ["--silent", "--include", "Mic"],
                "module": "recs",
                "platform": "linux",
                "control_endpoint": "/tmp/recs-gui.sock",
                "event_endpoint": None,
            },
        )

    def test_showco_args_omit_twitcho_when_disabled(self) -> None:
        arguments = services.showco_args(
            "0.0.0.0",
            17_352,
            None,
            False,
        )

        self.assertNotIn("--twitcho-enabled", arguments)

    def test_restart_twitcho_service_uses_service_registry(self) -> None:
        registry = mock.Mock()
        registry.controller.return_value.restart.return_value = StatusResult(
            installed=True,
            running=True,
        )
        with mock.patch("showco.services.service_registry", return_value=registry):
            result = services.restart_twitcho_service()

        self.assertTrue(result.ok)
        self.assertEqual(result.message, "twitcho restart requested")
        registry.controller.assert_called_once_with("twitcho")

    def test_report_service_status_fails_inactive_service(self) -> None:
        registry = mock.Mock()
        registry.report_status.return_value = 1
        with mock.patch("showco.services.service_registry", return_value=registry):
            self.assertEqual(services.report_service_status(["showco"]), 1)

        registry.report_status.assert_called_once_with(["showco"])

    def test_recs_service_controller_uses_gui_ipc_error_status(self) -> None:
        with mock.patch(
            "showco.services.paths.current_platform", return_value=Platform.linux
        ):
            controller = services.service_controller(services.RECS_SERVICE)

        self.assertIs(controller.status_model, services.RecsDaemonStatus)
        self.assertEqual(controller.status_error_attribute, "gui_ipc_error")


if __name__ == "__main__":
    unittest.main()
