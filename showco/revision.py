from __future__ import annotations

import shlex
from pathlib import Path


def showco_revision_command(root: Path, *, retry: bool) -> str:
    showco_directory = shlex.quote(str(root / "showco"))
    retry_options = ""
    if retry:
        retry_options = "--retry 5 --retry-connrefused --retry-delay 1 "
    else:
        retry_options = "--max-time 5 "
    return (
        f"expected=$(git -C {showco_directory} rev-parse HEAD) && "
        f"curl --fail --silent --show-error {retry_options}"
        "http://127.0.0.1:17352/status | "
        'grep --fixed-strings "\\"revision\\":\\"$expected\\""'
    )
