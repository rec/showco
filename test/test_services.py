from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from reccy.models import Platform, ServicePaths, StatusResult

from showco import services


class ServicesTests(unittest.TestCase):
    def test_showco_args_include_optional_services(self) -> None:
        self.assertEqual(
            services.showco_args(
                "0.0.0.0",
                17352,
                "10.43.0.18",
                "10.43.0.18",
                Path("/recordings"),
                Path("/twitcho/config.json"),
            ),
            [
                "--host",
                "0.0.0.0",
                "--port",
                "17352",
                "--mixer-host",
                "10.43.0.18",
                "--x18-host",
                "10.43.0.18",
                "--x18-log-dir",
                "/recordings",
                "--twitcho-config",
                "/twitcho/config.json",
            ],
        )

    def test_install_showco_service_uses_reccy_controller(self) -> None:
        with TemporaryDirectory() as directory:
            paths = ServicePaths(
                metadata=Path(directory) / "daemon.json",
                service=Path(directory) / "showco.service",
                status=Path(directory) / "status.json",
                stdout_log=Path(directory) / "showco.out.log",
                stderr_log=Path(directory) / "showco.err.log",
                control_endpoint=Path(directory) / "gui.sock",
            )
            controller = mock.Mock()
            controller.install.return_value = StatusResult(installed=True, running=True)
            with (
                mock.patch(
                    "showco.services.reccy.paths.current_platform",
                    return_value=Platform.linux,
                ),
                mock.patch(
                    "showco.services.reccy.paths.service_paths",
                    return_value=paths,
                ),
                mock.patch(
                    "showco.services.reccy.service.ServiceController",
                    return_value=controller,
                ),
            ):
                result = services.install_showco_service(
                    host="0.0.0.0",
                    port=17352,
                    executable=Path("/home/tom/code/showco/.venv/bin/showco"),
                )

        self.assertEqual(result, 0)
        metadata = controller.install.call_args.args[0]
        self.assertEqual(
            metadata.executable, Path("/home/tom/code/showco/.venv/bin/showco")
        )
        self.assertEqual(
            metadata.argv[:5], ["run", "--host", "0.0.0.0", "--port", "17352"]
        )

    def test_report_service_status_fails_inactive_service(self) -> None:
        registry = mock.Mock()
        registry.report_status.return_value = 1
        with mock.patch("showco.services.service_registry", return_value=registry):
            self.assertEqual(services.report_service_status(["showco"]), 1)

        registry.report_status.assert_called_once_with(["showco"])

    def test_recs_service_controller_uses_gui_ipc_error_status(self) -> None:
        with mock.patch(
            "showco.services.reccy.paths.current_platform", return_value=Platform.linux
        ):
            controller = services.service_controller(services.RECS_SERVICE)

        self.assertIs(controller.status_model, services.RecsDaemonStatus)
        self.assertEqual(controller.status_error_attribute, "gui_ipc_error")


if __name__ == "__main__":
    unittest.main()
