from __future__ import annotations

import socket
import threading
import unittest
from collections.abc import Iterator
from contextlib import contextmanager

from showco.mixer import MixerMonitor


class MixerTests(unittest.TestCase):
    def test_unconfigured_mixer_probe_reports_error(self) -> None:
        status = MixerMonitor().status()

        self.assertIsNone(status.latency_ms)
        self.assertEqual(status.error, "mixer probe not configured")

    def test_tcp_probe_reports_latency(self) -> None:
        with tcp_server() as port:
            status = MixerMonitor(host="127.0.0.1", port=port).status()

        self.assertIsNotNone(status.latency_ms)
        self.assertIsNone(status.error)


@contextmanager
def tcp_server() -> Iterator[int]:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        thread = threading.Thread(target=accept_one, args=(server,))
        thread.start()
        try:
            yield int(server.getsockname()[1])
        finally:
            thread.join(timeout=2)


def accept_one(server: socket.socket) -> None:
    with server.accept()[0]:
        pass


if __name__ == "__main__":
    unittest.main()
