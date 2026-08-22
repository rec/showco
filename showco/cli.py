from __future__ import annotations

from typing import Annotated, Literal

import tyro
from pydantic import BaseModel
from reccy import cli, logging

from . import logs, machine_role, network_config, python, rehearsal, services, update
from .mixer import MixerMonitor
from .provision import provision
from .server import make_server
from .twitcho import auth, client


class WebUiOptions(BaseModel, frozen=True):
    host: str = "127.0.0.1"
    port: int = 17_352
    mixer_host: str | None = None
    mixer_port: int | None = None
    mixer_protocol: Literal["tcp", "udp"] = "tcp"
    twitcho_enabled: bool = False
    rehearsal_mode: Annotated[
        bool,
        tyro.conf.arg(
            name="rehearsal",
            help="run with simulated recs and twitcho services",
        ),
    ] = False


def run_web_ui(options: WebUiOptions) -> int:
    if not options.rehearsal_mode:
        machine_role.require_target_machine("showco run")
    if options.rehearsal_mode:
        server = make_server(
            options.host,
            options.port,
            recs=rehearsal.RehearsalRecsClient(),
            twitcho=rehearsal.RehearsalTwitchoClient(),
            system=rehearsal.RehearsalSystemMonitor(),
            mixer=rehearsal.RehearsalMixerMonitor(),
            twitcho_restart=rehearsal.restart_twitcho,
            twitcho_enabled=True,
        )
        print(f"showco rehearsal listening on http://{options.host}:{options.port}")
    else:
        server = make_server(
            options.host,
            options.port,
            mixer=MixerMonitor(
                host=options.mixer_host,
                port=options.mixer_port,
                protocol=options.mixer_protocol,
            ),
            twitcho_enabled=options.twitcho_enabled,
        )
        print(f"showco listening on http://{options.host}:{options.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Interrupted")
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.configure()
    return cli.route_command(
        {
            "run": run_command,
            "provision": provision.main,
            "logs": logs.main,
            "python": python.main,
            "twitcho": twitcho_command,
            "update": update.main,
        },
        argv,
        prog="showco",
    )


def run_command(arguments: list[str]) -> int:
    if arguments[:1] == ["network-config"]:
        return network_config.main(arguments[1:])
    if arguments[:1] == ["install-service"]:
        return services.install_main(arguments[1:])
    if arguments[:1] == ["service-status"]:
        return services.status_main(arguments[1:])
    if arguments[:1] == ["twitcho-health"]:
        return client.health_main(arguments[1:])
    options = tyro.cli(
        WebUiOptions,
        args=arguments,
        description="Run the Showco web UI",
    )
    return run_web_ui(options)


def twitcho_command(arguments: list[str]) -> int:
    machine_role.require_target_machine("showco twitcho")
    return auth.main(arguments)
