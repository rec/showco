from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from reccy import process

from .. import models
from . import osc


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

    def status(self) -> models.RecorderStatus:
        process = self.process
        if process is None:
            return models.RecorderStatus(state="stopped")
        if (returncode := process.poll()) is not None:
            return models.RecorderStatus(
                state="error",
                last_error=f"X18 recorder exited with status {returncode}",
            )
        try:
            value = json.loads(osc.recorder_status_path(self.log_dir).read_text())
        except (OSError, json.JSONDecodeError):
            return models.RecorderStatus(state="running")
        path = value.get("path")
        size = value.get("size")
        error = value.get("last_error")
        return models.RecorderStatus(
            state="error" if isinstance(error, str) and error else "running",
            log_path=path if isinstance(path, str) else None,
            log_size=size if isinstance(size, int) else None,
            last_error=error if isinstance(error, str) else None,
        )

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
