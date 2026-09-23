from __future__ import annotations

import html
import json
from collections.abc import Mapping
from datetime import datetime
from functools import cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from . import gui_schema, models

SITE_DIRECTORY = Path(__file__).parent.parent.parent / 'site'
GUI_TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).parent.parent / 'templates'),
    autoescape=True,
    undefined=StrictUndefined,
)


@cache
def site_file(name: str) -> str:
    return (SITE_DIRECTORY / name).read_text()


def configured_page(
    page_spec: gui_schema.Page,
    status: models.ShowStatus,
    *,
    mutable_attributes: list[models.MutableAttribute]
    | models.ActionResult
    | None = None,
    musicians: Mapping[str, object] | models.ActionResult | None = None,
    action_log: list[models.ActionLogEntry] | None = None,
    features: set[str] | None = None,
) -> str:
    body = GUI_TEMPLATES.get_template('elements.html.j2').render(
        sections=page_spec.sections,
        status=status,
        bound_value=_gui_value,
        source_data=lambda source, show: _gui_source_data(
            source, show, musicians, action_log
        ),
        row_data=_gui_row,
        is_enabled=_gui_enabled,
        item_text=_gui_item_text,
        list_row_class=_gui_list_class,
        section_class=_gui_section_class,
        status_text=_gui_status_text,
        status_class=_gui_status_class,
        service_text=_gui_service_text,
        metric_data=_gui_metric_data,
        mutable_attributes=_gui_mutable_attributes(mutable_attributes),
        attribute_field=_gui_attribute_field,
        is_disabled=_gui_disabled,
        form_value=_gui_form_value,
        form_action=_gui_form_action,
        is_visible=lambda element, values: _gui_visible(element, values or set()),
        features=features or set(),
    )
    script = site_file('channel-controls.js') + site_file('status-script.js')
    if any(
        child.kind == 'waveform'
        for section in page_spec.sections
        for element in section.elements
        for child in element.children
    ):
        script += site_file('waveform-script.js')
    if any(section.style == 'transport' for section in page_spec.sections):
        script += site_file('playback-script.js')
    if any(
        element.kind.startswith('workflow_')
        for section in page_spec.sections
        for element in _all_elements(section.elements)
    ):
        script += site_file('workflow.js')
    return page(
        page_spec.name,
        body,
        script=script,
    )


def _all_elements(elements: list[gui_schema.Element]) -> list[gui_schema.Element]:
    return [
        element
        for parent in elements
        for element in [parent, *_all_elements(parent.children)]
    ]


def _gui_value(path: str, status: models.ShowStatus, item: object | None) -> object:
    root, *fields = path.split('.')
    value: object = status if root == 'show' else item
    if value is None:
        return ''
    for field in fields:
        value = getattr(value, field)
    return value


def _gui_source_data(
    source: str,
    status: models.ShowStatus,
    musicians: Mapping[str, object] | models.ActionResult | None,
    action_log: list[models.ActionLogEntry] | None,
) -> dict[str, object]:
    if source == 'recs.musicians':
        if isinstance(musicians, models.ActionResult):
            return {'items': [], 'error': musicians.message}
        return {'items': list((musicians or {}).values()), 'error': ''}
    if source == 'show.actions':
        return {'items': action_log or [], 'error': ''}
    value = _gui_value(source, status, None)
    if not isinstance(value, list):
        raise ValueError(f'GUI source {source!r} is not a list')
    return {'items': value, 'error': ''}


def _gui_form_value(element: gui_schema.Element, item: object | None) -> str:
    if not element.value:
        return ''
    _, *fields = element.value.split('.')
    value = item
    for field in fields:
        value = getattr(value, field)
    if element.format == 'lines':
        if not isinstance(value, list):
            raise TypeError(f'{element.name}: form lines value is not a list')
        return '\n'.join(str(line) for line in value)
    return str(value)


def _gui_form_action(action: str) -> str:
    return '/musicians' if action in gui_schema.MUSICIAN_ACTIONS else '/actions'


def _gui_visible(element: gui_schema.Element, features: set[str]) -> bool:
    return not element.visible_when or element.visible_when in features


def _gui_row(source: str, item: object | None) -> dict[str, object]:
    if source != 'show.recs.channels':
        raise ValueError(f'unknown GUI source {source!r}')
    if item is None:
        return {
            'class_name': 'level',
            'attributes': {
                'device': '',
                'channel': '',
                'channels': '',
                'saved-track-name': '',
            },
        }
    if not isinstance(item, models.ChannelLevel):
        raise TypeError(f'unexpected item in {source!r}')
    return {
        'class_name': f'level {item.state}',
        'attributes': {
            'device': item.device,
            'channel': item.name,
            'channels': ','.join(str(n) for n in item.channels),
            'saved-track-name': item.name,
        },
    }


def _gui_enabled(
    condition: str, item: object, items: list[models.ChannelLevel]
) -> bool:
    if condition == 'stereo_pair_available' and isinstance(item, models.ChannelLevel):
        return len(item.channels) == 2 or _stereo_enabled(item, items)
    return True


def _gui_disabled(element: gui_schema.Element, status: models.ShowStatus) -> bool:
    return (
        element.disabled_when == 'playback_waiting'
        and status.recs.playback.state == 'waiting'
    )


def _gui_item_text(
    element: gui_schema.Element, status: models.ShowStatus, item: object | None
) -> str:
    value = _gui_value(element.value, status, item)
    if element.format == 'mixer_detail' and isinstance(value, models.MixerStatus):
        return _mixer_detail(value)
    if element.format == 'osc_detail' and isinstance(value, models.RecorderStatus):
        return _osc_recorder_detail(value)
    if element.format == 'time' and isinstance(value, datetime):
        return value.strftime('%H:%M:%S')
    return str(value)


def _gui_list_class(source: str, item: object | None) -> str:
    if source in {'show.readiness.checks', 'show.input_checks'} and isinstance(
        item, models.ReadinessCheck | models.InputCheck
    ):
        return 'ok' if item.ok else 'failed'
    return ''


def _gui_section_class(style: str, status: models.ShowStatus) -> str:
    if style == 'readiness':
        return f'readiness {"healthy" if status.readiness.ready else "error"}'
    return 'cards' if style == 'cards' else ''


def _gui_status_text(element: gui_schema.Element, status: models.ShowStatus) -> str:
    value = _gui_value(element.value, status, None)
    match element.format:
        case 'readiness':
            return 'ready' if value else 'not ready'
        case 'service' if isinstance(value, models.ServiceStatus):
            return _service_detail(value.state, value.last_error)
        case 'snapshot':
            return str(value or 'connected')
        case 'progress':
            return str(value)
        case 'lyte' if isinstance(value, models.LyteStatus):
            return _lyte_detail(value)
        case 'temperature':
            return _temperature(status)
        case 'bitrate':
            return _bitrate(status)
        case 'playback_state' if isinstance(value, models.PlaybackStatus):
            return value.state
        case 'playback_selection' if isinstance(value, models.PlaybackStatus):
            return playback_selection(value)
        case 'playback_position' if isinstance(value, models.PlaybackStatus):
            return playback_position(value)
        case 'music' if isinstance(value, models.MusicStatus):
            track = str(value.track) if value.track is not None else 'No music playing'
            error = f' {value.error}' if value.error else ''
            return f'Mode: {value.mode}. {track}{error}'
    raise ValueError(f'unsupported status format {element.format!r}')


def _gui_status_class(format: str, status: models.ShowStatus) -> str:
    if format == 'readiness':
        return 'state'
    if format == 'progress':
        return 'ok' if status.recording_progress.ok else 'failed'
    return ''


def _gui_service_text(format: str, status: models.ShowStatus) -> str:
    if format == 'recording':
        return _recording_text(status)
    if format == 'streaming':
        return _streaming_text(status)
    raise ValueError(f'unsupported service format {format!r}')


def _gui_metric_data(name: str, status: models.ShowStatus) -> dict[str, object]:
    if name == 'cpu':
        percent, detail, critical = _cpu_percent(status), _cpu(status), False
    elif name == 'memory':
        percent, detail, critical = _memory_percent(status), _memory(status), False
    else:
        percent, detail = _disk_percent(status.recs), _disk(status.recs)
        disk = status.recs.disk
        critical = disk is not None and (
            disk.alert_active or disk.paused_for_disk_space
        )
    return {
        'percent': percent,
        'detail': detail,
        'state': _performance_state(percent, force_critical=critical),
    }


def _gui_mutable_attributes(
    attributes: list[models.MutableAttribute] | models.ActionResult | None,
) -> dict[str, object]:
    if isinstance(attributes, models.ActionResult):
        return {'items': [], 'error': attributes.message}
    return {'items': attributes or [], 'error': ''}


def _gui_attribute_field(attribute: models.MutableAttribute) -> dict[str, object]:
    value = attribute.value
    if isinstance(value, bool):
        return {
            'type': 'checkbox',
            'value_type': 'boolean',
            'value': '',
            'checked': value,
            'saved_value': json.dumps(value, separators=(',', ':')),
        }
    if isinstance(value, int | float):
        return {
            'type': 'number',
            'value_type': 'number',
            'value': str(value),
            'checked': False,
            'saved_value': json.dumps(value, separators=(',', ':')),
        }
    if isinstance(value, str):
        return {
            'type': 'text',
            'value_type': 'text',
            'value': value,
            'checked': False,
            'saved_value': json.dumps(value, separators=(',', ':')),
        }
    return {
        'type': 'text',
        'value_type': 'json',
        'value': json.dumps(value, separators=(',', ':')),
        'checked': False,
        'saved_value': json.dumps(value, separators=(',', ':')),
    }


def playback_selection(playback: models.PlaybackStatus) -> str:
    if playback.state == 'waiting':
        return 'No session selected'
    return (
        f'Session {playback.session}: {playback.source} channel {playback.channel} '
        f'to output {playback.output_channel}'
    )


def playback_position(playback: models.PlaybackStatus) -> str:
    if playback.position_seconds is None or playback.duration_seconds is None:
        return ''
    position = _duration(playback.position_seconds)
    duration = _duration(playback.duration_seconds)
    return f'{position} / {duration}'


def performance_page() -> str:
    return page(
        'performance',
        site_file('setlist-controls.html')
        + """
      <section class="performance" id="performance-screen">
        <h2>Performance</h2>
        <div class="performance-status" aria-live="polite">
          <p id="performance-recording">Recording: checking</p>
          <p id="performance-disk">Destination and capacity: checking</p>
          <p id="performance-progress">Audio writes: checking</p>
          <p id="performance-stream">Stream: checking</p>
        </div>
        <div class="performance-buttons">
          <button type="button" data-performance-action="recs-marker">
            Mark this moment</button>
          <button type="button" data-performance-action="recs-pause-recording">
            Pause recording</button>
          <button type="button" data-performance-action="recs-resume-recording">
            Resume recording</button>
          <a href="/health">Inspect health</a>
        </div>
        <p id="performance-result" role="status">Ready for an explicit action.</p>
        <div class="display-controls">
          <label><input type="checkbox" id="dim-display"> Dim display</label>
          <button type="button" id="keep-awake">Keep screen awake</button>
          <span id="awake-status" role="status">Screen awake: off</span>
        </div>
        <h3>Pinned inputs</h3>
        <p>Pins and dimming are saved on this browser.
          Unpinning never changes recording.</p>
        <div id="performance-inputs"></div>
        <details><summary>Choose inputs to pin</summary>
          <div id="input-pins"></div></details>
      </section>""",
        script=site_file('performance.js') + site_file('workflow.js'),
    )


def workflow_page(name: str) -> str:
    return page(
        name,
        site_file(f'{name}.html'),
        script=site_file('lighting.js' if name == 'lighting' else 'workflow.js'),
    )


def page(page_id: str, body: str, *, script: str = '') -> str:
    document = gui_schema.current_gui()
    page_spec = document.page(page_id)
    navigation = ''.join(
        f'<a href="/{item.name}">{html.escape(item.title)}</a>'
        for item in document.pages
    )
    page_script = (
        f'<script>{site_file("status-connection.js")}</script>'
        f'<script>{site_file("show-controls.js")}</script>'
        f'<script>{script or "pollShowControls();"}</script>'
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(document.name)} {html.escape(page_spec.title)}</title>
  <style>{site_file('server.css')}</style>
</head>
<body>
  <header>
    <h1>{html.escape(document.name)}</h1>
    <nav>{navigation}</nav>
  </header>
  <main><p id="connection-status" role="status">Connecting...</p>
    <section class="show-controls" aria-label="Performance protection">
      <strong id="performance-lock-state">Performance lock: checking</strong>
      <form id="performance-lock-form" method="post" action="/actions">
        <button name="action" value="performance-lock">Enable performance lock</button>
        <label><input type="checkbox" name="confirmation" value="unlock">
          Confirm unlock</label>
        <button name="action" value="performance-unlock">
          Unlock protected actions</button>
      </form>
      <p>Protects web configuration, calibration, tests, session replacement,
         and shutdown. Separate CLI and deployment commands are not locked.</p>
      <p id="lock-result" role="status"></p>
    </section>
    <section id="fault-banner" class="failed" aria-live="polite">
      Checking for faults...</section>
    <p id="monitoring-error" role="status"></p>
    {body}</main>
  <script>{site_file('shutdown-action.js')}</script>
  {page_script}
</body>
</html>"""


def _stereo_enabled(
    channel: models.ChannelLevel, channels: list[models.ChannelLevel]
) -> bool:
    if len(channel.channels) != 1:
        return False
    return any(
        other.device == channel.device and other.channels == [channel.channels[0] + 1]
        for other in channels
    )


def _recording_text(status: models.ShowStatus) -> str:
    if not status.recs.recording:
        return 'stopped'
    elapsed = _duration(status.recs.elapsed_seconds)
    files = status.recs.file_count if status.recs.file_count is not None else '?'
    if status.recs.paused:
        return f'paused after {elapsed}, {files} files'
    return f'recording for {elapsed}, {files} files'


def _streaming_text(status: models.ShowStatus) -> str:
    state = status.streamo.stream_state
    muted = ', muted' if status.streamo.muted else ''
    return f'{state}{muted}'


def _service_detail(state: str, error: str | None) -> str:
    return f'{state}: {error}' if error else state


def _temperature(status: models.ShowStatus) -> str:
    if status.system.temperature_c is not None:
        return f'{status.system.temperature_c:.1f} °C'
    return status.system.temperature_error or 'unknown'


def _performance_state(percent: float | None, *, force_critical: bool = False) -> str:
    if force_critical or percent is not None and percent >= 95:
        return 'critical'
    if percent is not None and percent >= 85:
        return 'warning'
    return 'normal'


def _cpu_percent(status: models.ShowStatus) -> float | None:
    return status.system.cpu_percent


def _cpu(status: models.ShowStatus) -> str:
    if status.system.cpu_percent is None:
        return status.system.cpu_error or 'unknown'
    return f'{status.system.cpu_percent:.0f}%'


def _memory_percent(status: models.ShowStatus) -> float | None:
    used = status.system.memory_used_bytes
    total = status.system.memory_total_bytes
    if used is None or total is None or total <= 0:
        return None
    return 100 * used / total


def _memory(status: models.ShowStatus) -> str:
    used = status.system.memory_used_bytes
    total = status.system.memory_total_bytes
    percent = _memory_percent(status)
    if used is None or total is None or percent is None:
        return status.system.memory_error or 'unknown'
    return f'{_bytes(used)} / {_bytes(total)} ({percent:.0f}%)'


def _disk_percent(status: models.RecsStatus) -> float | None:
    if status.disk is None or status.disk.total_bytes <= 0:
        return None
    return 100 * status.disk.used_bytes / status.disk.total_bytes


def _disk(status: models.RecsStatus) -> str:
    disk = status.disk
    if disk is None:
        return (
            status.disk_error or status.snapshot_error or 'recording disk unavailable'
        )
    percent = 100 * disk.used_bytes / disk.total_bytes
    detail = (
        f'{disk.path}: {_bytes(disk.free_bytes)} free / {_bytes(disk.total_bytes)}'
        f' ({percent:.0f}% used)'
    )
    if disk.estimated_seconds_remaining is not None:
        detail += f', {_duration(disk.estimated_seconds_remaining)} remaining'
    if disk.paused_for_disk_space:
        detail = f'paused: {detail}'
    elif disk.alert_active:
        detail = f'alert: {detail}'
    if status.disk_error or status.snapshot_error:
        detail += f', stale: {status.disk_error or status.snapshot_error}'
    return detail


def _bytes(value: int) -> str:
    if value >= 1024**3:
        return f'{value / 1024**3:.1f} GiB'
    if value >= 1024**2:
        return f'{value / 1024**2:.1f} MiB'
    return f'{value / 1024:.1f} KiB'


def _bitrate(status: models.ShowStatus) -> str:
    if status.streamo.output_bitrate_kbps is None:
        return 'unknown'
    return f'{status.streamo.output_bitrate_kbps:.0f} kbps'


def _lyte_detail(status: models.LyteStatus) -> str:
    if status.service.state == 'disabled':
        return 'disabled'
    details = []
    if status.service.last_error:
        details.append(f'{status.service.state}: {status.service.last_error}')
    elif status.active_animation:
        details.append(f'animation {status.active_animation}')
    else:
        details.append(status.service.state)
    if status.queued_animation:
        details.append(f'queued {status.queued_animation}')
    details.extend(f'{name}={value}' for name, value in status.bindings.items())
    if status.active_test:
        details.append('test active')
    elif status.queued_test:
        details.append('test queued')
    if status.midi_error:
        details.append('MIDI error')
    elif status.midi_connected:
        midi = 'MIDI connected'
        if status.note is not None:
            midi += f', note {status.note}'
        if status.breath is not None:
            midi += f', breath {status.breath}'
        if status.pitch_bend is not None:
            midi += f', bend {status.pitch_bend}'
        details.append(midi)
    details.extend(
        f'{name}: {_lyte_string_detail(value)}'
        for name, value in status.strings.items()
    )
    return ', '.join(details)


def _lyte_string_detail(status: models.LyteStringStatus) -> str:
    details = [status.state]
    if status.host:
        details.append(status.host)
    if status.mac:
        details.append(status.mac)
    if status.led_count is not None:
        details.append(f'{status.led_count} LEDs')
    details.append(f'{status.frame_count} frames')
    if status.failure_count:
        details.append(f'{status.failure_count} failures')
    if status.last_error:
        details.append(status.last_error)
    return ' '.join(details)


def _mixer_detail(mixer: models.MixerStatus) -> str:
    if mixer.error:
        return f'{mixer.state}: {mixer.error}'
    missing = []
    if mixer.audio_ready is False:
        missing.append('USB audio')
    if mixer.midi_ready is False:
        missing.append('MIDI')
    detail = f'{mixer.state} for {" and ".join(missing)}' if missing else mixer.state
    if mixer.latency_ms is not None:
        return f'{detail}: {mixer.latency_ms:.1f} ms'
    return detail


def _osc_recorder_detail(status: models.RecorderStatus) -> str:
    if status.last_error:
        return f'{status.state}: {status.last_error}'
    if status.log_path and status.log_size is not None:
        return f'{status.state}: {status.log_path} ({status.log_size} bytes)'
    return status.state


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return 'unknown time'
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f'{hours}:{minutes:02}:{secs:02}'
    return f'{minutes}:{secs:02}'


SHOW_MARKERS = ['Show start', 'Song start', 'Interval', 'Show end']
