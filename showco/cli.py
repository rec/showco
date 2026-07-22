from __future__ import annotations

import argparse

from .server import make_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Showco web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=17_352)
    args = parser.parse_args(argv)

    server = make_server(args.host, args.port)
    print(f"showco listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Interrupted")
    finally:
        server.server_close()
    return 0
