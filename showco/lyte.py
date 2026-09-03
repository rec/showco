from __future__ import annotations

from pathlib import Path

from reccy import rpc

from . import models

CONTROL_ENDPOINT = Path.home() / ".local/state/lyte/gui.sock"


class LyteClient:
    def __init__(
        self, *, enabled: bool, control_endpoint: Path = CONTROL_ENDPOINT
    ) -> None:
        self.enabled = enabled
        self.control_endpoint = control_endpoint

    def status(self) -> models.LyteStatus:
        if not self.enabled:
            return models.LyteStatus(
                service=models.ServiceStatus(name="lyte", state="disabled")
            )
        try:
            result = self._call("status")
        except (ConnectionError, OSError, TimeoutError, ValueError) as error:
            return models.LyteStatus(
                service=models.ServiceStatus(
                    name="lyte", state="offline", last_error=str(error)
                )
            )
        if not isinstance(result, dict):
            return models.LyteStatus(
                service=models.ServiceStatus(
                    name="lyte",
                    state="error",
                    last_error="lyte status reply is not an object",
                )
            )
        error = _status_error(result)
        state = _string(result.get("state")) or "unknown"
        return models.LyteStatus(
            service=models.ServiceStatus(
                name="lyte",
                state="error" if error else "connected",
                last_error=error,
            ),
            daemon_state=state,
            output_state=_string(result.get("output_state")) or "unknown",
            host=_string(result.get("host")),
            device_mac=_string(result.get("device_mac")),
            planned_led_count=_integer(result.get("planned_led_count")),
            actual_led_count=_integer(result.get("actual_led_count")),
            frame_send_count=_integer(result.get("frame_send_count")),
            last_frame_sent_at=_string(result.get("last_frame_sent_at")),
            queued_test=result.get("queued_test") is not None,
            active_test=result.get("active_test") is not None,
        )

    def test(self) -> models.ActionResult:
        if not self.enabled:
            return models.ActionResult(ok=False, message="lyte is disabled")
        try:
            result = self._call("test")
        except (ConnectionError, OSError, TimeoutError, ValueError) as error:
            return models.ActionResult(ok=False, message=f"lyte test failed: {error}")
        if isinstance(result, dict) and result.get("state") == "queued":
            return models.ActionResult(ok=True, message="lyte light test queued")
        return models.ActionResult(ok=False, message="lyte did not queue light test")

    def _call(self, command: str) -> str | dict[str, object]:
        return rpc.Client(self.control_endpoint, role="showco").call(command)


def _status_error(status: dict[str, object]) -> str | None:
    for name in ("output_error", "render_error"):
        if error := _string(status.get(name)):
            return error
    errors = status.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[-1], dict):
        return _string(errors[-1].get("message"))
    return None


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
