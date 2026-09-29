from __future__ import annotations

import json
import threading

from pydantic import ValidationError
from reccy.entities import Musician

from . import models, recs_channels, recs_control, recs_snapshot

STATUS_CHANGE_WAIT_SECONDS = 4
STATUS_CHANGE_SAMPLE_COUNT = 3
STATUS_ERROR_LIMIT = 3
STATUS_DIAGNOSIS_SEPARATOR = '\n--- recs service diagnosis ---\n'


class RecsClient:
    def __init__(
        self,
        *,
        control: recs_control.RecsControlClient | None = None,
        snapshot_cache_seconds: float = recs_snapshot.STATUS_SNAPSHOT_CACHE_SECONDS,
        snapshot_timeout_seconds: float = recs_snapshot.STATUS_SNAPSHOT_TIMEOUT_SECONDS,
    ) -> None:
        self.control = control or recs_control.RecsControlClient()
        self.track_name_lock = threading.Lock()
        self.snapshot_client = recs_snapshot.RecsSnapshotClient(
            self.control,
            cache_seconds=snapshot_cache_seconds,
            timeout_seconds=snapshot_timeout_seconds,
        )

    def status(self) -> models.RecsStatus:
        snapshot = self.snapshot_client.status()
        rows = snapshot.rows
        totals = rows[0] if rows else {}
        return models.RecsStatus(
            service=models.ServiceStatus(
                name='recs',
                state=snapshot.service_state,
                last_error=snapshot.error,
            ),
            snapshot_available=snapshot.has_snapshot,
            recording=snapshot.has_snapshot and snapshot.service_state == 'connected',
            paused=snapshot.paused,
            elapsed_seconds=recs_channels._float(totals.get('time')),
            recorded_seconds=recs_channels._float(totals.get('recorded')),
            file_size=recs_channels._float(totals.get('file_size')),
            file_count=_int(totals.get('file_count')),
            channels=recs_channels.channel_levels(rows),
            errors=snapshot.errors,
            snapshot_error=snapshot.error,
            disk=snapshot.disk,
            disk_error=snapshot.error,
            playback=snapshot.playback,
            osc=snapshot.osc,
            midi=snapshot.midi,
        )

    def play(self) -> models.ActionResult:
        if self.status().playback.state == 'paused':
            return self.action('continue_playback')
        return self.action('play_session', session=-1)

    def calibrate(
        self, device: str = '', channels: list[int] | None = None
    ) -> models.ActionResult:
        if device or channels is not None:
            if not device:
                return models.ActionResult(
                    ok=False, message='recs calibration device is missing'
                )
            if not channels:
                return models.ActionResult(
                    ok=False, message='recs calibration channels are missing'
                )
            parameters: dict[str, object] | None = {'channels': {device: channels}}
        else:
            parameters = None
        response = self._control_command('calibrate', parameters)
        if isinstance(response, models.ActionResult):
            return response
        if calibrated_response(response):
            return models.ActionResult(ok=True, message='recs calibration succeeded')
        return models.ActionResult(
            ok=False, message='recs did not send calibrated response'
        )

    def musicians(self) -> dict[str, Musician] | models.ActionResult:
        response = self._control_command('list_musicians')
        if isinstance(response, models.ActionResult):
            return response
        if (
            not recs_snapshot.object_dict(response)
            or response.get('type') != 'musicians'
        ):
            return models.ActionResult(ok=False, message='recs sent invalid musicians')
        values = response.get('musicians')
        if not recs_snapshot.object_dict(values):
            return models.ActionResult(ok=False, message='recs sent invalid musicians')
        try:
            return {
                name: Musician.model_validate(value) for name, value in values.items()
            }
        except ValidationError:
            return models.ActionResult(ok=False, message='recs sent invalid musician')

    def add_musician(
        self,
        name: str,
        other_names: list[str],
        copyright_name: str | None,
        public_keys: list[str],
        links: list[str],
    ) -> models.ActionResult:
        return self._save_musician(
            'add_musician',
            {
                'musician': {
                    'name': name,
                    'other_names': other_names,
                    'copyright_name': copyright_name,
                    'public_keys': public_keys,
                    'links': links,
                }
            },
        )

    def edit_musician(
        self,
        name: str,
        other_names: list[str],
        public_keys: list[str] | None,
        links: list[str],
    ) -> models.ActionResult:
        parameters: dict[str, object] = {
            'name': name,
            'other_names': other_names or None,
            'links': links or None,
            'clear_other_names': not other_names,
            'clear_links': not links,
        }
        if public_keys is not None:
            parameters['public_keys'] = public_keys or None
            parameters['clear_public_keys'] = not public_keys
        return self._save_musician(
            'edit_musician',
            parameters,
        )

    def _save_musician(
        self, command: str, parameters: dict[str, object]
    ) -> models.ActionResult:
        response = self._control_command(command, parameters)
        if isinstance(response, models.ActionResult):
            return response
        if (
            not recs_snapshot.object_dict(response)
            or response.get('type') != 'musician'
            or not recs_snapshot.object_dict(response.get('musician'))
        ):
            return models.ActionResult(
                ok=False, message=f'recs did not confirm {command}'
            )
        try:
            musician = Musician.model_validate(response['musician'])
        except ValidationError:
            return models.ActionResult(
                ok=False, message=f'recs sent invalid {command} musician'
            )
        return models.ActionResult(
            ok=True, message=f'recs saved musician {musician.name}'
        )

    def set_track_name(
        self, device: str, channel: str, track_name: str, expected_name: str | None
    ) -> models.ActionResult:
        device = device.strip()
        channel = channel.strip()
        track_name = track_name.strip()
        if not device:
            return models.ActionResult(
                ok=False, message='recs track name device is missing'
            )
        if not channel:
            return models.ActionResult(
                ok=False, message='recs track name channel is missing'
            )
        if expected_name is None:
            return models.ActionResult(
                ok=False, message='Original track name is missing; reload the page'
            )

        with self.track_name_lock:
            track_names = self.track_names()
            if isinstance(track_names, models.ActionResult):
                return track_names
            channel_number = recs_channels.track_channel(device, channel, track_names)
            if channel_number is None:
                return models.ActionResult(
                    ok=False,
                    message=f'could not resolve recs channel {channel} for {device}',
                )
            current_name = recs_channels.track_name(track_names, device, channel_number)
            if not current_name:
                self.snapshot_client.invalidate()
                status = self.status()
                if not status.snapshot_available or status.service.state != 'connected':
                    return models.ActionResult(
                        ok=False,
                        message=(
                            'Cannot verify current track name; '
                            'retry after recs reconnects'
                        ),
                    )
                reported_name = next(
                    (
                        c.name
                        for c in status.channels
                        if c.device == device and c.channels[:1] == [channel_number]
                    ),
                    None,
                )
                if reported_name is None:
                    return models.ActionResult(
                        ok=False, message='recs channel is no longer available'
                    )
                current_name = reported_name
            if current_name != expected_name:
                self.snapshot_client.invalidate()
                return models.ActionResult(
                    ok=False,
                    message=(
                        'Track name changed in recs; current name is '
                        f'{current_name or "(unnamed)"}. Review it before saving again'
                    ),
                )

            updated = recs_channels.replace_track_name(
                track_names, device, channel_number, track_name
            )
            response = self._control_command(
                'set_track_names',
                {
                    'track_names': updated,
                    'expected_track_names': track_names,
                },
            )
        if isinstance(response, models.ActionResult):
            return response
        if (
            isinstance(response, dict)
            and response.get('type') == 'track_names_conflict'
        ):
            self.snapshot_client.invalidate()
            return models.ActionResult(
                ok=False,
                message=(
                    'Track names changed in recs; '
                    'review the current names before saving again'
                ),
            )
        if response == 'ok':
            self.snapshot_client.invalidate()
            if track_name:
                return models.ActionResult(
                    ok=True, message=f'recs track name set to {track_name}'
                )
            return models.ActionResult(
                ok=True, message=f'recs track name cleared for {channel}'
            )
        return models.ActionResult(
            ok=False, message='recs did not confirm track name update'
        )

    def set_stereo(self, device: str, channels: list[int]) -> models.ActionResult:
        with self.track_name_lock:
            tracks = recs_channels.stereo_tracks(
                self.status().channels, device, channels
            )
            if isinstance(tracks, models.ActionResult):
                return tracks
            track_names = self.track_names()
            if isinstance(track_names, models.ActionResult):
                return track_names
            response = self._control_command(
                'set_tracks',
                {
                    'source': device,
                    'tracks': [
                        {
                            'channels': track,
                            'name': recs_channels.track_name(
                                track_names, device, track[0]
                            ),
                        }
                        for track in tracks
                    ],
                },
            )
        if isinstance(response, models.ActionResult):
            return response
        if response == 'ok':
            return models.ActionResult(ok=True, message='recs stereo updated')
        return models.ActionResult(ok=False, message='recs did not update stereo')

    def track_names(self) -> dict[str, dict[str, int]] | models.ActionResult:
        response = self._control_command('get_track_names')
        if isinstance(response, models.ActionResult):
            return response
        if (track_names := recs_channels.track_names_response(response)) is None:
            return models.ActionResult(
                ok=False, message='recs sent invalid track names'
            )
        return track_names

    def mutable_attributes(
        self,
    ) -> list[models.MutableAttribute] | models.ActionResult:
        response = self._control_command('mutable_attributes')
        if isinstance(response, models.ActionResult):
            return response
        if not isinstance(response, dict):
            return models.ActionResult(
                ok=False,
                message='recs did not send mutable attributes',
            )
        if response.get('type') != 'mutable_attributes_result':
            return models.ActionResult(
                ok=False,
                message='recs sent invalid mutable attributes',
            )
        address_values = response.get('mutable_attributes')
        if not isinstance(address_values, list):
            return models.ActionResult(
                ok=False,
                message='recs sent invalid mutable attributes',
            )
        addresses = [a for a in address_values if isinstance(a, str)]
        if len(addresses) != len(address_values):
            return models.ActionResult(
                ok=False,
                message='recs sent invalid mutable attributes',
            )
        attributes: list[models.MutableAttribute] = []
        for address in addresses:
            value = self._control_command('get_cfg', {'address': address})
            if isinstance(value, models.ActionResult):
                return value
            if (
                not recs_snapshot.object_dict(value)
                or value.get('type') != 'cfg_value'
                or value.get('address') != address
            ):
                return models.ActionResult(
                    ok=False,
                    message=f'recs did not send {address} value',
                )
            attributes.append(
                models.MutableAttribute(
                    address=address,
                    value=value.get('value'),
                )
            )
        return attributes

    def set_attr(self, address: str, value: object) -> models.ActionResult:
        response = self._control_command(
            'set_cfg', {'address': address, 'value': value}
        )
        if isinstance(response, models.ActionResult):
            return response
        if response != 'ok':
            return models.ActionResult(
                ok=False,
                message=f'recs did not set {address}',
            )
        return models.ActionResult(ok=True, message=f'recs set {address}')

    def action(self, command: str, **fields: object) -> models.ActionResult:
        if command not in ACTION_COMMANDS:
            return models.ActionResult(
                ok=False, message=f'recs does not support {command}'
            )
        parameters = {k: v for k, v in fields.items() if v not in ('', None)}
        response = self._control_command(command, parameters or None)
        if command in PLAYBACK_COMMANDS:
            self.snapshot_client.invalidate()
        if isinstance(response, models.ActionResult):
            return response
        if command in DATA_RESPONSE_TYPES:
            if valid_data_response(command, response):
                return models.ActionResult(
                    ok=True,
                    message=command_result_message(command, response),
                )
        elif response == 'ok':
            return models.ActionResult(
                ok=True,
                message=command_result_message(command, response),
            )
        return models.ActionResult(
            ok=False, message=f'recs sent invalid {command} response'
        )

    def pause_recording(self) -> bool | models.ActionResult:
        response = self._control_command('pause_recording')
        if isinstance(response, models.ActionResult):
            return response
        if (
            not recs_snapshot.object_dict(response)
            or response.get('type') != 'recording_state'
            or response.get('paused') is not True
            or not isinstance(was_paused := response.get('was_paused'), bool)
        ):
            return models.ActionResult(
                ok=False, message='recs sent invalid pause_recording response'
            )
        return not was_paused

    def shutdown(self) -> models.ActionResult:
        response = self._control_command('shutdown')
        if isinstance(response, models.ActionResult):
            return response
        if response == 'ok':
            return models.ActionResult(ok=True, message='recs shutdown requested')
        return models.ActionResult(ok=False, message='recs did not confirm shutdown')

    def _control_command(
        self, command: str, parameters: dict[str, object] | None = None
    ) -> object | models.ActionResult:
        try:
            if parameters is None:
                return self.control.call(command)
            return self.control.call(command, parameters)
        except (
            ConnectionError,
            OSError,
            TimeoutError,
            ValidationError,
            ValueError,
        ) as error:
            return models.ActionResult(
                ok=False, message=f'recs {command} failed: {error}'
            )


def status_changes_command() -> str:
    return (
        'status="$HOME/.local/state/recs/status.json"; '
        'diagnose() { '
        'cat "$status" 2>&1; '
        "printf '\\n--- recs service diagnosis ---\\n'; "
        'systemctl --user show recs.service '
        '--property=LoadState,ActiveState,SubState,Result,ExecMainStatus --no-pager '
        '2>&1 || true; '
        "printf '\\nRecent recs journal entries:\\n'; "
        'journalctl --user --unit=recs.service --lines=25 --no-pager 2>&1 || true; '
        "printf '\\nRecent recs service log entries:\\n'; "
        'tail --lines=50 "$HOME/.local/state/recs/recs.log" 2>&1 || true; '
        '}; '
        'updated_at() { sed -nE '
        '\'s/.*"updated_at"[[:space:]]*:[[:space:]]*'
        '([0-9]+([.][0-9]+)?).*/\\1/p\' "$status"; }; '
        'previous=""; '
        f'for sample in $(seq {STATUS_CHANGE_SAMPLE_COUNT}); do '
        'current=$(updated_at); '
        'if [ -z "$current" ] || '
        '{ [ -n "$previous" ] && [ "$previous" = "$current" ]; }; then '
        'diagnose; exit 1; fi; '
        'previous="$current"; '
        f'[ "$sample" = {STATUS_CHANGE_SAMPLE_COUNT} ] || '
        f'sleep {STATUS_CHANGE_WAIT_SECONDS}; '
        'done'
    )


def status_failure_summary(output: str) -> str:
    status, separator, diagnosis = output.partition(STATUS_DIAGNOSIS_SEPARATOR)
    try:
        data = json.loads(status)
    except json.JSONDecodeError:
        return output.strip()
    if not isinstance(data, dict):
        return output.strip()
    result = 'recs status did not advance'
    if isinstance(updated_at := data.get('updated_at'), int | float):
        result += f'; updated_at={updated_at}'
    errors = data.get('errors')
    if isinstance(errors, list):
        messages = [error_message(e) for e in errors]
        messages = [m for m in messages if m]
        if messages:
            result += '\nRecent recs errors:\n' + '\n'.join(
                f'- {m}' for m in messages[-STATUS_ERROR_LIMIT:]
            )
    if separator and diagnosis.strip():
        result += '\nrecs service diagnosis:\n' + diagnosis.strip()
    return result


def calibrated_response(value: object) -> bool:
    if not recs_snapshot.object_dict(value) or value.get('type') != 'calibrated':
        return False
    measurements = value.get('measurements')
    noise_floors = value.get('noise_floors')
    return (
        recs_snapshot.object_dict(measurements)
        and all(_number(v) is not None for v in measurements.values())
        and recs_snapshot.object_dict(noise_floors)
        and all(
            recs_snapshot.object_dict(v)
            and all(_number(n) is not None for n in v.values())
            for v in noise_floors.values()
        )
    )


def valid_data_response(command: str, value: object) -> bool:
    if not recs_snapshot.object_dict(value):
        return False
    if value.get('type') != DATA_RESPONSE_TYPES[command]:
        return False
    if command == 'capabilities':
        commands = value.get('commands')
        version = value.get('version')
        return (
            isinstance(commands, list)
            and all(isinstance(v, str) for v in commands)
            and isinstance(version, int)
            and not isinstance(version, bool)
        )
    if command == 'card_replace':
        return all(
            isinstance(value.get(k), str) and bool(value.get(k))
            for k in ('deadline', 'old_mount', 'old_uuid')
        )
    if command == 'new_session':
        return all(
            isinstance(value.get(k), str) and bool(value.get(k))
            for k in (
                'session_id',
                'session_directory',
                'previous_record_path',
                'record_path',
            )
        )
    if command == 'disk_status':
        disk = {k: v for k, v in value.items() if k != 'type'}
        return not isinstance(recs_snapshot.recording_disk_status(disk), str)
    if command == 'list_devices':
        devices = value.get('devices')
        return isinstance(devices, list) and all(valid_device(v) for v in devices)
    if command == 'status_snapshot':
        return not isinstance(recs_snapshot.snapshot_status(value), str)
    if command == 'pause_recording':
        return value.get('paused') is True and isinstance(value.get('was_paused'), bool)
    if command in PLAYBACK_COMMANDS:
        playback = {k: v for k, v in value.items() if k != 'type'}
        return not isinstance(recs_snapshot.playback_status(playback), str)
    return False


def valid_device(value: object) -> bool:
    if not recs_snapshot.object_dict(value):
        return False
    channels = value.get('channels')
    sample_rate = _number(value.get('sample_rate'))
    return (
        isinstance(value.get('name'), str)
        and isinstance(channels, int)
        and not isinstance(channels, bool)
        and channels > 0
        and sample_rate is not None
        and sample_rate > 0
        and isinstance(value.get('online'), bool)
    )


def command_result_message(command: str, response: object) -> str:
    if recs_snapshot.object_dict(response):
        response = {k: v for k, v in response.items() if k != 'type'}
    elif response == 'ok':
        return f'recs {command} succeeded'
    text = json.dumps(response, sort_keys=True)
    if len(text) > 500:
        text = text[:497] + '...'
    return f'recs {command} succeeded: {text}'


def error_message(value: object) -> str:
    if isinstance(value, str):
        return value
    if recs_snapshot.object_dict(value) and isinstance(
        message := value.get('message'), str
    ):
        return message
    return ''


def _int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _number(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


ACTION_COMMANDS = {
    'capabilities',
    'card_replace',
    'disk_status',
    'list_devices',
    'mark',
    'new_session',
    'pause_recording',
    'pause_playback',
    'play_session',
    'reload_profiles',
    'resume_recording',
    'set_key_label',
    'set_noise_floor',
    'status_snapshot',
    'stop_playback',
    'continue_playback',
    'jump_playback',
    'jump_session',
}

DATA_RESPONSE_TYPES = {
    'capabilities': 'capabilities_result',
    'card_replace': 'card_replace_started',
    'disk_status': 'disk_status_result',
    'list_devices': 'devices',
    'new_session': 'new_session_started',
    'pause_recording': 'recording_state',
    'pause_playback': 'playback_state',
    'play_session': 'playback_state',
    'status_snapshot': 'status_snapshot_result',
    'stop_playback': 'playback_state',
    'continue_playback': 'playback_state',
    'jump_playback': 'playback_state',
    'jump_session': 'playback_state',
}

PLAYBACK_COMMANDS = {
    'pause_playback',
    'play_session',
    'stop_playback',
    'continue_playback',
    'jump_playback',
    'jump_session',
}
