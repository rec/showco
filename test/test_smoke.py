from __future__ import annotations

import socket
import threading
import unittest
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

from showco.rehearsal import RehearsalRecsClient, RehearsalTwitchoClient
from showco.server import make_server


class SmokeTests(unittest.TestCase):
    def test_rehearsal_server_serves_home_and_accepts_actions(self) -> None:
        recs = RehearsalRecsClient()
        twitcho = RehearsalTwitchoClient()
        with running_rehearsal_server(recs, twitcho) as url:
            home = read_url(f"{url}/home")
            self.assertIn("Recording channels", home)
            self.assertIn("Streaming", home)

            response = post_form(f"{url}/actions", {"action": "twitcho-mute"})
            self.assertEqual(response.status, 200)
            self.assertTrue(twitcho.status().muted)

            actions = response.read().decode()
            self.assertIn("rehearsal twitcho mute succeeded", actions)

    def test_rehearsal_server_exercises_recs_calibration(self) -> None:
        recs = RehearsalRecsClient()
        twitcho = RehearsalTwitchoClient()
        with running_rehearsal_server(recs, twitcho) as url:
            response = post_form(f"{url}/actions", {"action": "recs-calibrate"})

            self.assertEqual(response.status, 200)
            self.assertEqual(recs.calibration_count, 1)
            self.assertIn("rehearsal recs calibration 1", response.read().decode())


@contextmanager
def running_rehearsal_server(
    recs: RehearsalRecsClient, twitcho: RehearsalTwitchoClient
) -> Iterator[str]:
    server = make_server("127.0.0.1", unused_port(), recs=recs, twitcho=twitcho)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        stop_server(server, thread)


def read_url(url: str) -> str:
    with urllib.request.urlopen(url, timeout=2) as response:
        return response.read().decode()


def post_form(url: str, form: dict[str, str]) -> object:
    data = urllib.parse.urlencode(form).encode()
    opener = urllib.request.build_opener(NoRedirectHandler)
    return opener.open(url, data=data, timeout=2)


def stop_server(server: ThreadingHTTPServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> urllib.request.Request | None:
        if code == 303:
            return urllib.request.Request(newurl, method="GET")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


if __name__ == "__main__":
    unittest.main()
