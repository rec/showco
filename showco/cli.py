from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import tyro
from reccy import cli

from . import git_pull, machine_role, network_config, rehearsal, services
from .mixer import MixerMonitor
from .provision import provision
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
    rehearsal_mode: Annotated[
        bool,
        tyro.conf.arg(
            name="rehearsal",
            help="run with simulated recs and twitcho services",
        ),
    ] = False,
) -> int:
    if not rehearsal_mode:
        machine_role.require_target_machine("showco run")
    x18_recorder = None

    if rehearsal_mode:
        server = make_server(
            host,
            port,
            recs=rehearsal.RehearsalRecsClient(),
            twitcho=rehearsal.RehearsalTwitchoClient(),
            system=rehearsal.RehearsalSystemMonitor(),
            mixer=rehearsal.RehearsalMixerMonitor(),
            twitcho_supervisor=rehearsal.RehearsalTwitchoSupervisor(),
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
    return cli.route_command(
        {
            "run": run_command,
            "provision": provision.main,
            "twitcho": twitcho_command,
        },
        argv,
        prog="showco",
    )


def run_command(arguments: list[str]) -> int:
    if arguments[:1] == ["git-pull"]:
        machine_role.require_target_machine("showco run git-pull")
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


def twitcho_command(arguments: list[str]) -> int:
    machine_role.require_target_machine("showco twitcho")
    return auth.main(arguments)
