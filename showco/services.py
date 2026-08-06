from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import reccy.paths
import reccy.renderers
import reccy.service
from pydantic import BaseModel
from reccy.models import ServiceSpec, StatusResult

SHOWCO_SERVICE = ServiceSpec(
    name="showco",
    display_name="Showco",
    description="Showco local show control",
    launchd_label="com.swirly.showco",
    daemon_env_var="SHOWCO_DAEMON",
    windows_pipe=r"\\.\pipe\showco",
)
RECS_SERVICE = ServiceSpec(
    name="recs",
    display_name="recs",
    description="recs background recorder",
    launchd_label="com.swirly.recs",
    daemon_env_var="RECS_DAEMON",
    windows_pipe=r"\\.\pipe\recs",
)
SERVICES = {
    "recs": RECS_SERVICE,
    "showco": SHOWCO_SERVICE,
}


class RecsDaemonStatus(BaseModel, frozen=True):
    gui_ipc_error: str | None = None


def install_showco_service(
    host: str = "0.0.0.0",
    port: int = 17_352,
    mixer_host: str | None = None,
    x18_host: str | None = None,
    x18_log_dir: Path | None = None,
    twitcho_config: Path | None = None,
    executable: Path | None = None,
) -> int:
    platform = reccy.paths.current_platform()
    paths = reccy.paths.service_paths(SHOWCO_SERVICE, platform)
    executable = executable or Path.home() / "code/showco/.venv/bin/showco"
    metadata = reccy.renderers.service_metadata(
        executable,
        platform,
        [
            "run",
            *showco_args(
                host,
                port,
                mixer_host,
                x18_host,
                x18_log_dir or Path.home() / "recordings",
                twitcho_config,
            ),
        ],
        paths,
    )
    result = reccy.service.ServiceController(SHOWCO_SERVICE, platform).install(metadata)
    print_service_status("showco", result)
    return 0 if result.running else 1


def showco_args(
    host: str,
    port: int,
    mixer_host: str | None,
    x18_host: str | None,
    x18_log_dir: Path,
    twitcho_config: Path | None,
) -> list[str]:
    result = ["--host", host, "--port", str(port)]
    if mixer_host:
        result.extend(["--mixer-host", mixer_host])
    if x18_host:
        result.extend(["--x18-host", x18_host, "--x18-log-dir", str(x18_log_dir)])
    if twitcho_config:
        result.extend(["--twitcho-config", str(twitcho_config)])
    return result


def report_service_status(service_names: list[str]) -> int:
    failures = 0
    for name in service_names:
        if name not in SERVICES:
            print(f"unknown service: {name}", file=sys.stderr)
            failures += 1
            continue
        result = service_status(name)
        print_service_status(name, result)
        if result.running is not True:
            failures += 1
    return 0 if failures == 0 else 1


def service_status(name: str) -> StatusResult:
    service = SERVICES[name]
    return service_controller(service).status()


def service_controller(
    service: ServiceSpec,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> reccy.service.ServiceController:
    platform = reccy.paths.current_platform()
    if service == RECS_SERVICE:
        return reccy.service.ServiceController(
            service,
            platform,
            runner=runner,
            status_model=RecsDaemonStatus,
            status_error_attribute="gui_ipc_error",
            status_error_label="GUI IPC error",
        )
    return reccy.service.ServiceController(service, platform, runner=runner)


def print_service_status(name: str, result: StatusResult) -> None:
    state = "active" if result.running else "inactive"
    print(f"{name}: {state}")
    if result.details:
        print(result.details)


def install_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="showco run install-service",
        description="Install or refresh the Showco user service",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=17_352, type=int)
    parser.add_argument("--mixer-host")
    parser.add_argument("--x18-host")
    parser.add_argument("--x18-log-dir", type=Path)
    parser.add_argument("--twitcho-config", type=Path)
    args = parser.parse_args(argv)
    return install_showco_service(
        host=args.host,
        port=args.port,
        mixer_host=args.mixer_host,
        x18_host=args.x18_host,
        x18_log_dir=args.x18_log_dir,
        twitcho_config=args.twitcho_config,
    )


def status_main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments or arguments[:1] in (["-h"], ["--help"]):
        print("Usage: showco run service-status {recs,showco} ...")
        return 0
    return report_service_status(arguments)
