from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .git_pull import main as git_pull_main
from .mixer import MixerMonitor
from .rehearsal import (
    RehearsalMixerMonitor,
    RehearsalRecsClient,
    RehearsalSystemMonitor,
    RehearsalTwitchoClient,
    RehearsalTwitchoSupervisor,
)
from .server import make_server
from .twitcho_supervisor import TwitchoSupervisor
from .x18_osc import X18_OSC_PORT
from .x18_osc import main as x18_record_main
from .x18_recorder_supervisor import X18RecorderSupervisor


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments[:1] == ["git-pull"]:
        return git_pull_main(arguments[1:])
    if arguments[:1] == ["x18-record"]:
        return x18_record_main(arguments[1:])

    parser = argparse.ArgumentParser(description="Run the Showco web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=17_352)
    parser.add_argument("--mixer-host")
    parser.add_argument("--mixer-port", type=int)
    parser.add_argument("--mixer-protocol", choices=["tcp", "udp"], default="tcp")
    parser.add_argument(
        "--x18-host",
        help="start a read-only X18 OSC recorder subprocess for this mixer host",
    )
    parser.add_argument("--x18-port", type=int, default=X18_OSC_PORT)
    parser.add_argument("--x18-log-dir", type=Path, default=Path("."))
    parser.add_argument(
        "--twitcho-config",
        type=Path,
        help="start and supervise Twitcho with this config file",
    )
    parser.add_argument(
        "--twitcho-restart-policy",
        choices=["internal", "external"],
        default="external",
        help="restart policy for the supervised Twitcho process",
    )
    parser.add_argument(
        "--rehearsal",
        action="store_true",
        help="run with simulated recs and twitcho services",
    )
    args = parser.parse_args(arguments)
    x18_recorder = None

    if args.rehearsal:
        server = make_server(
            args.host,
            args.port,
            recs=RehearsalRecsClient(),
            twitcho=RehearsalTwitchoClient(),
            system=RehearsalSystemMonitor(),
            mixer=RehearsalMixerMonitor(),
            twitcho_supervisor=RehearsalTwitchoSupervisor(),
        )
        print(f"showco rehearsal listening on http://{args.host}:{args.port}")
    else:
        twitcho_supervisor = None
        if args.twitcho_config:
            twitcho_supervisor = TwitchoSupervisor(
                args.twitcho_config,
                policy=args.twitcho_restart_policy,
            )
        server = make_server(
            args.host,
            args.port,
            mixer=MixerMonitor(
                host=args.mixer_host,
                port=args.mixer_port,
                protocol=args.mixer_protocol,
            ),
            twitcho_supervisor=twitcho_supervisor,
        )
        print(f"showco listening on http://{args.host}:{args.port}")
    if args.x18_host:
        x18_recorder = X18RecorderSupervisor(
            args.x18_host,
            port=args.x18_port,
            log_dir=args.x18_log_dir,
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
