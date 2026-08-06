from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import reccy.cli
import tyro

from . import git_pull, network_config, services
from .mixer import MixerMonitor
from .provision import provision
from .rehearsal import (
    RehearsalMixerMonitor,
    RehearsalRecsClient,
    RehearsalSystemMonitor,
    RehearsalTwitchoClient,
    RehearsalTwitchoSupervisor,
)
from .server import make_server
from .twitcho import auth, supervisor
from .x18 import osc, recorder_supervisor


def run_web_ui(
    host: str = "127.0.0.1",
    port: int = 17_352,
    mixer_host: str | None = None,
    mixer_port: int | None = None,
    mixer_protocol: Literal["tcp", "udp"] = "tcp",
    x18_host: Annotated[
        str | None,
        tyro.conf.arg(
            help="start a read-only X18 OSC recorder subprocess for this mixer host"
        ),
    ] = None,
    x18_port: int = osc.X18_OSC_PORT,
    x18_log_dir: Path = Path("."),
    twitcho_config: Annotated[
        Path | None,
        tyro.conf.arg(help="start and supervise Twitcho with this config file"),
    ] = None,
    twitcho_restart_policy: Annotated[
        Literal["internal", "external"],
        tyro.conf.arg(help="restart policy for the supervised Twitcho process"),
    ] = "external",
    rehearsal: Annotated[
        bool,
        tyro.conf.arg(help="run with simulated recs and twitcho services"),
    ] = False,
) -> int:
    x18_recorder = None

    if rehearsal:
        server = make_server(
            host,
            port,
            recs=RehearsalRecsClient(),
            twitcho=RehearsalTwitchoClient(),
            system=RehearsalSystemMonitor(),
            mixer=RehearsalMixerMonitor(),
            twitcho_supervisor=RehearsalTwitchoSupervisor(),
        )
        print(f"showco rehearsal listening on http://{host}:{port}")
    else:
        twitcho_supervisor = None
        if twitcho_config:
            twitcho_supervisor = supervisor.TwitchoSupervisor(
                twitcho_config,
                policy=twitcho_restart_policy,
            )
        server = make_server(
            host,
            port,
            mixer=MixerMonitor(
                host=mixer_host,
                port=mixer_port,
                protocol=mixer_protocol,
            ),
            twitcho_supervisor=twitcho_supervisor,
        )
        print(f"showco listening on http://{host}:{port}")
    if x18_host:
        x18_recorder = recorder_supervisor.X18RecorderSupervisor(
            x18_host,
            port=x18_port,
            log_dir=x18_log_dir,
        )
        x18_recorder.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Interrupted")
    finally:
        server.server_close()
        if x18_recorder:
            x18_recorder.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    return reccy.cli.route_command(
        {
            "run": run_command,
            "provision": provision.main,
            "twitcho": auth.main,
        },
        argv,
        prog="showco",
    )


def run_command(arguments: list[str]) -> int:
    if arguments[:1] == ["git-pull"]:
        return git_pull.main(arguments[1:])
    if arguments[:1] == ["x18-record"]:
        return osc.main(arguments[1:])
    if arguments[:1] == ["network-config"]:
        return network_config.main(arguments[1:])
    if arguments[:1] == ["install-service"]:
        return services.install_main(arguments[1:])
    if arguments[:1] == ["service-status"]:
        return services.status_main(arguments[1:])
    return tyro.cli(
        run_web_ui,
        args=arguments,
        description="Run the Showco web UI",
    )
