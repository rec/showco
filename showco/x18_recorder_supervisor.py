from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path


class X18RecorderSupervisor:
    def __init__(
        self,
        host: str,
        *,
        port: int,
        log_dir: Path,
        python: str = sys.executable,
        run_process: Callable[[list[str]], subprocess.Popen[bytes]] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.log_dir = log_dir
        self.python = python
        self.run_process = run_process or subprocess.Popen
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        if self.process and self.process.poll() is None:
            return
        self.process = self.run_process(self.command())

    def close(self) -> None:
        process = self.process
        if not process or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def command(self) -> list[str]:
        return [
            self.python,
            "-m",
            "showco",
            "x18-record",
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--log-dir",
            str(self.log_dir),
        ]
