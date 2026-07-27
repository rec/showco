from __future__ import annotations

import json
import os
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from recs.daemon.gui_backend import client_connection
from recs.daemon.gui_protocol import Command, Error, Hello, Reply, parse_message
from recs.daemon.models import DaemonMetadata

from .models import ActionResult, ChannelLevel, RecsStatus, ServiceStatus

STALE_AFTER_SECONDS = 3.0
WINDOWS_PIPE = r"\\.\pipe\recs"


class RecsClient:
    def __init__(
        self,
        *,
        status_path: Path | None = None,
        metadata_path: Path | None = None,
        stale_after_seconds: float = STALE_AFTER_SECONDS,
    ) -> None:
        paths = recs_paths()
        self.status_path = status_path or paths.status
        self.metadata_path = metadata_path or paths.metadata
        self.stale_after_seconds = stale_after_seconds

    def status(self) -> RecsStatus:
        if not self.status_path.exists():
            return RecsStatus(
                service=ServiceStatus(
                    name="recs",
                    state="offline",
                    last_error=f"{self.status_path} does not exist",
                )
            )

        try:
            data = json.loads(self.status_path.read_text())
        except json.JSONDecodeError as e:
            return RecsStatus(
                service=ServiceStatus(
                    name="recs",
                    state="error",
                    last_error=f"invalid status JSON: {e.msg}",
                )
            )
        except OSError as e:
            return RecsStatus(
                service=ServiceStatus(
                    name="recs",
                    state="error",
                    last_error=f"could not read status JSON: {e}",
                )
            )

        if not isinstance(data, dict):
            return RecsStatus(
                service=ServiceStatus(
                    name="recs",
                    state="error",
                    last_error="status JSON is not an object",
                )
            )

        updated_at = _float(data.get("updated_at"))
        gui_ipc_error = _string(data.get("gui_ipc_error"))
        state = _connection_state(updated_at, self.stale_after_seconds)
        rows = _rows(data.get("rows"))
        totals = rows[0] if rows else {}

        return RecsStatus(
            service=ServiceStatus(
                name="recs",
                state=state,
                last_error=gui_ipc_error,
                updated_at=updated_at,
            ),
            recording=bool(data.get("recording")),
            elapsed_seconds=_float(totals.get("time")),
            recorded_seconds=_float(totals.get("recorded")),
            file_size=_float(totals.get("file_size")),
            file_count=_int(totals.get("file_count")),
            client_count=_int(data.get("client_count")) or 0,
            channels=channel_levels(rows),
        )

    def calibrate(self) -> ActionResult:
        try:
            metadata = self._metadata()
        except (OSError, ValueError) as e:
            return ActionResult(False, f"could not read recs metadata: {e}")
        if metadata is None:
            return ActionResult(False, f"{self.metadata_path} does not exist")

        try:
            connection = client_connection(_endpoint(metadata.gui_endpoint))
        except OSError as e:
            return ActionResult(False, f"could not connect to recs: {e}")
        try:
            if not connection.write(
                Hello(type="hello", role="gui").model_dump_json() + "\n"
            ):
                return ActionResult(False, "could not send recs hello")
            if error := _expect_daemon_hello(_read_message(connection)):
                return ActionResult(False, error)

            message_id = str(uuid.uuid4())
            if not connection.write(
                Command(
                    type="command",
                    id=message_id,
                    command="calibrate",
                ).model_dump_json()
                + "\n"
            ):
                return ActionResult(False, "could not send recs calibrate command")

            return _calibration_result(_read_message(connection), message_id)
        except (OSError, ValueError) as e:
            return ActionResult(False, f"recs calibration failed: {e}")
        finally:
            connection.close()

    def _metadata(self) -> DaemonMetadata | None:
        if not self.metadata_path.exists():
            return None
        return DaemonMetadata.model_validate_json(self.metadata_path.read_text())


class RecsPaths:
    def __init__(self, metadata: Path, status: Path, gui_endpoint: str) -> None:
        self.metadata = metadata
        self.status = status
        self.gui_endpoint = gui_endpoint


class RecsConnection(Protocol):
    def read_lines(self) -> Iterator[str]: ...

    def write(self, message: str) -> bool: ...

    def close(self) -> None: ...


def recs_paths(home: Path | None = None) -> RecsPaths:
    home = home or Path.home()
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA", home / "AppData/Roaming"))
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
        return RecsPaths(
            metadata=appdata / "recs/daemon.json",
            status=local / "recs/status.json",
            gui_endpoint=WINDOWS_PIPE,
        )
    return RecsPaths(
        metadata=home / ".config/recs/daemon.json",
        status=home / ".local/state/recs/status.json",
        gui_endpoint=str(home / ".local/state/recs/gui.sock"),
    )


def _endpoint(endpoint: str) -> Path | str:
    if endpoint == WINDOWS_PIPE:
        return endpoint
    return Path(endpoint)


def _read_message(connection: RecsConnection) -> object:
    for line in connection.read_lines():
        return parse_message(line)
    return Error(type="error", message="recs closed the connection")


def _expect_daemon_hello(message: object) -> str | None:
    if isinstance(message, Error):
        return message.message
    if not isinstance(message, Hello) or message.role != "daemon":
        return "recs did not send daemon hello"
    return None


def _calibration_result(message: object, message_id: str) -> ActionResult:
    if isinstance(message, Error):
        return ActionResult(False, message.message)
    if not isinstance(message, Reply):
        return ActionResult(False, "recs did not send calibration reply")
    if message.id != message_id:
        return ActionResult(False, "recs sent reply for a different command")
    if message.ok:
        return ActionResult(True, "recs calibration succeeded")
    return ActionResult(False, message.message or "recs calibration failed")


def channel_levels(rows: list[dict[str, object]]) -> list[ChannelLevel]:
    channels = []
    for row in rows:
        if not isinstance(name := row.get("channel"), str):
            continue
        signal = _float(row.get("signal"))
        channels.append(
            ChannelLevel(name=name, state=level_state(signal), signal=signal)
        )
    return channels


def level_state(signal: float | None) -> str:
    if signal is None or signal < 0.001:
        return "silent"
    if signal < 1 / 3:
        return "present"
    if signal < 0.9:
        return "healthy"
    return "clipping"


def _connection_state(updated_at: float | None, stale_after_seconds: float) -> str:
    if updated_at is None:
        return "connected"
    if time.time() - updated_at > stale_after_seconds:
        return "stale"
    return "connected"


def _rows(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, object]] = []
    for r in value:
        if isinstance(r, dict):
            row: dict[str, object] = {}
            for k, v in r.items():
                if isinstance(k, str):
                    row[k] = v
            rows.append(row)
    return rows


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _float(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None
