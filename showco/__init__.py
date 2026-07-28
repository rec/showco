"""Local show control for recs and twitcho."""

import subprocess
from collections.abc import Mapping, Sequence

__all__ = ["__version__", "run"]

__version__ = "0.1.0"


def run(
    command: Sequence[str],
    *,
    capture_output: bool = True,
    check: bool = True,
    env: Mapping[str, str] | None = None,
    text: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=capture_output,
        check=check,
        env=env,
        text=text,
        timeout=timeout,
    )
