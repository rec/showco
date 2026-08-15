from __future__ import annotations

import base64
import html
import json
import secrets
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar, cast
from urllib import parse

from . import models, services
from .mixer import MixerMonitor
from .recs import RecsClient
from .system import SystemMonitor
from .twitcho.client import TwitchoClient

MAX_ACTION_BYTES = 65_536
MAX_CONCURRENT_REQUESTS = 8


class ShowcoApp:
    def __init__(
        self,
        recs: RecsClient,
        twitcho: TwitchoClient | None,
        system: SystemMonitor,
        mixer: MixerMonitor,
        twitcho_restart: Callable[[], models.ActionResult] | None = None,
        x18_status: Callable[[], models.RecorderStatus] | None = None,
    ) -> None:
        self.recs = recs
        self.twitcho = twitcho
        self.system = system
        self.mixer = mixer
        self.twitcho_restart = twitcho_restart or services.restart_twitcho_service
        self.x18_status = x18_status
        self.revision = source_revision()
        self.run_started_at = time.time()
        self.action_log: list[models.ActionResult] = []
        self.action_log_lock = threading.Lock()

    def status(self) -> models.ShowStatus:
        if self.twitcho is None:
            twitcho = models.TwitchoStatus(
                service=models.ServiceStatus(name="twitcho", state="disabled")
            )
        else:
            twitcho = self.twitcho.status()
        return models.ShowStatus(
            recs=self.recs.status(),
            twitcho=twitcho,
            system=self.system.status(),
            mixer=self.mixer.status(),
            x18=self.x18_status() if self.x18_status else models.RecorderStatus(),
            revision=self.revision,
            run_started_at=self.run_started_at,
        )

    def run_action(self, form: dict[str, str]) -> models.ActionResult:
        action = form.get("action", "")
        if action == "recs-calibrate":
            result = self.recs.calibrate()
        elif action == "recs-track-name":
            result = self.recs.set_track_name(
                form.get("device", ""),
                form.get("channel", ""),
                form.get("track_name", ""),
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
                result = models.ActionResult(ok=True, message="recs shutdown canceled")
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
                result = models.ActionResult(ok=False, message="twitcho is disabled")
            else:
                result = self.twitcho.action(
                    TWITCHO_ACTIONS[action], **_twitcho_fields(form)
                )
        else:
            result = models.ActionResult(ok=False, message=f"unknown action {action}")
        with self.action_log_lock:
            self.action_log = [result, *self.action_log[:9]]
        return result

    def recent_actions(self) -> list[models.ActionResult]:
        with self.action_log_lock:
            return list(self.action_log)


class ShowcoHandler(BaseHTTPRequestHandler):
    app: ClassVar[ShowcoApp]
    control_password: ClassVar[str | None] = None

    def do_GET(self) -> None:
        if not self._authorized() or not self._acquire_request():
            return
        try:
            self._do_get()
        finally:
            cast(ShowcoServer, self.server).request_slots.release()

    def _do_get(self) -> None:
        if self.path == "/status":
            self._json(self.app.status())
            return
        if self.path in {"/", "/home"}:
            self._html(home_page(self.app.status(), self.app.recs.mutable_attributes()))
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
        if not self._authorized() or not self._acquire_request():
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

    def _authorized(self) -> bool:
        if self.control_password is None:
            return True
        header = self.headers.get("Authorization", "")
        prefix = "Basic "
        if not header.startswith(prefix):
            self._request_authentication()
            return False
        try:
            token = base64.b64decode(header[len(prefix) :], validate=True).decode()
        except (UnicodeDecodeError, ValueError):
            self._request_authentication()
            return False
        _, separator, password = token.partition(":")
        if not separator or not secrets.compare_digest(password, self.control_password):
            self._request_authentication()
            return False
        return True

    def _request_authentication(self) -> None:
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Showco"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _acquire_request(self) -> bool:
        if cast(ShowcoServer, self.server).request_slots.acquire(blocking=False):
            return True
        self.send_error(503, "Showco is busy")
        return False

    def _log_action(self, action: str, result: models.ActionResult) -> None:
        detail = result.message.replace("\n", " ")[:240]
        print(
            f"showco action source={self.client_address[0]} action={action!r} "
            f"ok={result.ok} detail={detail!r}",
            flush=True,
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
    mixer: MixerMonitor | None = None,
    twitcho_restart: Callable[[], models.ActionResult] | None = None,
    twitcho_enabled: bool = False,
    control_password: str | None = None,
    x18_status: Callable[[], models.RecorderStatus] | None = None,
) -> ThreadingHTTPServer:
    handler = type("ConfiguredShowcoHandler", (ShowcoHandler,), {})
    app = ShowcoApp(
        recs or RecsClient(),
        (twitcho or TwitchoClient()) if twitcho_enabled else None,
        system or SystemMonitor(),
        mixer or MixerMonitor(),
        twitcho_restart if twitcho_enabled else None,
        x18_status,
    )
    handler.app = app
    handler.control_password = control_password
    server = ShowcoServer((host, port), handler)
    server.app = app
    return server


def home_page(
    status: models.ShowStatus,
    mutable_attributes: list[models.MutableAttribute]
    | models.ActionResult
    | None = None,
) -> str:
    recs = status.recs.service
    twitcho = status.twitcho.service
    channel_html = "".join(
        level(c.device, c.name, c.state) for c in status.recs.channels
    )
    if not channel_html:
        channel_html = "<p>No channel data from recs.</p>"
    return page(
        "Home",
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
          <h2>Recording channels</h2>
          <div class="levels" id="channels">
            {channel_html}
          </div>
          <div class="channel-actions">
            <button type="button" id="save-track-names">Save</button>
            <button type="button" id="revert-track-names">Revert</button>
          </div>
        </section>
        <section>
          <h2>Health</h2>
          <p id="recs-health">recs: {_service_detail(recs.state, recs.last_error)}</p>
          <label class="toggle">
            <input id="show-all-errors" type="checkbox" role="switch">
            Show all errors
          </label>
          <div id="recs-errors">{
            _recs_errors(status.recs.errors, status.run_started_at)
        }</div>
          <p id="twitcho-health">
            twitcho: {_service_detail(twitcho.state, twitcho.last_error)}
          </p>
          <p>Pi temperature: <span id="temperature">{_temperature(status)}</span></p>
          <p>Twitch bitrate: <span id="bitrate">{_bitrate(status)}</span></p>
          <p>Mixer latency: <span id="mixer-latency">{_mixer_latency(status)}</span></p>
          <p>X18 OSC recorder:
            <span id="x18-recorder">{_x18_recorder(status)}</span></p>
          <p>Generated: <span id="generated-at">{_time(status.generated_at)}</span></p>
        </section>
        {mutable_attributes_section(mutable_attributes)}
        """,
        script=HOME_STATUS_SCRIPT,
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
  <style>{CSS}</style>
</head>
<body>
  <header>
    <h1>Showco</h1>
    <nav><a href="/home">Home</a><a href="/actions">Actions</a></nav>
  </header>
  <main>{body}</main>
  <script>
    for (const form of document.querySelectorAll("form[data-confirm=true]")) {{
      form.addEventListener("submit", event => {{
        if (!confirm("Are you sure?")) {{
          event.preventDefault();
        }}
      }});
    }}
  </script>
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


def level(device: str, name: str, state: str) -> str:
    safe_device = html.escape(device)
    safe_name = html.escape(name)
    safe_state = html.escape(state)
    return f"""
    <div class="level {safe_state}" data-device="{safe_device}"
         data-channel="{safe_name}" data-saved-track-name="{safe_name}">
      <label>
        <span class="channel-caption">
          <span class="channel-state {channel_indicator(state)}"
                aria-label="{safe_state}" title="{safe_state}">•</span>
          <b>Channel {safe_name}</b>
        </span>
        <input name="track_name" value="{safe_name}">
      </label>
    </div>
    """


def channel_indicator(state: str) -> str:
    if state in {"present", "healthy"}:
        return "indicator-green"
    return "indicator-red"


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


def _mixer_latency(status: models.ShowStatus) -> str:
    if status.mixer.latency_ms is not None:
        return f"{status.mixer.latency_ms:.1f} ms"
    return status.mixer.error or "unknown"


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


def _recs_errors(errors: list[models.ErrorRecord], run_started_at: float) -> str:
    current_errors = [e for e in errors if _error_timestamp(e) >= run_started_at]
    if not current_errors:
        return ""
    items = "".join(
        f'<li><time class="error-time">{html.escape(e.timestamp)}</time>'
        f"<span>{html.escape(e.message)}</span></li>"
        for e in current_errors
    )
    return f"<p>Recs errors:</p><ul>{items}</ul>"


def _error_timestamp(error: models.ErrorRecord) -> float:
    try:
        return datetime.fromisoformat(error.timestamp).timestamp()
    except ValueError:
        return 0.0


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown time"
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02}:{secs:02}"
    return f"{minutes}:{secs:02}"


def _time(timestamp: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(timestamp))


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

HOME_STATUS_SCRIPT = """
<script>
  function serviceDetail(service) {
    return service.last_error
      ? `${service.state}: ${service.last_error}`
      : service.state;
  }

  function recordingText(recs) {
    if (!recs.recording) return "stopped";
    const seconds = recs.elapsed_seconds;
    if (seconds === null) return "recording for unknown time, ? files";
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    const duration = hours
      ? `${hours}:${String(minutes % 60).padStart(2, "0")}:${String(
          Math.floor(seconds % 60),
        ).padStart(2, "0")}`
      : `${minutes}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
    return `recording for ${duration}, ${recs.file_count ?? "?"} files`;
  }

  function streamingText(twitcho) {
    return `${twitcho.stream_state}${twitcho.muted ? ", muted" : ""}`;
  }

  function updateService(identifier, service, detail, healthIdentifier) {
    document.getElementById(`${identifier}-card`).className = `card ${service.state}`;
    document.getElementById(`${identifier}-state`).textContent = service.state;
    document.getElementById(`${identifier}-detail`).textContent = detail;
    document.getElementById(healthIdentifier).textContent = `${
      healthIdentifier.replace("-health", "")
    }: ${serviceDetail(service)}`;
  }

  function trackKey(channel) {
    return `${channel.device}\\u0000${channel.name}`;
  }

  function revertTrackName(form) {
    const input = form.querySelector("[name=track_name]");
    input.value = form.dataset.savedTrackName;
    input.setCustomValidity("");
  }

  function saveTrackName(form) {
    const input = form.querySelector("[name=track_name]");
    if (input.value === form.dataset.savedTrackName) return Promise.resolve();
    input.setCustomValidity("");
    return fetch("/actions", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: new URLSearchParams({
        action: "recs-track-name",
        device: form.dataset.device,
        channel: form.dataset.channel,
        track_name: input.value,
      }),
    })
      .then(response => {
        if (!response.ok) {
          throw new Error(`track name request failed: ${response.status}`);
        }
        return response.json();
      })
      .then(result => {
        if (!result.ok) throw new Error(result.message);
        form.dataset.savedTrackName = input.value;
      })
      .catch(error => {
        input.setCustomValidity(error.message);
        input.reportValidity();
      });
  }

  function channelForms() {
    return [...document.querySelectorAll("#channels .level")];
  }

  function saveTrackNames() {
    let saved = Promise.resolve();
    for (const form of channelForms()) {
      saved = saved.then(() => saveTrackName(form));
    }
    return saved;
  }

  function revertTrackNames() {
    for (const form of channelForms()) revertTrackName(form);
  }

  function mutableAttributeValue(input) {
    if (input.dataset.valueType === "boolean") return input.checked;
    if (input.dataset.valueType === "number") return Number(input.value);
    if (input.dataset.valueType === "json") return JSON.parse(input.value);
    return input.value;
  }

  function saveMutableAttribute(event) {
    const input = event.currentTarget;
    const attribute = input.closest(".mutable-attribute");
    input.setCustomValidity("");
    let value;
    try {
      value = mutableAttributeValue(input);
    } catch (error) {
      input.setCustomValidity(error.message);
      input.reportValidity();
      return;
    }
    const savedValue = JSON.stringify(value);
    if (savedValue === attribute.dataset.savedValue) return;
    fetch("/actions", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: new URLSearchParams({
        action: "recs-set-attr",
        address: attribute.dataset.address,
        value: savedValue,
      }),
    })
      .then(response => {
        if (!response.ok) {
          throw new Error(`attribute request failed: ${response.status}`);
        }
        return response.json();
      })
      .then(result => {
        if (!result.ok) throw new Error(result.message);
        attribute.dataset.savedValue = savedValue;
      })
      .catch(error => {
        input.setCustomValidity(error.message);
        input.reportValidity();
      });
  }

  function channelForm(channel, trackName, savedTrackName) {
    const form = document.createElement("div");
    form.className = `level ${channel.state}`;
    form.dataset.device = channel.device;
    form.dataset.channel = channel.name;
    form.dataset.savedTrackName = savedTrackName;
    const label = document.createElement("label");
    const caption = document.createElement("span");
    caption.className = "channel-caption";
    const title = document.createElement("b");
    title.textContent = `Channel ${channel.name}`;
    const input = document.createElement("input");
    input.name = "track_name";
    input.value = trackName;
    label.append(title, input);
    const state = document.createElement("span");
    state.className = `channel-state ${
      channel.state === "present" || channel.state === "healthy"
        ? "indicator-green"
        : "indicator-red"
    }`;
    state.setAttribute("aria-label", channel.state);
    state.title = channel.state;
    state.textContent = "•";
    caption.append(state, title);
    label.append(caption, input);
    form.append(label);
    return form;
  }

  function updateChannels(channels) {
    const container = document.getElementById("channels");
    if (document.activeElement.closest("#channels .level")) return;
    const names = new Map(
      [...container.querySelectorAll(".level")].map(form => [
        `${form.dataset.device}\\u0000${form.dataset.channel}`,
        {
          trackName: form.querySelector("[name=track_name]").value,
          savedTrackName: form.dataset.savedTrackName,
        },
      ]),
    );
    container.replaceChildren(...channels.map(channel => {
      const name = names.get(trackKey(channel));
      return channelForm(
        channel,
        name?.trackName ?? channel.name,
        name?.savedTrackName ?? channel.name,
      );
    }));
  }

  let latestErrors = [];
  let showcoStartedAt = 0;

  function updateRecsErrors(errors, runStartedAt) {
    latestErrors = errors;
    showcoStartedAt = runStartedAt;
    const container = document.getElementById("recs-errors");
    container.replaceChildren();
    const errorsToShow = document.getElementById("show-all-errors").checked
      ? errors
      : errors.filter(error => Date.parse(error.timestamp) / 1000 >= runStartedAt);
    if (!errorsToShow.length) return;
    const heading = document.createElement("p");
    heading.textContent = "Recs errors:";
    const list = document.createElement("ul");
    for (const error of errorsToShow) {
      const item = document.createElement("li");
      const timestamp = document.createElement("time");
      timestamp.className = "error-time";
      timestamp.textContent = new Date(error.timestamp).toLocaleTimeString();
      const message = document.createElement("span");
      message.textContent = error.message;
      item.append(timestamp, message);
      list.append(item);
    }
    container.append(heading, list);
  }

  function updateStatus() {
    return fetch("/status", { cache: "no-store" })
      .then(response => {
      if (!response.ok) throw new Error(`status request failed: ${response.status}`);
        return response.json();
      })
      .then(status => {
      updateService(
        "recording", status.recs.service, recordingText(status.recs), "recs-health",
      );
      updateService(
        "streaming", status.twitcho.service, streamingText(status.twitcho),
        "twitcho-health",
      );
      updateChannels(status.recs.channels);
      updateRecsErrors(status.recs.errors, status.run_started_at);
      document.getElementById("temperature").textContent =
        status.system.temperature_c === null
        ? status.system.temperature_error || "unknown"
        : `${status.system.temperature_c.toFixed(1)} °C`;
      document.getElementById("bitrate").textContent =
        status.twitcho.output_bitrate_kbps === null
        ? "unknown"
        : `${status.twitcho.output_bitrate_kbps.toFixed(0)} kbps`;
      document.getElementById("mixer-latency").textContent =
        status.mixer.latency_ms === null
        ? status.mixer.error || "unknown"
        : `${status.mixer.latency_ms.toFixed(1)} ms`;
      document.getElementById("x18-recorder").textContent =
        status.x18.last_error || status.x18.log_path === null
        ? status.x18.last_error || status.x18.state
        : `${status.x18.state}: ${status.x18.log_path} (${status.x18.log_size} bytes)`;
      document.getElementById("generated-at").textContent = new Date(
        status.generated_at * 1000,
      ).toLocaleTimeString();
      })
      .catch(error => {
      document.getElementById("generated-at").textContent =
        "stale (status update failed)";
      });
  }

  function pollStatus() {
    updateStatus().then(() => setTimeout(pollStatus, 1000));
  }

  document.getElementById("show-all-errors").addEventListener("change", () => {
    updateRecsErrors(latestErrors, showcoStartedAt);
  });

  document.getElementById("save-track-names").addEventListener(
    "click", saveTrackNames,
  );
  document.getElementById("revert-track-names").addEventListener(
    "click", revertTrackNames,
  );
  for (const input of document.querySelectorAll("#mutable-attributes input")) {
    input.addEventListener("blur", saveMutableAttribute);
  }

  pollStatus();
</script>
"""

CSS = """
body {
  background: #f7f4ed;
  color: #171717;
  font-family: system-ui, sans-serif;
  margin: 0;
}
header {
  align-items: center;
  background: #eee4d4;
  display: flex;
  justify-content: space-between;
  padding: 0.75rem 1rem;
}
h1, h2 { margin: 0.25rem 0; }
nav a {
  color: #171717;
  font-size: 1.2rem;
  font-weight: 700;
  margin-left: 1rem;
}
main {
  display: grid;
  gap: 1rem;
  padding: 1rem;
}
.cards, .actions, .levels {
  display: grid;
  gap: 0.75rem;
  grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr));
}
.card, .actions > form, section {
  background: #fffaf0;
  border: 2px solid #2b2b2b;
  border-radius: 0.75rem;
  padding: 1rem;
}
.state {
  font-size: 1.6rem;
  font-weight: 800;
}
.connected { border-color: #14853d; }
.stale { border-color: #b57900; }
.offline, .error, .failed { border-color: #b3261e; }
#recs-errors li {
  display: grid;
  gap: 0.75rem;
  grid-template-columns: 5.5rem 1fr;
}
.error-time {
  font-family: ui-monospace, monospace;
}
.toggle {
  align-items: center;
  display: flex;
  gap: 0.5rem;
}
.toggle input {
  margin: 0;
  min-height: 1rem;
  width: auto;
}
.level {
  border-radius: 0.5rem;
  color: white;
  display: grid;
  gap: 0.5rem;
  padding: 0.75rem;
}
.level label {
  margin: 0;
}
.channel-caption {
  align-items: center;
  display: flex;
  gap: 0.5rem;
}
.level input {
  margin: 0.25rem 0 0;
  min-height: 2rem;
}
.channel-actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.75rem;
}
.channel-actions button {
  width: auto;
}
.attributes {
  display: grid;
  gap: 0.75rem;
  grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
}
.mutable-attribute {
  margin: 0;
}
.channel-state {
  font-size: 3rem;
  line-height: 1;
}
.indicator-green { color: #14853d; }
.indicator-red { color: #b3261e; }
.silent { background: #777; }
.present { background: #3366cc; }
.healthy { background: #14853d; }
.clipping { background: #b3261e; }
button, input {
  box-sizing: border-box;
  display: block;
  font-size: 1.1rem;
  margin-top: 0.5rem;
  min-height: 2.5rem;
  width: 100%;
}
button {
  background: #f0c24b;
  border: 2px solid #171717;
  border-radius: 0.5rem;
  font-weight: 800;
}
.ok { color: #14853d; }
.failed { color: #b3261e; }
"""
