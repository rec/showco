from __future__ import annotations

import socket
import threading
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from unittest import mock

from showco.mixer import MixerMonitor, MixerProbeSpec, MixersMonitor, MixerSpec
from showco.models import MixerStatus


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

    def test_unprobed_mixer_reports_declared_input_progress(self) -> None:
        monitor = MixersMonitor(
            [
                MixerSpec(
                    name="Flow 8",
                    audio_device_names=["FLOW 8"],
                    midi_input_names=["FLOW 8"],
                )
            ]
        )

        waiting = monitor.status(set(), {})[0]
        partial = monitor.status({"FLOW 8"}, {})[0]
        connected = monitor.status({"FLOW 8"}, {"FLOW 8": "recording"})[0]

        self.assertEqual(waiting.state, "waiting")
        self.assertFalse(waiting.audio_ready)
        self.assertFalse(waiting.midi_ready)
        self.assertEqual(partial.state, "partial")
        self.assertEqual(connected.state, "connected")

    def test_waiting_network_mixer_hides_probe_failure(self) -> None:
        monitor = MixersMonitor(
            [
                MixerSpec(
                    name="X18",
                    probe=MixerProbeSpec(host="127.0.0.1", port=1),
                )
            ]
        )

        status = monitor.status(set(), {})[0]

        self.assertEqual(status.state, "waiting")
        self.assertIsNone(status.error)

    def test_network_mixer_reports_failure_after_a_successful_probe(self) -> None:
        monitor = MixersMonitor(
            [
                MixerSpec(
                    name="X18",
                    probe=MixerProbeSpec(host="127.0.0.1", port=1),
                )
            ]
        )
        probe = monitor.monitors["X18"]
        with mock.patch.object(
            probe,
            "status",
            side_effect=[MixerStatus(latency_ms=1.0), MixerStatus(error="offline")],
        ):
            monitor.status(set(), {})
            status = monitor.status(set(), {})[0]

        self.assertEqual(status.state, "error")
        self.assertEqual(status.error, "offline")


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
