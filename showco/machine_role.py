from __future__ import annotations

import os
import sys
from pathlib import Path

TARGET_ROLE = "target"
ROLE_FILE_ENVIRONMENT_VARIABLE = "SHOWCO_MACHINE_ROLE_FILE"


def mark_target_machine() -> None:
    path = role_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{TARGET_ROLE}\n")


def require_target_machine(command: str) -> None:
    if machine_role() == TARGET_ROLE:
        return
    sys.exit(
        f"ERROR: {command} must run on a provisioned Showco target machine. "
        f"Missing {role_file()}."
    )


def require_provisioning_machine(command: str) -> None:
    if machine_role() == TARGET_ROLE:
        sys.exit(
            f"ERROR: {command} must run on the provisioning machine, not on a "
            "provisioned Showco target machine."
        )


def machine_role() -> str:
    path = role_file()
    if not path.exists():
        return ""
    return path.read_text().strip()


def role_file() -> Path:
    if path := os.environ.get(ROLE_FILE_ENVIRONMENT_VARIABLE):
        return Path(path)
    return Path.home() / ".config/showco/machine-role"
