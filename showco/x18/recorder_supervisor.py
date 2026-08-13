from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from reccy import process


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
        self.managed_process = process.ManagedProcess(
            self.command(),
            run_process=run_process or subprocess.Popen,
        )

    @property
    def process(self) -> subprocess.Popen[bytes] | None:
        return self.managed_process.process

    @process.setter
    def process(self, value: subprocess.Popen[bytes] | None) -> None:
        self.managed_process.process = value

    def start(self) -> None:
        self.managed_process.start()

    def close(self) -> None:
        self.managed_process.close()

    def command(self) -> list[str]:
        return [
            self.python,
            "-m",
            "showco",
            "run",
            "x18-record",
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--log-dir",
            str(self.log_dir),
        ]
