from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from subprocess import CompletedProcess
from typing import ClassVar

from pydantic import BaseModel
from reccy import paths, reccy, service, service_spec
from reccy.models import ServiceSpec, StatusResult

from . import machine_role, models

PROJECT_ROOT = Path(__file__).parent.parent
SHOWCO_SERVICE = service_spec.load(Path(__file__).with_name("service.toml"))
RECS_SERVICE = service_spec.load(PROJECT_ROOT.parent / "recs/recs/daemon/service.toml")
LYTE_SERVICE = service_spec.load(PROJECT_ROOT.parent / "lyte/lyte/service.toml")
TWITCHO_SERVICE = service_spec.load(
    PROJECT_ROOT.parent / "twitcho/twitcho/service.toml"
)
SERVICES = {
    "lyte": LYTE_SERVICE,
    "recs": RECS_SERVICE,
    "showco": SHOWCO_SERVICE,
    "twitcho": TWITCHO_SERVICE,
}


class ShowcoDaemon(reccy.Reccy, frozen=True):
    service_spec: ClassVar[ServiceSpec] = SHOWCO_SERVICE

    root: Path

    def daemon_executable(self) -> Path:
        return self.root / "showco/.venv/bin/showco"


class RecsDaemonStatus(BaseModel, frozen=True):
    gui_ipc_error: str | None = None


STATUS_MODELS = {"recs": RecsDaemonStatus}
STATUS_ERROR_ATTRIBUTES = {"recs": "gui_ipc_error"}
STATUS_ERROR_LABELS = {"recs": "GUI IPC error"}


def install_showco_service(
    root: Path,
    host: str = "0.0.0.0",
    port: int = 17_352,
    mixer_host: str | None = None,
    mixer_port: int | None = None,
    mixer_protocol: str = "tcp",
    x18_host: str | None = None,
    x18_log_dir: Path | None = None,
    twitcho_enabled: bool = False,
) -> int:
    daemon = ShowcoDaemon(
        platform=paths.current_platform(),
        root=root,
    )
    result = daemon.install_service(
        [
            "run",
            *showco_args(
                host,
                port,
                mixer_host,
                mixer_port,
                mixer_protocol,
                x18_host,
                x18_log_dir or Path.home() / "recordings",
                twitcho_enabled,
            ),
        ]
    )
    service.print_service_status("showco", result)
    return 0 if result.running else 1


def showco_args(
    host: str,
    port: int,
    mixer_host: str | None,
    mixer_port: int | None,
    mixer_protocol: str,
    x18_host: str | None,
    x18_log_dir: Path,
    twitcho_enabled: bool,
) -> list[str]:
    result = ["--host", host, "--port", str(port)]
    if mixer_host and mixer_port is not None:
        result.extend(
            [
                "--mixer-host",
                mixer_host,
                "--mixer-port",
                str(mixer_port),
                "--mixer-protocol",
                mixer_protocol,
            ]
        )
    if x18_host:
        result.extend(["--x18-host", x18_host, "--x18-log-dir", str(x18_log_dir)])
    if twitcho_enabled:
        result.append("--twitcho-enabled")
    return result


def restart_twitcho_service() -> models.ActionResult:
    result = service_registry().controller("twitcho").restart()
    if result.running:
        return models.ActionResult(ok=True, message="twitcho restart requested")
    return models.ActionResult(ok=False, message="twitcho service did not start")


def report_service_status(service_names: list[str]) -> int:
    return service_registry().report_status(service_names)


def service_status(name: str) -> StatusResult:
    return service_registry().status(name)


def service_controller(
    service: ServiceSpec,
    runner: Callable[..., CompletedProcess[str]] | None = None,
) -> service.ServiceController:
    return service_registry(runner=runner).controller(service.name)


def service_registry(
    runner: Callable[..., CompletedProcess[str]] | None = None,
) -> service.ServiceRegistry:
    return service.ServiceRegistry(
        SERVICES,
        runner=runner,
        status_models=STATUS_MODELS,
        status_error_attributes=STATUS_ERROR_ATTRIBUTES,
        status_error_labels=STATUS_ERROR_LABELS,
    )


def install_main(argv: list[str] | None = None) -> int:
    machine_role.require_target_machine("showco run install-service")
    parser = argparse.ArgumentParser(
        prog="showco run install-service",
        description="Install or refresh the Showco user service",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=17_352, type=int)
    parser.add_argument("--mixer-host")
    parser.add_argument("--mixer-port", type=int)
    parser.add_argument("--mixer-protocol", default="tcp")
    parser.add_argument("--x18-host")
    parser.add_argument("--x18-log-dir", type=Path)
    parser.add_argument("--twitcho-enabled", action="store_true")
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args(argv)
    return install_showco_service(
        host=args.host,
        port=args.port,
        mixer_host=args.mixer_host,
        mixer_port=args.mixer_port,
        mixer_protocol=args.mixer_protocol,
        x18_host=args.x18_host,
        x18_log_dir=args.x18_log_dir,
        twitcho_enabled=args.twitcho_enabled,
        root=args.root,
    )


def status_main(argv: list[str] | None = None) -> int:
    machine_role.require_target_machine("showco run service-status")
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments or arguments[:1] in (["-h"], ["--help"]):
        print("Usage: showco run service-status {lyte,recs,showco,twitcho} ...")
        return 0
    return report_service_status(arguments)
