from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .models import ActionResult, ServiceStatus

EXTERNAL_RESTART_DELAYS = [0.2, 1.0, 10.0, 20.0]


class TwitchoSupervisorLike(Protocol):
    def start(self) -> None: ...

    def restart(self) -> ActionResult: ...

    def close(self) -> None: ...

    def status(self) -> ServiceStatus: ...


def restart_delay(policy: str, failure_count: int) -> float | None:
    if policy == "internal":
        return 0.0 if failure_count < 2 else 0.2
    if policy == "external":
        if failure_count >= len(EXTERNAL_RESTART_DELAYS):
            return None
        return EXTERNAL_RESTART_DELAYS[failure_count]
    raise ValueError(f"unknown Twitcho restart policy {policy}")


class TwitchoSupervisor:
    def __init__(
        self,
        config: Path,
        *,
        policy: str = "external",
        python: str = sys.executable,
        run_process: Callable[[list[str]], subprocess.Popen[bytes]] | None = None,
    ) -> None:
        restart_delay(policy, 0)
        self.config = config
        self.policy = policy
        self.python = python
        self.run_process = run_process or subprocess.Popen
        self.failure_count = 0
        self.process: subprocess.Popen[bytes] | None = None
        self.thread: threading.Thread | None = None
        self.state = "configured"
        self.last_error: str | None = None
        self.lock = threading.Lock()
        self.stop_requested = threading.Event()
        self.restart_requested = threading.Event()

    def start(self) -> None:
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.stop_requested.clear()
            self.restart_requested.clear()
            self.thread = threading.Thread(
                target=self._run,
                name="TwitchoSupervisor",
                daemon=True,
            )
            self.thread.start()

    def restart(self) -> ActionResult:
        with self.lock:
            self.failure_count = 0
            thread = self.thread
            self.restart_requested.set()
        if not thread or not thread.is_alive():
            self.start()
        else:
            self._terminate_process()
        return ActionResult(True, "twitcho restart requested")

    def close(self) -> None:
        self.stop_requested.set()
        self._terminate_process()
        if self.thread:
            self.thread.join(timeout=5)
        with self.lock:
            self.state = "stopped"

    def status(self) -> ServiceStatus:
        with self.lock:
            return ServiceStatus(
                name="twitcho",
                state=self.state,
                last_error=self.last_error,
            )

    def command(self) -> list[str]:
        return [self.python, "-m", "twitcho", str(self.config)]

    def _run(self) -> None:
        while not self.stop_requested.is_set():
            with self.lock:
                self.state = "starting"
                self.last_error = None
            try:
                process = self.run_process(self.command())
            except OSError as e:
                if not self._restart_after_failure(str(e)):
                    return
                continue

            with self.lock:
                self.process = process
                self.state = "running"

            return_code = self._wait_for_process(process)
            if self.stop_requested.is_set():
                return
            if self.restart_requested.is_set():
                self.restart_requested.clear()
                with self.lock:
                    self.state = "restarting"
                continue
            if return_code == 0:
                with self.lock:
                    self.state = "stopped"
                    self.process = None
                return
            if not self._restart_after_failure(f"twitcho exited with {return_code}"):
                return

    def _wait_for_process(self, process: subprocess.Popen[bytes]) -> int | None:
        while not self.stop_requested.is_set() and not self.restart_requested.is_set():
            if (return_code := process.poll()) is not None:
                return return_code
            self.stop_requested.wait(0.05)
        self._terminate_process()
        return process.poll()

    def _restart_after_failure(self, error: str) -> bool:
        with self.lock:
            delay = restart_delay(self.policy, self.failure_count)
            self.failure_count += 1
            self.process = None
            self.last_error = error
            if delay is None:
                self.state = "failed"
                return False
            self.state = "restarting"
        if delay:
            self.stop_requested.wait(delay)
        return not self.stop_requested.is_set()

    def _terminate_process(self) -> None:
        with self.lock:
            process = self.process
        if not process or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
