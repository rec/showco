from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from subprocess import CompletedProcess
from typing import ClassVar

from pydantic import BaseModel
from reccy import paths, reccy, service, service_spec
from reccy.models import DaemonMetadata, ServiceSpec, StatusResult

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
    daemon_module: ClassVar[str] = "showco"


class RecsDaemonStatus(BaseModel, frozen=True):
    gui_ipc_error: str | None = None


STATUS_MODELS = {"recs": RecsDaemonStatus}
STATUS_ERROR_ATTRIBUTES = {"recs": "gui_ipc_error"}
STATUS_ERROR_LABELS = {"recs": "GUI IPC error"}


def install_showco_service(
    root: Path,
    host: str = "0.0.0.0",
    port: int = 17_352,
    mixers_config: Path | None = None,
    twitcho_enabled: bool = False,
    lyte_enabled: bool = False,
) -> int:
    daemon = ShowcoDaemon(platform=paths.current_platform())
    result = daemon.install_service(
        [
            "run",
            *showco_args(
                host,
                port,
                mixers_config,
                twitcho_enabled,
                lyte_enabled,
            ),
        ]
    )
    service.print_service_status("showco", result)
    return 0 if result.running else 1


def showco_args(
    host: str,
    port: int,
    mixers_config: Path | None,
    twitcho_enabled: bool,
    lyte_enabled: bool,
) -> list[str]:
    result = ["--host", host, "--port", str(port)]
    if mixers_config is not None:
        result.extend(["--mixers-config", str(mixers_config)])
    if twitcho_enabled:
        result.append("--twitcho-enabled")
    if lyte_enabled:
        result.append("--lyte-enabled")
    return result


def restart_twitcho_service() -> models.ActionResult:
    result = service_registry().controller("twitcho").restart()
    if result.running:
        return models.ActionResult(ok=True, message="twitcho restart requested")
    return models.ActionResult(ok=False, message="twitcho service did not start")


def refresh_service_definition(
    name: str,
    runner: Callable[..., CompletedProcess[str]] | None = None,
) -> StatusResult:
    controller = service_controller(SERVICES[name], runner=runner)
    data = json.loads(controller.paths.metadata.read_text())
    if name == "recs" and "gui_endpoint" in data:
        data["control_endpoint"] = data.pop("gui_endpoint")
    if "module" not in data:
        module = name
        data["module"] = module
        if data["argv"][:2] == ["-m", module]:
            data["argv"] = data["argv"][2:]
    metadata = DaemonMetadata.model_validate(data)
    return controller.install(metadata)


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
    parser.add_argument("--mixers-config", type=Path)
    parser.add_argument("--twitcho-enabled", action="store_true")
    parser.add_argument("--lyte-enabled", action="store_true")
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args(argv)
    return install_showco_service(
        host=args.host,
        port=args.port,
        mixers_config=args.mixers_config,
        twitcho_enabled=args.twitcho_enabled,
        lyte_enabled=args.lyte_enabled,
        root=args.root,
    )


def status_main(argv: list[str] | None = None) -> int:
    machine_role.require_target_machine("showco run service-status")
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments or arguments[:1] in (["-h"], ["--help"]):
        print("Usage: showco run service-status {lyte,recs,showco,twitcho} ...")
        return 0
    return report_service_status(arguments)
