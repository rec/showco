from __future__ import annotations

import html
import json
from functools import cache
from pathlib import Path

from . import models

ERROR_PAGE_LIMIT = 25
SITE_DIRECTORY = Path(__file__).parent.parent.parent / 'site'


@cache
def site_file(name: str) -> str:
    return (SITE_DIRECTORY / name).read_text()


def channels_page(status: models.ShowStatus) -> str:
    channel_html = ''.join(
        level(channel, status.recs.channels) for channel in status.recs.channels
    )
    if not channel_html:
        channel_html = '<p>No channel data from recs.</p>'
    return page(
        'Channels',
        f"""
        <section>
          <h2>Recording channels</h2>
          <div class="levels" id="channels">
            {channel_html}
          </div>
          <div class="channel-actions">
            <button type="button" id="save-track-names">Save</button>
            <button type="button" id="revert-track-names">Revert</button>
          </div>
        </section>
        """,
        script=site_file('channel-controls.js')
        + site_file('status-script.js')
        + site_file('waveform-script.js'),
    )


def health_page(status: models.ShowStatus) -> str:
    recs = status.recs.service
    streamo = status.streamo.service
    progress_class = 'ok' if status.recording_progress.ok else 'failed'
    disk_critical = status.recs.disk is not None and (
        status.recs.disk.alert_active or status.recs.disk.paused_for_disk_space
    )
    performance = ''.join(
        [
            _performance_row('cpu', 'CPU', _cpu_percent(status), _cpu(status)),
            _performance_row(
                'memory', 'Memory', _memory_percent(status), _memory(status)
            ),
            _performance_row(
                'disk',
                'Recording disk',
                _disk_percent(status.recs),
                _disk(status.recs),
                force_critical=disk_critical,
            ),
        ]
    )
    return page(
        'Health',
        f"""
        {readiness_section(status.readiness)}
        <section class="cards">
          {service_card('recording', 'Recording', recs.state, _recording_text(status))}
          {
            service_card(
                'streaming', 'Streaming', streamo.state, _streaming_text(status)
            )
        }
        </section>
        <section>
          <h2>Performance</h2>
          <div class="performance">
            {performance}
          </div>
        </section>
        <section>
          <h2>Health</h2>
          <p id="recs-health">recs: {_service_detail(recs.state, recs.last_error)}</p>
          <p id="recs-snapshot">recs snapshot: {_snapshot_detail(status.recs)}</p>
          <p id="recording-progress" class="{progress_class}">
            recording progress: {html.escape(status.recording_progress.message)}
          </p>
          <p id="streamo-health">
            streamo: {_service_detail(streamo.state, streamo.last_error)}
          </p>
          <p id="lyte-health">lyte: {html.escape(_lyte_detail(status.lyte))}</p>
          <p>Pi temperature: <span id="temperature">{_temperature(status)}</span></p>
          <p>Stream bitrate: <span id="bitrate">{_bitrate(status)}</span></p>
          <div id="mixers">{_mixers(status)}</div>
          <div id="osc-recorders">{_osc_recorders(status.recs.osc)}</div>
        </section>
        <section>
          <h2>Recording inputs</h2>
          <p>Signal checks for channels currently recording, including clipping.</p>
          <div id="input-checks">{input_checks(status.input_checks)}</div>
        </section>
        <section>
          <h2>recs errors</h2>
          <div id="recs-errors" data-limit="{ERROR_PAGE_LIMIT}">
            {_recs_errors(status.recs.errors[-ERROR_PAGE_LIMIT:])}
          </div>
        </section>
        <section>
          <h2>Observed incidents</h2>
          <p>The target samples in the background, even without a browser.
          History retains the latest 100 events across restarts.
          Transitions between samples may be missed.</p>
          <div id="incidents">{incident_list(status.incidents)}</div>
        </section>
        """,
        script=site_file('channel-controls.js') + site_file('status-script.js'),
    )


def readiness_section(status: models.ReadinessStatus) -> str:
    state = 'ready' if status.ready else 'not ready'
    css_class = 'healthy' if status.ready else 'error'
    return f"""
        <section class="readiness {css_class}">
          <h2>Service readiness</h2>
          <p>Checks service connections and recording flags.
          Confirm recorded-audio progress below;
          silence filtering may legitimately pause file growth.</p>
          <p class="state" id="readiness-state">{state}</p>
          <ul id="readiness-checks">
            {''.join(readiness_check(check) for check in status.checks)}
          </ul>
        </section>
    """


def readiness_check(check: models.ReadinessCheck) -> str:
    state = 'ok' if check.ok else 'failed'
    return (
        f'<li class="{state}"><b>{html.escape(check.name)}</b>: '
        f'{html.escape(check.message)}</li>'
    )


def incident_list(incidents: list[models.Incident]) -> str:
    if not incidents:
        return '<p>No incidents.</p>'
    return '<ul>' + ''.join(incident(value) for value in incidents) + '</ul>'


def incident(value: models.Incident) -> str:
    return (
        f'<li><time>{value.timestamp.strftime("%H:%M:%S")}</time> '
        f'{html.escape(value.message)}</li>'
    )


def input_checks(checks: list[models.InputCheck]) -> str:
    if not checks:
        return '<p>No recording inputs.</p>'
    return '<ul>' + ''.join(input_check_item(check) for check in checks) + '</ul>'


def input_check_item(check: models.InputCheck) -> str:
    state = 'ok' if check.ok else 'failed'
    return f'<li class="{state}"><b>{html.escape(check.name)}</b>: {check.message}</li>'


def attributes_page(
    mutable_attributes: list[models.MutableAttribute] | models.ActionResult | None,
) -> str:
    return page(
        'Attributes',
        mutable_attributes_section(mutable_attributes),
        script=site_file('channel-controls.js') + site_file('status-script.js'),
    )


def errors_page(errors: list[models.ErrorRecord]) -> str:
    body = (
        f'<section id="recs-errors" data-limit="{ERROR_PAGE_LIMIT}">'
        f'{_recs_errors(errors[-ERROR_PAGE_LIMIT:])}'
        '</section>'
    )
    return page(
        'Errors',
        body,
        script=site_file('channel-controls.js') + site_file('status-script.js'),
    )


def actions_page(
    action_log: list[models.ActionLogEntry],
    *,
    streamo_enabled: bool = True,
    lyte_enabled: bool = True,
) -> str:
    title_fields = ['title', 'category', 'tags']
    noise_floor = field_action(
        'recs-set-noise-floor',
        'Set noise floor',
        ['source', 'channel', 'noise_floor'],
    )
    return page(
        'Actions',
        f"""
        <section class="actions">
          {button('recs-calibrate', 'Calibrate noise floor')}
          {noise_floor}
          {button('recs-reload-profiles', 'Reload recs profiles')}
          {''.join(marker_button(label) for label in SHOW_MARKERS)}
          {field_action('recs-marker', 'Create recs marker', ['label'])}
          {field_action('recs-key-label', 'Set recs key label', ['key', 'label'])}
          {button('recs-new-session', 'Start new recording session', confirm=True)}
          {button('recs-pause-recording', 'Pause recording')}
          {button('recs-resume-recording', 'Resume recording')}
          {button('recs-status-snapshot', 'recs status snapshot')}
          {button('recs-disk-status', 'recs disk status')}
          {button('recs-list-devices', 'List recs devices')}
          {button('recs-capabilities', 'recs capabilities')}
          {shutdown_action()}
          {button('lyte-test', 'Test lights') if lyte_enabled else ''}
          {cable_test_action()}
          {_streamo_actions(title_fields) if streamo_enabled else ''}
        </section>
        <section>
          <h2>Recent actions</h2>
          {''.join(action_result(r) for r in action_log) or '<p>No actions yet.</p>'}
        </section>
        """,
    )


def playback_page(playback: models.PlaybackStatus) -> str:
    session_disabled = ' disabled' if playback.state == 'waiting' else ''
    transport = ''.join(
        [
            transport_button(
                'recs-playback-jump-session',
                'Previous session',
                offset=-1,
                disabled=session_disabled,
            ),
            transport_button(
                'recs-playback-jump',
                '-10 seconds',
                seconds=-10,
                disabled=session_disabled,
            ),
            transport_button('recs-playback-play', 'Play'),
            transport_button('recs-playback-pause', 'Pause', disabled=session_disabled),
            transport_button('recs-playback-stop', 'Stop', disabled=session_disabled),
            transport_button(
                'recs-playback-jump',
                '+10 seconds',
                seconds=10,
                disabled=session_disabled,
            ),
            transport_button(
                'recs-playback-jump-session',
                'Next session',
                offset=1,
                disabled=session_disabled,
            ),
        ]
    )
    return page(
        'Playback',
        f"""
        <section>
          <h2>Playback</h2>
          <p id="playback-state" aria-live="polite">{html.escape(playback.state)}</p>
          <p id="playback-selection">{html.escape(playback_selection(playback))}</p>
          <p id="playback-position">{html.escape(playback_position(playback))}</p>
          <p id="playback-result" aria-live="polite"></p>
          <div class="transport" id="playback-transport">
            {transport}
          </div>
        </section>
        """,
        script=site_file('playback-script.js'),
    )


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


def _streamo_actions(title_fields: list[str]) -> str:
    return f"""
          {button('streamo-restart', 'Restart Stream')}
          {button('streamo-mute', 'Mute Stream')}
          {button('streamo-unmute', 'Unmute Stream')}
          {button('streamo-stop', 'Stop Stream', confirm=True)}
          {field_action('streamo-title', 'Update stream info', title_fields)}
          {field_action('streamo-chat', 'Send chat message', ['message'])}
          {field_action('streamo-announce', 'Send announcement', ['message'])}
          {button('streamo-clip', 'Create clip')}
          {field_action('streamo-marker', 'Create stream marker', ['description'])}
    """


def performance_page() -> str:
    return page(
        'Performance',
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
        name.title(), site_file(f'{name}.html'), script=site_file('workflow.js')
    )


def page(title: str, body: str, *, script: str = '') -> str:
    page_script = (
        f'<script>{site_file("status-connection.js")}</script>'
        f'<script>{site_file("show-controls.js")}</script>'
        f'<script>{script or "pollShowControls();"}</script>'
    )
    connection = '<p id="connection-status" role="status">Connecting...</p>'
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>showCo {title}</title>
  <style>{site_file('server.css')}</style>
</head>
<body>
  <header>
    <h1>showCo</h1>
    <nav>
      <a href="/performance">Performance</a>
      <a href="/setlist">Set list</a>
      <a href="/soundcheck">Soundcheck</a>
      <a href="/recovery">Recovery</a>
      <a href="/channels">Channels</a>
      <a href="/health">Health</a>
      <a href="/playback">Playback</a>
      <a href="/attributes">Attributes</a>
      <a href="/actions">Actions</a>
      <a href="/errors">Errors</a>
    </nav>
  </header>
  <main>{connection}
    <section class="show-controls" aria-label="Performance protection">
      <strong id="performance-lock-state">Performance lock: checking</strong>
      <form id="performance-lock-form" method="post" action="/actions">
        <button name="action" value="performance-lock">Enable performance lock</button>
        <label><input type="checkbox" name="confirmation" value="unlock">
          Confirm unlock</label>
        <button name="action" value="performance-unlock">
          Unlock protected actions</button>
      </form>
      <p>Protects web configuration, calibration, tests,
         session replacement, and shutdown.
         Separate CLI and deployment commands are not locked.</p>
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


def service_card(identifier: str, title: str, state: str, detail: str) -> str:
    return f"""
    <article class="card {html.escape(state)}" id="{html.escape(identifier)}-card">
      <h2>{html.escape(title)}</h2>
      <div class="state" id="{html.escape(identifier)}-state">{html.escape(state)}</div>
      <p id="{html.escape(identifier)}-detail">{html.escape(detail)}</p>
    </article>
    """


def level(channel: models.ChannelLevel, channels: list[models.ChannelLevel]) -> str:
    safe_device = html.escape(channel.device)
    safe_name = html.escape(channel.name)
    safe_state = html.escape(channel.state)
    recording_state = 'recording' if channel.on else 'not recording'
    stereo = len(channel.channels) == 2
    enabled = stereo or _stereo_enabled(channel, channels)
    checked = ' checked' if stereo else ''
    disabled = '' if enabled else ' disabled'
    numbers = ','.join(str(number) for number in channel.channels)
    return f"""
    <div class="level {safe_state}" data-device="{safe_device}"
         data-channel="{safe_name}" data-channels="{numbers}"
         data-saved-track-name="{safe_name}">
      <label>
        <span class="channel-caption">
          <span class="channel-state {channel_indicator(channel.on)}"
                aria-label="{recording_state}" title="{recording_state}">•</span>
          <b>{safe_name}</b>
        </span>
        <input name="track_name" value="{safe_name}">
      </label>
      <label class="stereo"><input type="checkbox"{checked}{disabled}>Stereo</label>
      <canvas class="waveform" aria-label="Live waveform"></canvas>
      <button class="calibrate-channel" type="button">Calibrate</button>
    </div>
    """


def _stereo_enabled(
    channel: models.ChannelLevel, channels: list[models.ChannelLevel]
) -> bool:
    if len(channel.channels) != 1:
        return False
    return any(
        other.device == channel.device and other.channels == [channel.channels[0] + 1]
        for other in channels
    )


def channel_indicator(on: bool) -> str:
    return 'indicator-red' if on else 'indicator-green'


def mutable_attributes_section(
    attributes: list[models.MutableAttribute] | models.ActionResult | None,
) -> str:
    if isinstance(attributes, models.ActionResult):
        body = f'<p>{html.escape(attributes.message)}</p>'
    elif attributes:
        body = (
            '<div class="attributes" id="mutable-attributes">'
            + ''.join(mutable_attribute(a) for a in attributes)
            + '</div>'
        )
    else:
        body = '<p>No mutable recs attributes.</p>'
    return f"""
        <section>
          <h2>recs attributes</h2>
          {body}
        </section>
    """


def mutable_attribute(attribute: models.MutableAttribute) -> str:
    value = attribute.value
    input_type = 'text'
    value_type = 'text'
    if isinstance(value, bool):
        input_type = 'checkbox'
        value_type = 'boolean'
        value_html = ' checked' if value else ''
    elif isinstance(value, int | float):
        input_type = 'number'
        value_type = 'number'
        value_html = f' value="{value}" step="any"'
    elif isinstance(value, str):
        value_html = f' value="{html.escape(value)}"'
    else:
        value_type = 'json'
        value_html = f' value="{html.escape(json.dumps(value, separators=(",", ":")))}"'
    saved_value = html.escape(json.dumps(value, separators=(',', ':')))
    address = html.escape(attribute.address)
    return f"""
      <label class="mutable-attribute" data-address="{address}"
             data-saved-value="{saved_value}">
        {address}
        <input type="{input_type}" data-value-type="{value_type}"{value_html}>
      </label>
    """


def button(action: str, label: str, *, confirm: bool = False) -> str:
    confirmation = ' data-confirm="true"' if confirm else ''
    return f"""
    <form method="post"{confirmation}>
      <input type="hidden" name="action" value="{html.escape(action)}">
      <button>{html.escape(label)}</button>
    </form>
    """


def transport_button(
    action: str,
    label: str,
    *,
    seconds: int | None = None,
    offset: int | None = None,
    disabled: str = '',
) -> str:
    fields = ''
    if seconds is not None:
        fields += f'<input type="hidden" name="seconds" value="{seconds}">'
    if offset is not None:
        fields += f'<input type="hidden" name="offset" value="{offset}">'
    return f"""
    <form method="post">
      <input type="hidden" name="action" value="{html.escape(action)}">
      {fields}
      <button{disabled}>{html.escape(label)}</button>
    </form>
    """


def marker_button(label: str) -> str:
    return f"""
    <form method="post">
      <input type="hidden" name="action" value="recs-marker">
      <input type="hidden" name="label" value="{html.escape(label)}">
      <button>{html.escape(label)}</button>
    </form>
    """


def field_action(action: str, label: str, fields: list[str]) -> str:
    inputs = ''.join(
        f'<label>{html.escape(f)}<input name="{html.escape(f)}"></label>'
        for f in fields
    )
    return f"""
    <form method="post">
      <input type="hidden" name="action" value="{html.escape(action)}">
      <h2>{html.escape(label)}</h2>
      {inputs}
      <button>{html.escape(label)}</button>
    </form>
    """


def shutdown_action() -> str:
    return """
    <form method="post">
      <input type="hidden" name="action" value="recs-shutdown">
      <h2>Shutdown recs daemon</h2>
      <label>confirmation
        <select name="confirmation">
          <option value="cancel" selected>Cancel</option>
          <option value="shutdown">Shutdown recs daemon</option>
        </select>
      </label>
      <button>Apply shutdown choice</button>
    </form>
    """


def cable_test_action() -> str:
    return """
    <form method="post">
      <input type="hidden" name="action" value="cable-test">
      <h2>Test X18 cables</h2>
      <label>channels<input name="channels" value="9-14"></label>
      <label>sends<input name="sends" value="1-6"></label>
      <button>Test cables</button>
    </form>
    """


def action_result(entry: models.ActionLogEntry) -> str:
    result = entry.result
    state = 'ok' if result.ok else 'failed'
    timestamp = entry.timestamp.strftime('%H:%M:%S')
    return (
        f'<p class="{state}"><time>{timestamp}</time> '
        f'{html.escape(entry.service)} {html.escape(entry.command)}: '
        f'{html.escape(result.message)}</p>'
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


def _snapshot_detail(status: models.RecsStatus) -> str:
    return status.snapshot_error or 'connected'


def _temperature(status: models.ShowStatus) -> str:
    if status.system.temperature_c is not None:
        return f'{status.system.temperature_c:.1f} °C'
    return status.system.temperature_error or 'unknown'


def _performance_row(
    identifier: str,
    label: str,
    percent: float | None,
    detail: str,
    *,
    force_critical: bool = False,
) -> str:
    state = _performance_state(percent, force_critical=force_critical)
    value = f' value="{percent:.2f}"' if percent is not None else ''
    return f"""
      <div class="performance-row {state}" id="{identifier}-performance">
        <b>{html.escape(label)}</b>
        <meter id="{identifier}-meter" min="0" max="100"{value}></meter>
        <span id="{identifier}-value">{html.escape(detail)}</span>
      </div>
    """


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


def _mixers(status: models.ShowStatus) -> str:
    if not status.mixers:
        return '<p>No mixers configured.</p>'
    return ''.join(
        f'<p>{html.escape(mixer.name)}: {html.escape(_mixer_detail(mixer))}</p>'
        for mixer in status.mixers
    )


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


def _osc_recorders(statuses: list[models.RecorderStatus]) -> str:
    if not statuses:
        return '<p>No OSC recorders.</p>'
    return ''.join(
        f'<p>{html.escape(status.name)} OSC recorder: '
        f'{html.escape(_osc_recorder_detail(status))}</p>'
        for status in statuses
    )


def _osc_recorder_detail(status: models.RecorderStatus) -> str:
    if status.last_error:
        return f'{status.state}: {status.last_error}'
    if status.log_path and status.log_size is not None:
        return f'{status.state}: {status.log_path} ({status.log_size} bytes)'
    return status.state


def _recs_errors(errors: list[models.ErrorRecord]) -> str:
    if not errors:
        return '<p>No errors</p>'
    items = ''.join(
        f'<li><time class="error-time">{html.escape(e.timestamp)}</time>'
        f'<span>{html.escape(e.message)}</span></li>'
        for e in errors
    )
    return f'<ul>{items}</ul>'


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return 'unknown time'
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f'{hours}:{minutes:02}:{secs:02}'
    return f'{minutes}:{secs:02}'


SHOW_MARKERS = ['Show start', 'Song start', 'Interval', 'Show end']
