from __future__ import annotations

import argparse

from .rehearsal import (
    RehearsalRecsClient,
    RehearsalSystemMonitor,
    RehearsalTwitchoClient,
)
from .server import make_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Showco web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=17_352)
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
        )
        print(f"showco rehearsal listening on http://{args.host}:{args.port}")
    else:
        server = make_server(args.host, args.port)
        print(f"showco listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Interrupted")
    finally:
        server.server_close()
    return 0
