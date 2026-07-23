from __future__ import annotations

import argparse
from pathlib import Path

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Showco web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=17_352)
    parser.add_argument("--mixer-host")
    parser.add_argument("--mixer-port", type=int)
    parser.add_argument("--mixer-protocol", choices=["tcp", "udp"], default="tcp")
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
    args = parser.parse_args(argv)

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
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Interrupted")
    finally:
        server.server_close()
    return 0
