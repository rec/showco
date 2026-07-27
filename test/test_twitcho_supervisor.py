from __future__ import annotations

import subprocess
import threading
import unittest
from pathlib import Path

from showco.twitcho.supervisor import TwitchoSupervisor, restart_delay


class TwitchoSupervisorTests(unittest.TestCase):
    def test_internal_policy_restarts_immediately_twice_then_backs_off(self) -> None:
        self.assertEqual(restart_delay("internal", 0), 0.0)
        self.assertEqual(restart_delay("internal", 1), 0.0)
        self.assertEqual(restart_delay("internal", 2), 0.2)
        self.assertEqual(restart_delay("internal", 50), 0.2)

    def test_external_policy_backs_off_then_stops_trying(self) -> None:
        self.assertEqual(restart_delay("external", 0), 0.2)
        self.assertEqual(restart_delay("external", 1), 1.0)
        self.assertEqual(restart_delay("external", 2), 10.0)
        self.assertEqual(restart_delay("external", 3), 20.0)
        self.assertIsNone(restart_delay("external", 4))

    def test_command_runs_twitcho_module_with_config_path(self) -> None:
        supervisor = TwitchoSupervisor(Path("twitcho.json"), python="/python")

        self.assertEqual(
            supervisor.command(),
            ["/python", "-m", "twitcho", "twitcho.json"],
        )

    def test_unknown_policy_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            restart_delay("bad", 0)

    def test_spawn_failure_is_preserved_while_retrying(self) -> None:
        calls = 0
        second_spawn_started = threading.Event()
        finish_second_spawn = threading.Event()

        def run_process(command: list[str]) -> subprocess.Popen[bytes]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("first spawn failed")
            second_spawn_started.set()
            finish_second_spawn.wait(1)
            raise OSError("second spawn failed")

        supervisor = TwitchoSupervisor(
            Path("twitcho.json"),
            policy="external",
            run_process=run_process,
        )
        supervisor.start()
        try:
            self.assertTrue(second_spawn_started.wait(1))
            status = supervisor.status()

            self.assertEqual(status.state, "starting")
            self.assertEqual(status.last_error, "first spawn failed")
        finally:
            finish_second_spawn.set()
            supervisor.close()


if __name__ == "__main__":
    unittest.main()
