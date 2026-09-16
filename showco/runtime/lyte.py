from __future__ import annotations

from pathlib import Path

from reccy.protocol import rpc

from . import models

CONTROL_ENDPOINT = Path.home() / '.local/state/lyte/gui.sock'
LIGHT_TEST_LEVEL = 30.0
LIGHT_TEST_DURATION = 1.0


class LyteClient:
    def __init__(
        self, *, enabled: bool, control_endpoint: Path = CONTROL_ENDPOINT
    ) -> None:
        self.enabled = enabled
        self.control_endpoint = control_endpoint

    def status(self) -> models.LyteStatus:
        if not self.enabled:
            return models.LyteStatus(
                service=models.ServiceStatus(name='lyte', state='disabled')
            )
        try:
            result = self._call('status')
        except (ConnectionError, OSError, TimeoutError, ValueError) as error:
            return models.LyteStatus(
                service=models.ServiceStatus(
                    name='lyte', state='offline', last_error=str(error)
                )
            )
        if not isinstance(result, dict):
            return models.LyteStatus(
                service=models.ServiceStatus(
                    name='lyte',
                    state='error',
                    last_error='lyte status reply is not an object',
                )
            )
        animations = result.get('animations')
        strings = _string_statuses(result.get('strings'))
        error = _status_error(result, strings)
        return models.LyteStatus(
            service=models.ServiceStatus(
                name='lyte',
                state='error' if error else 'connected',
                last_error=error,
            ),
            running=result.get('running') is True,
            animations=[a for a in animations if isinstance(a, str)]
            if isinstance(animations, list)
            else [],
            blackout=result.get('blackout') is True,
            active_animation=_string(result.get('active_animation')),
            queued_animation=_string(result.get('queued_animation')),
            strings=strings,
            bindings=_string_dict(result.get('bindings')),
            midi_connected=result.get('midi_connected') is True,
            midi_error=_string(result.get('midi_error')),
            note=_integer(result.get('note')),
            breath=_integer(result.get('breath')),
            pitch_bend=_integer(result.get('pitch_bend')),
            queued_test=result.get('queued_test') is not None,
            active_test=result.get('active_test') is not None,
        )

    def test(self) -> models.ActionResult:
        if not self.enabled:
            return models.ActionResult(ok=False, message='lyte is disabled')
        try:
            result = self._call(
                'test', level=LIGHT_TEST_LEVEL, duration=LIGHT_TEST_DURATION
            )
        except (ConnectionError, OSError, TimeoutError, ValueError) as error:
            return models.ActionResult(ok=False, message=f'lyte test failed: {error}')
        if isinstance(result, dict) and result.get('state') == 'queued':
            return models.ActionResult(ok=True, message='lyte light test queued')
        return models.ActionResult(ok=False, message='lyte did not queue light test')

    def select_animation(self, name: str) -> models.ActionResult:
        if not self.enabled:
            return models.ActionResult(ok=False, message='lyte is disabled')
        result = self._call('select_animation', name=name)
        if (
            isinstance(result, dict)
            and result.get('state') == 'queued'
            and result.get('name') == name
        ):
            return models.ActionResult(ok=True, message=f'lyte queued {name}')
        return models.ActionResult(
            ok=False, message='lyte selection was not acknowledged'
        )

    def _call(self, command: str, **params: object) -> str | dict[str, object]:
        return rpc.Client(self.control_endpoint, role='showco').call(command, **params)


def _status_error(
    status: dict[str, object], strings: dict[str, models.LyteStringStatus]
) -> str | None:
    errors = status.get('errors')
    if isinstance(errors, list) and errors and isinstance(errors[-1], dict):
        if error := _string(errors[-1].get('message')):
            return error
    if error := _string(status.get('midi_error')):
        return f'MIDI: {error}'
    for name, value in strings.items():
        if value.last_error:
            return f'{name}: {value.last_error}'
    return None


def _string_statuses(value: object) -> dict[str, models.LyteStringStatus]:
    if not isinstance(value, dict):
        return {}
    result = {}
    for name, status in value.items():
        if not isinstance(name, str) or not isinstance(status, dict):
            continue
        result[name] = models.LyteStringStatus(
            state=_string(status.get('state')) or 'unknown',
            host=_string(status.get('host')),
            mac=_string(status.get('mac')),
            led_count=_integer(status.get('led_count')),
            frame_count=_integer(status.get('frame_count')) or 0,
            failure_count=_integer(status.get('failure_count')) or 0,
            last_error=_string(status.get('last_error')),
        )
    return result


def _string_dict(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
