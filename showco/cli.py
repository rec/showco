from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import tyro
from pydantic import BaseModel
from reccy import cli
from reccy.runtime import logging

from .deployment import bundle, card, go, logs, machine_role, python
from .provision import network
from .runtime import rehearsal, services
from .runtime.mixer import MixersMonitor, load_mixer_specs
from .runtime.server import make_server
from .streamo import auth, client, config


class WebUiOptions(BaseModel, frozen=True):
    host: str = "127.0.0.1"
    port: int = 17_352
    mixers_config: Path = Path()
    streamo_enabled: bool = False
    lyte_enabled: bool = False
    rehearsal_mode: Annotated[
        bool,
        tyro.conf.arg(
            name="rehearsal",
            help="run with simulated recs and streamo services",
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
            streamo=rehearsal.RehearsalStreamoClient(),
            system=rehearsal.RehearsalSystemMonitor(),
            mixers=rehearsal.RehearsalMixersMonitor(),
            streamo_restart=rehearsal.restart_streamo,
            streamo_enabled=True,
        )
        print(f"showco rehearsal listening on http://{options.host}:{options.port}")
    else:
        server = make_server(
            options.host,
            options.port,
            mixers=MixersMonitor(load_mixer_specs(options.mixers_config)),
            streamo_enabled=options.streamo_enabled,
            lyte_enabled=options.lyte_enabled,
            performance_enabled=True,
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
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments or arguments[0].startswith("-"):
        return go.main(arguments)
    return cli.route_command(
        {
            "run": run_command,
            "bundle": bundle.main,
            "prepare-card": card.main,
            "go": go.main,
            "logs": logs.main,
            "python": python.main,
            "streamo": streamo_command,
        },
        arguments,
        prog="showco",
    )


def run_command(arguments: list[str]) -> int:
    if arguments[:1] == ["network-config"]:
        return network.main(arguments[1:])
    if arguments[:1] == ["install-service"]:
        return services.install_main(arguments[1:])
    if arguments[:1] == ["service-status"]:
        return services.status_main(arguments[1:])
    if arguments[:1] == ["streamo-health"]:
        return client.health_main(arguments[1:])
    if arguments[:1] == ["streamo-config"]:
        return config.main(arguments[1:])
    options = tyro.cli(
        WebUiOptions,
        args=arguments,
        description="Run the Showco web UI",
    )
    return run_web_ui(options)


def streamo_command(arguments: list[str]) -> int:
    machine_role.require_target_machine("showco streamo")
    return auth.main(arguments)
