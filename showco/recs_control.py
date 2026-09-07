from __future__ import annotations

import threading
import time
from pathlib import Path

from reccy.protocol import rpc
from recs.daemon import paths

CONTROL_TIMEOUT_SECONDS = 6.0


class RecsControlClient:
    def __init__(self, endpoint: Path | str | None = None) -> None:
        self.endpoint = endpoint or paths.external_control_endpoint()
        self.lock = threading.Lock()

    def call(
        self,
        command: str,
        parameters: dict[str, object] | None = None,
        *,
        timeout: float = CONTROL_TIMEOUT_SECONDS,
    ) -> object:
        deadline = time.monotonic() + timeout
        if not self.lock.acquire(timeout=timeout):
            raise TimeoutError(
                f"Recs control request timed out after {timeout}s waiting for access"
            )
        try:
            return rpc.Client(
                self.endpoint,
                role="showco",
                timeout=max(0.0, deadline - time.monotonic()),
            ).call(command, **(parameters or {}))
        finally:
            self.lock.release()
