import json

from . import models
from .recs import RecsClient


def run(recs: RecsClient, action: str, form: dict[str, str]) -> models.ActionResult:
    if action == 'recs-calibrate':
        device = form.get('device', '')
        channels = _channel_numbers(form.get('channels', ''))
        if device or channels:
            return recs.calibrate(device, channels)
        return recs.calibrate()
    if action in {'recs-musician-add', 'recs-musician-edit'}:
        return _save_musician(recs, action, form)
    if action == 'recs-track-name':
        return recs.set_track_name(
            form.get('device', ''),
            form.get('channel', ''),
            form.get('track_name', ''),
            form.get('expected_name'),
        )
    if action == 'recs-set-stereo':
        return recs.set_stereo(
            form.get('device', ''), _channel_numbers(form.get('channels', ''))
        )
    if action == 'recs-set-attr':
        try:
            value = json.loads(form.get('value', ''))
        except json.JSONDecodeError:
            return models.ActionResult(
                ok=False,
                message='recs attribute value must be valid JSON',
            )
        return recs.set_attr(form.get('address', ''), value)
    if action == 'recs-shutdown':
        if form.get('confirmation') == 'shutdown':
            return recs.shutdown()
        return models.ActionResult(ok=True, message='recs shutdown canceled')
    if action == 'recs-playback-play':
        return recs.play()
    if action in RECS_ACTIONS:
        return recs.action(RECS_ACTIONS[action], **_recs_fields(form))
    return models.ActionResult(ok=False, message=f'unknown action {action}')


def _save_musician(
    recs: RecsClient, action: str, form: dict[str, str]
) -> models.ActionResult:
    name = form.get('name', '').strip()
    if not name:
        return models.ActionResult(ok=False, message='Musician name is required')
    other_names = _text_lines(form.get('other_names', ''))
    copyright_name = form.get('copyright_name', '').strip() or None
    public_keys = _text_lines(form['public_keys']) if 'public_keys' in form else None
    links = _text_lines(form.get('links', ''))
    if action == 'recs-musician-add':
        return recs.add_musician(
            name, other_names, copyright_name, public_keys or [], links
        )
    return recs.edit_musician(name, other_names, public_keys, links)


def _channel_numbers(value: str) -> list[int]:
    if not value.strip():
        return []
    try:
        return [int(n) for n in value.split(',')]
    except ValueError:
        raise ValueError(
            'channels must be a comma-separated list of integers'
        ) from None


def _recs_fields(form: dict[str, str]) -> dict[str, object]:
    fields: dict[str, object] = {}
    for k, v in form.items():
        if k == 'action' or not v:
            continue
        if k in {'noise_floor', 'seconds'}:
            try:
                fields[k] = float(v)
            except ValueError:
                raise ValueError(f'{k} must be a number') from None
        elif k in {'offset', 'session'}:
            try:
                fields[k] = int(v)
            except ValueError:
                raise ValueError(f'{k} must be an integer') from None
        else:
            fields[k] = v
    return fields


def _text_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


RECS_ACTIONS = {
    'recs-capabilities': 'capabilities',
    'recs-disk-status': 'disk_status',
    'recs-key-label': 'set_key_label',
    'recs-list-devices': 'list_devices',
    'recs-marker': 'mark',
    'recs-new-session': 'new_session',
    'recs-pause-recording': 'pause_recording',
    'recs-playback-jump': 'jump_playback',
    'recs-playback-jump-session': 'jump_session',
    'recs-playback-pause': 'pause_playback',
    'recs-playback-stop': 'stop_playback',
    'recs-reload-profiles': 'reload_profiles',
    'recs-resume-recording': 'resume_recording',
    'recs-set-noise-floor': 'set_noise_floor',
    'recs-status-snapshot': 'status_snapshot',
}
