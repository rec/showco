from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

from showco.runtime import recs_control


class RecsControlTests(unittest.TestCase):
    def test_call_uses_public_endpoint_role_timeout_and_parameters(self) -> None:
        with mock.patch("showco.runtime.recs_control.rpc.Client") as rpc_client:
            rpc_client.return_value.call.return_value = "ok"
            client = recs_control.RecsControlClient("/tmp/recs-control")

            result = client.call("mark", {"label": "test"})

        self.assertEqual(result, "ok")
        (endpoint,) = rpc_client.call_args.args
        self.assertEqual(endpoint, "/tmp/recs-control")
        self.assertEqual(rpc_client.call_args.kwargs["role"], "showco")
        self.assertGreater(rpc_client.call_args.kwargs["timeout"], 5.9)
        self.assertLessEqual(rpc_client.call_args.kwargs["timeout"], 6.0)
        rpc_client.return_value.call.assert_called_once_with("mark", label="test")

    def test_calls_are_serialized(self) -> None:
        active = 0
        maximum = 0
        state_lock = threading.Lock()
        started = threading.Event()

        def call(command: str, **parameters: object) -> str:
            nonlocal active, maximum
            with state_lock:
                active += 1
                maximum = max(maximum, active)
                started.set()
            time.sleep(0.02)
            with state_lock:
                active -= 1
            return "ok"

        with mock.patch("showco.runtime.recs_control.rpc.Client") as rpc_client:
            rpc_client.return_value.call.side_effect = call
            client = recs_control.RecsControlClient("/tmp/recs-control")
            first = threading.Thread(target=client.call, args=("first",))
            second = threading.Thread(target=client.call, args=("second",))

            first.start()
            self.assertTrue(started.wait(0.1))
            second.start()
            first.join()
            second.join()

        self.assertEqual(maximum, 1)

    def test_timeout_includes_waiting_for_another_request(self) -> None:
        client = recs_control.RecsControlClient("/tmp/recs-control")
        client.lock.acquire()
        try:
            with self.assertRaisesRegex(TimeoutError, "waiting for access"):
                client.call("status_snapshot", timeout=0.01)
        finally:
            client.lock.release()


if __name__ == "__main__":
    unittest.main()
