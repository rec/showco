from __future__ import annotations

import html
import json
import subprocess
import threading
import time
from collections.abc import Callable
from functools import cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar, cast
from urllib import parse

from reccy import logging

from . import models, services
from .mixer import MixersMonitor
from .recs import RecsClient
from .system import SystemMonitor
from .twitcho.client import TwitchoClient

MAX_ACTION_BYTES = 65_536
MAX_CONCURRENT_REQUESTS = 8
ERROR_PAGE_LIMIT = 25
LOGGER = logging.get_logger(__name__)
SITE_DIRECTORY = Path(__file__).parent.parent / "site"


@cache
def site_file(name: str) -> str:
    return (SITE_DIRECTORY / name).read_text()


class ShowcoApp:
    def __init__(
        self,
        recs: RecsClient,
        twitcho: TwitchoClient | None,
        system: SystemMonitor,
        mixers: MixersMonitor,
        twitcho_restart: Callable[[], models.ActionResult] | None = None,
        x18_status: Callable[[], models.RecorderStatus] | None = None,
    ) -> None:
        self.recs = recs
        self.twitcho = twitcho
        self.system = system
        self.mixers = mixers
        self.twitcho_restart = twitcho_restart or services.restart_twitcho_service
        self.x18_status = x18_status
        self.revision = source_revision()
        self.run_started_at = time.time()
        self.action_log: list[models.ActionResult] = []
        self.action_lock = threading.Lock()
        self.action_log_lock = threading.Lock()

    def status(self) -> models.ShowStatus:
        if self.twitcho is None:
            twitcho = models.TwitchoStatus(
                service=models.ServiceStatus(name="twitcho", state="disabled")
            )
        else:
            twitcho = self.twitcho.status()
        recs = self.recs.status()
        return models.ShowStatus(
            recs=recs,
            twitcho=twitcho,
            system=self.system.status(),
            mixers=self.mixers.status(
                {channel.device for channel in recs.channels},
                {midi.name: midi.state for midi in recs.midi},
            ),
            x18=recs.x18,
            revision=self.revision,
            run_started_at=self.run_started_at,
        )

    def run_action(self, form: dict[str, str]) -> models.ActionResult:
        with self.action_lock:
            action = form.get("action", "")
            if action == "recs-calibrate":
                result = self.recs.calibrate()
            elif action == "recs-track-name":
                result = self.recs.set_track_name(
                    form.get("device", ""),
                    form.get("channel", ""),
                    form.get("track_name", ""),
                )
            elif action == "recs-set-stereo":
                result = self.recs.set_stereo(
                    form.get("device", ""), _channel_numbers(form.get("channels", ""))
                )
            elif action == "recs-set-attr":
                try:
                    value = json.loads(form.get("value", ""))
                except json.JSONDecodeError:
                    result = models.ActionResult(
                        ok=False,
                        message="recs attribute value must be valid JSON",
                    )
                else:
                    result = self.recs.set_attr(form.get("address", ""), value)
            elif action == "recs-shutdown":
                if form.get("confirmation") == "shutdown":
                    result = self.recs.shutdown()
                else:
                    result = models.ActionResult(
                        ok=True, message="recs shutdown canceled"
                    )
            elif action in RECS_ACTIONS:
                try:
                    fields = _recs_fields(form)
                except ValueError as e:
                    result = models.ActionResult(ok=False, message=str(e))
                else:
                    result = self.recs.action(RECS_ACTIONS[action], **fields)
            elif action == "twitcho-restart" and self.twitcho is None:
                result = models.ActionResult(ok=False, message="twitcho is disabled")
            elif action == "twitcho-restart":
                result = self.twitcho_restart()
            elif action in TWITCHO_ACTIONS:
                if self.twitcho is None:
                    result = models.ActionResult(
                        ok=False, message="twitcho is disabled"
                    )
                else:
                    result = self.twitcho.action(
                        TWITCHO_ACTIONS[action], **_twitcho_fields(form)
                    )
            else:
                result = models.ActionResult(
                    ok=False, message=f"unknown action {action}"
                )
            with self.action_log_lock:
                self.action_log = [result, *self.action_log[:9]]
            return result

    def recent_actions(self) -> list[models.ActionResult]:
        with self.action_log_lock:
            return list(self.action_log)


class ShowcoHandler(BaseHTTPRequestHandler):
    app: ClassVar[ShowcoApp]

    def do_GET(self) -> None:
        if not self._acquire_request():
            return
        try:
            self._do_get()
        finally:
            cast(ShowcoServer, self.server).request_slots.release()

    def _do_get(self) -> None:
        if self.path == "/status":
            self._json(self.app.status())
            return
        if self.path in {"/", "/channels"}:
            self._html(channels_page(self.app.status()))
            return
        if self.path == "/health":
            self._html(health_page(self.app.status()))
            return
        if self.path == "/attributes":
            self._html(attributes_page(self.app.recs.mutable_attributes()))
            return
        if self.path == "/errors":
            self._html(errors_page(self.app.status().recs.errors))
            return
        if self.path == "/actions":
            self._html(
                actions_page(
                    self.app.recent_actions(),
                    twitcho_enabled=self.app.twitcho is not None,
                )
            )
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if not self._acquire_request():
            return
        try:
            self._do_post()
        finally:
            cast(ShowcoServer, self.server).request_slots.release()

    def _do_post(self) -> None:
        if self.path != "/actions":
            self.send_error(404)
            return
        try:
            form = self._form()
        except ValueError as error:
            self.send_error(413, str(error))
            return
        result = self.app.run_action(form)
        self._log_action(form.get("action", ""), result)
        if self.headers.get("Accept") == "application/json":
            self._json_action(result)
            return
        self.send_response(303)
        self.send_header("Location", "/actions")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return

    def _form(self) -> dict[str, str]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("invalid Content-Length") from None
        if length < 0 or length > MAX_ACTION_BYTES:
            raise ValueError(f"action body exceeds {MAX_ACTION_BYTES} bytes")
        body = self.rfile.read(length).decode()
        parsed = parse.parse_qs(body)
        return {k: v[-1] for k, v in parsed.items() if v}

    def _acquire_request(self) -> bool:
        if cast(ShowcoServer, self.server).request_slots.acquire(blocking=False):
            return True
        self.send_error(503, "Showco is busy")
        return False

    def _log_action(self, action: str, result: models.ActionResult) -> None:
        detail = result.message.replace("\n", " ")[:240]
        log = LOGGER.info if result.ok else LOGGER.error
        log(
            "showco action source=%s action=%r ok=%s detail=%r",
            self.client_address[0],
            action,
            result.ok,
            detail,
        )

    def _html(self, body: str) -> None:
        data = body.encode()
        self.send_response(200)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, value: models.ShowStatus) -> None:
        data = value.model_dump_json().encode()
        self.send_response(200)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json_action(self, value: models.ActionResult) -> None:
        data = value.model_dump_json().encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class ShowcoServer(ThreadingHTTPServer):
    app: ShowcoApp
    request_slots: threading.BoundedSemaphore

    def __init__(self, address: tuple[str, int], handler: type[ShowcoHandler]) -> None:
        super().__init__(address, handler)
        self.request_slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
        self.daemon_threads = True


def source_revision() -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(Path(__file__).parent.parent), "rev-parse", "HEAD"],
            capture_output=True,
            check=False,
            text=True,
        )
    except FileNotFoundError:
        return None
    if result.returncode:
        return None
    return result.stdout.strip() or None


def make_server(
    host: str,
    port: int,
    *,
    recs: RecsClient | None = None,
    twitcho: TwitchoClient | None = None,
    system: SystemMonitor | None = None,
    mixers: MixersMonitor | None = None,
    twitcho_restart: Callable[[], models.ActionResult] | None = None,
    twitcho_enabled: bool = False,
    x18_status: Callable[[], models.RecorderStatus] | None = None,
) -> ThreadingHTTPServer:
    handler = type("ConfiguredShowcoHandler", (ShowcoHandler,), {})
    app = ShowcoApp(
        recs or RecsClient(),
        (twitcho or TwitchoClient()) if twitcho_enabled else None,
        system or SystemMonitor(),
        mixers or MixersMonitor([]),
        twitcho_restart if twitcho_enabled else None,
        x18_status,
    )
    handler.app = app
    server = ShowcoServer((host, port), handler)
    server.app = app
    return server


def channels_page(status: models.ShowStatus) -> str:
    channel_html = "".join(
        level(channel, status.recs.channels) for channel in status.recs.channels
    )
    if not channel_html:
        channel_html = "<p>No channel data from recs.</p>"
    return page(
        "Channels",
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
        script=site_file("status-script.js"),
    )


def health_page(status: models.ShowStatus) -> str:
    recs = status.recs.service
    twitcho = status.twitcho.service
    return page(
        "Health",
        f"""
        <section class="cards">
          {service_card("recording", "Recording", recs.state, _recording_text(status))}
          {
            service_card(
                "streaming", "Streaming", twitcho.state, _streaming_text(status)
            )
        }
        </section>
        <section>
          <h2>Health</h2>
          <p id="recs-health">recs: {_service_detail(recs.state, recs.last_error)}</p>
          <p id="twitcho-health">
            twitcho: {_service_detail(twitcho.state, twitcho.last_error)}
          </p>
          <p>Pi temperature: <span id="temperature">{_temperature(status)}</span></p>
          <p>Twitch bitrate: <span id="bitrate">{_bitrate(status)}</span></p>
          <div id="mixers">{_mixers(status)}</div>
          <p>X18 OSC recorder:
            <span id="x18-recorder">{_x18_recorder(status)}</span></p>
        </section>
        """,
        script=site_file("status-script.js"),
    )


def attributes_page(
    mutable_attributes: list[models.MutableAttribute] | models.ActionResult | None,
) -> str:
    return page(
        "Attributes",
        mutable_attributes_section(mutable_attributes),
        script=site_file("status-script.js"),
    )


def errors_page(errors: list[models.ErrorRecord]) -> str:
    body = (
        f'<section id="recs-errors" data-limit="{ERROR_PAGE_LIMIT}">'
        f"{_recs_errors(errors[-ERROR_PAGE_LIMIT:])}"
        "</section>"
    )
    return page(
        "Errors",
        body,
        script=site_file("status-script.js"),
    )


def actions_page(
    action_log: list[models.ActionResult], *, twitcho_enabled: bool = True
) -> str:
    title_fields = ["title", "category", "tags"]
    noise_floor = field_action(
        "recs-set-noise-floor",
        "Set noise floor",
        ["source", "channel", "noise_floor"],
    )
    return page(
        "Actions",
        f"""
        <section class="actions">
          {button("recs-calibrate", "Calibrate noise floor")}
          {noise_floor}
          {button("recs-reload-profiles", "Reload Recs profiles")}
          {field_action("recs-marker", "Create Recs marker", ["label"])}
          {field_action("recs-key-label", "Set Recs key label", ["key", "label"])}
          {button("recs-pause-recording", "Pause recording")}
          {button("recs-resume-recording", "Resume recording")}
          {button("recs-stop-recording", "Stop recording", confirm=True)}
          {button("recs-start-recording", "Start recording")}
          {button("recs-status-snapshot", "Recs status snapshot")}
          {button("recs-disk-status", "Recs disk status")}
          {button("recs-list-devices", "List Recs devices")}
          {button("recs-capabilities", "Recs capabilities")}
          {shutdown_action()}
          {_twitcho_actions(title_fields) if twitcho_enabled else ""}
        </section>
        <section>
          <h2>Recent actions</h2>
          {"".join(action_result(r) for r in action_log) or "<p>No actions yet.</p>"}
        </section>
        """,
    )


def _twitcho_actions(title_fields: list[str]) -> str:
    return f"""
          {button("twitcho-restart", "Restart Twitch")}
          {button("twitcho-mute", "Mute Twitch")}
          {button("twitcho-unmute", "Unmute Twitch")}
          {button("twitcho-stop", "Stop Twitch", confirm=True)}
          {field_action("twitcho-title", "Update stream info", title_fields)}
          {field_action("twitcho-chat", "Send chat message", ["message"])}
          {field_action("twitcho-announce", "Send announcement", ["message"])}
          {button("twitcho-clip", "Create clip")}
          {field_action("twitcho-marker", "Create stream marker", ["description"])}
    """


def page(title: str, body: str, *, script: str = "") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Showco {title}</title>
  <style>{site_file("server.css")}</style>
</head>
<body>
  <header>
    <h1>Showco</h1>
    <nav>
      <a href="/channels">Channels</a>
      <a href="/health">Health</a>
      <a href="/attributes">Attributes</a>
      <a href="/actions">Actions</a>
      <a href="/errors">Errors</a>
    </nav>
  </header>
  <main>{body}</main>
  <script>{site_file("shutdown-action.js")}</script>
  {script}
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
    recording_state = "recording" if channel.on else "not recording"
    stereo = len(channel.channels) == 2
    enabled = stereo or _stereo_enabled(channel, channels)
    checked = " checked" if stereo else ""
    disabled = "" if enabled else " disabled"
    numbers = ",".join(str(number) for number in channel.channels)
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


def _channel_numbers(value: str) -> list[int]:
    try:
        return [int(number) for number in value.split(",") if number]
    except ValueError:
        return []


def channel_indicator(on: bool) -> str:
    return "indicator-red" if on else "indicator-green"


def mutable_attributes_section(
    attributes: list[models.MutableAttribute] | models.ActionResult | None,
) -> str:
    if isinstance(attributes, models.ActionResult):
        body = f"<p>{html.escape(attributes.message)}</p>"
    elif attributes:
        body = (
            '<div class="attributes" id="mutable-attributes">'
            + "".join(mutable_attribute(a) for a in attributes)
            + "</div>"
        )
    else:
        body = "<p>No mutable Recs attributes.</p>"
    return f"""
        <section>
          <h2>Recs attributes</h2>
          {body}
        </section>
    """


def mutable_attribute(attribute: models.MutableAttribute) -> str:
    value = attribute.value
    input_type = "text"
    value_type = "text"
    if isinstance(value, bool):
        input_type = "checkbox"
        value_type = "boolean"
        value_html = " checked" if value else ""
    elif isinstance(value, int | float):
        input_type = "number"
        value_type = "number"
        value_html = f' value="{value}" step="any"'
    elif isinstance(value, str):
        value_html = f' value="{html.escape(value)}"'
    else:
        value_type = "json"
        value_html = f' value="{html.escape(json.dumps(value, separators=(",", ":")))}"'
    saved_value = html.escape(json.dumps(value, separators=(",", ":")))
    address = html.escape(attribute.address)
    return f"""
      <label class="mutable-attribute" data-address="{address}"
             data-saved-value="{saved_value}">
        {address}
        <input type="{input_type}" data-value-type="{value_type}"{value_html}>
      </label>
    """


def button(action: str, label: str, *, confirm: bool = False) -> str:
    confirmation = ' data-confirm="true"' if confirm else ""
    return f"""
    <form method="post"{confirmation}>
      <input type="hidden" name="action" value="{html.escape(action)}">
      <button>{html.escape(label)}</button>
    </form>
    """


def field_action(action: str, label: str, fields: list[str]) -> str:
    inputs = "".join(
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
      <h2>Shutdown Recs daemon</h2>
      <label>confirmation
        <select name="confirmation">
          <option value="cancel" selected>Cancel</option>
          <option value="shutdown">Shutdown Recs daemon</option>
        </select>
      </label>
      <button>Apply shutdown choice</button>
    </form>
    """


def action_result(result: models.ActionResult) -> str:
    state = "ok" if result.ok else "failed"
    return f'<p class="{state}">{html.escape(result.message)}</p>'


def _recording_text(status: models.ShowStatus) -> str:
    if not status.recs.recording:
        return "stopped"
    elapsed = _duration(status.recs.elapsed_seconds)
    files = status.recs.file_count if status.recs.file_count is not None else "?"
    return f"recording for {elapsed}, {files} files"


def _streaming_text(status: models.ShowStatus) -> str:
    state = status.twitcho.stream_state
    muted = ", muted" if status.twitcho.muted else ""
    return f"{state}{muted}"


def _service_detail(state: str, error: str | None) -> str:
    return f"{state}: {error}" if error else state


def _temperature(status: models.ShowStatus) -> str:
    if status.system.temperature_c is not None:
        return f"{status.system.temperature_c:.1f} °C"
    return status.system.temperature_error or "unknown"


def _bitrate(status: models.ShowStatus) -> str:
    if status.twitcho.output_bitrate_kbps is None:
        return "unknown"
    return f"{status.twitcho.output_bitrate_kbps:.0f} kbps"


def _mixers(status: models.ShowStatus) -> str:
    if not status.mixers:
        return "<p>No mixers configured.</p>"
    return "".join(
        f"<p>{html.escape(mixer.name)}: {html.escape(_mixer_detail(mixer))}</p>"
        for mixer in status.mixers
    )


def _mixer_detail(mixer: models.MixerStatus) -> str:
    if mixer.error:
        return f"{mixer.state}: {mixer.error}"
    missing = []
    if mixer.audio_ready is False:
        missing.append("USB audio")
    if mixer.midi_ready is False:
        missing.append("MIDI")
    detail = f"{mixer.state} for {' and '.join(missing)}" if missing else mixer.state
    if mixer.latency_ms is not None:
        return f"{detail}: {mixer.latency_ms:.1f} ms"
    return detail


def _x18_recorder(status: models.ShowStatus) -> str:
    if status.x18.state == "disabled":
        return "disabled"
    if status.x18.last_error:
        return status.x18.last_error
    if status.x18.log_path and status.x18.log_size is not None:
        return (
            f"{status.x18.state}: {status.x18.log_path} ({status.x18.log_size} bytes)"
        )
    return status.x18.state


def _recs_errors(errors: list[models.ErrorRecord]) -> str:
    if not errors:
        return "<p>No errors</p>"
    items = "".join(
        f'<li><time class="error-time">{html.escape(e.timestamp)}</time>'
        f"<span>{html.escape(e.message)}</span></li>"
        for e in errors
    )
    return f"<ul>{items}</ul>"


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown time"
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02}:{secs:02}"
    return f"{minutes}:{secs:02}"


def _twitcho_fields(form: dict[str, str]) -> dict[str, object]:
    return {k: v for k, v in form.items() if k != "action" and v}


def _recs_fields(form: dict[str, str]) -> dict[str, object]:
    fields: dict[str, object] = {}
    for k, v in form.items():
        if k == "action" or not v:
            continue
        if k == "noise_floor":
            try:
                fields[k] = float(v)
            except ValueError:
                raise ValueError("noise_floor must be a number") from None
        else:
            fields[k] = v
    return fields


RECS_ACTIONS = {
    "recs-capabilities": "capabilities",
    "recs-disk-status": "disk_status",
    "recs-key-label": "set_key_label",
    "recs-list-devices": "list_devices",
    "recs-marker": "mark",
    "recs-pause-recording": "pause_recording",
    "recs-reload-profiles": "reload_profiles",
    "recs-resume-recording": "resume_recording",
    "recs-set-noise-floor": "set_noise_floor",
    "recs-start-recording": "start_recording",
    "recs-status-snapshot": "status_snapshot",
    "recs-stop-recording": "stop_recording",
}

TWITCHO_ACTIONS = {
    "twitcho-mute": "mute",
    "twitcho-unmute": "unmute",
    "twitcho-stop": "stop",
    "twitcho-title": "update_stream_info",
    "twitcho-chat": "chat",
    "twitcho-announce": "announce",
    "twitcho-clip": "clip",
    "twitcho-marker": "marker",
}
