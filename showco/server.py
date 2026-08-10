from __future__ import annotations

import html
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar
from urllib import parse

from . import models
from .mixer import MixerMonitor
from .recs import RecsClient
from .system import SystemMonitor
from .twitcho.client import TwitchoClient
from .twitcho.supervisor import TwitchoSupervisorLike


class ShowcoApp:
    def __init__(
        self,
        recs: RecsClient,
        twitcho: TwitchoClient | None,
        system: SystemMonitor,
        mixer: MixerMonitor,
        twitcho_supervisor: TwitchoSupervisorLike | None = None,
    ) -> None:
        self.recs = recs
        self.twitcho = twitcho
        self.system = system
        self.mixer = mixer
        self.twitcho_supervisor = twitcho_supervisor
        self.action_log: list[models.ActionResult] = []
        self.action_log_lock = threading.Lock()

    def status(self) -> models.ShowStatus:
        if self.twitcho is None:
            twitcho = models.TwitchoStatus(
                service=models.ServiceStatus(name="twitcho", state="disabled")
            )
        else:
            twitcho = self.twitcho.status()
            if self.twitcho_supervisor and not twitcho.service.fresh:
                twitcho = twitcho.model_copy(
                    update={"service": self.twitcho_supervisor.status()}
                )
        return models.ShowStatus(
            recs=self.recs.status(),
            twitcho=twitcho,
            system=self.system.status(),
            mixer=self.mixer.status(),
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
            if self.twitcho_supervisor:
                result = self.twitcho_supervisor.restart()
            else:
                result = models.ActionResult(
                    ok=False, message="twitcho supervisor is not configured"
                )
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

    def start(self) -> None:
        if self.twitcho_supervisor:
            self.twitcho_supervisor.start()

    def close(self) -> None:
        if self.twitcho_supervisor:
            self.twitcho_supervisor.close()


class ShowcoHandler(BaseHTTPRequestHandler):
    app: ClassVar[ShowcoApp]

    def do_GET(self) -> None:
        if self.path == "/status":
            self._json(self.app.status())
            return
        if self.path in {"/", "/home"}:
            self._html(home_page(self.app.status()))
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
        if self.path != "/actions":
            self.send_error(404)
            return
        form = self._form()
        self.app.run_action(form)
        self.send_response(303)
        self.send_header("Location", "/actions")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return

    def _form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode()
        parsed = parse.parse_qs(body)
        return {k: v[-1] for k, v in parsed.items() if v}

    def _html(self, body: str) -> None:
        data = body.encode()
        self.send_response(200)
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


class ShowcoServer(ThreadingHTTPServer):
    app: ShowcoApp

    def server_close(self) -> None:
        self.app.close()
        super().server_close()


def make_server(
    host: str,
    port: int,
    *,
    recs: RecsClient | None = None,
    twitcho: TwitchoClient | None = None,
    system: SystemMonitor | None = None,
    mixer: MixerMonitor | None = None,
    twitcho_supervisor: TwitchoSupervisorLike | None = None,
    twitcho_enabled: bool = False,
) -> ThreadingHTTPServer:
    handler = type("ConfiguredShowcoHandler", (ShowcoHandler,), {})
    app = ShowcoApp(
        recs or RecsClient(),
        (twitcho or TwitchoClient()) if twitcho_enabled else None,
        system or SystemMonitor(),
        mixer or MixerMonitor(),
        twitcho_supervisor if twitcho_enabled else None,
    )
    handler.app = app
    server = ShowcoServer((host, port), handler)
    server.app = app
    app.start()
    return server


def home_page(status: models.ShowStatus) -> str:
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
        </section>
        <section>
          <h2>Health</h2>
          <p id="recs-health">recs: {_service_detail(recs.state, recs.last_error)}</p>
          <div id="recs-errors">{_recs_errors(status)}</div>
          <p id="twitcho-health">
            twitcho: {_service_detail(twitcho.state, twitcho.last_error)}
          </p>
          <p>Pi temperature: <span id="temperature">{_temperature(status)}</span></p>
          <p>Twitch bitrate: <span id="bitrate">{_bitrate(status)}</span></p>
          <p>Mixer latency: <span id="mixer-latency">{_mixer_latency(status)}</span></p>
          <p>Generated: <span id="generated-at">{_time(status.generated_at)}</span></p>
        </section>
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
        ["source", "noise_floor"],
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
    <form class="level {safe_state}" method="post" action="/actions"
          data-device="{safe_device}" data-channel="{safe_name}">
      <input type="hidden" name="action" value="recs-track-name">
      <input type="hidden" name="device" value="{safe_device}">
      <input type="hidden" name="channel" value="{safe_name}">
      <label>
        <b>{safe_name}</b>
        <input name="track_name" value="{safe_name}">
      </label>
      <span class="channel-state">{safe_state}</span>
      <button>Save</button>
    </form>
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


def _recs_errors(status: models.ShowStatus) -> str:
    if not status.recs.errors:
        return ""
    items = "".join(f"<li>{html.escape(e)}</li>" for e in status.recs.errors)
    return f"<p>Recs errors:</p><ul>{items}</ul>"


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

  function hiddenInput(name, value) {
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = name;
    input.value = value;
    return input;
  }

  function channelForm(channel, trackName) {
    const form = document.createElement("form");
    form.className = `level ${channel.state}`;
    form.method = "post";
    form.action = "/actions";
    form.dataset.device = channel.device;
    form.dataset.channel = channel.name;
    form.append(hiddenInput("action", "recs-track-name"));
    form.append(hiddenInput("device", channel.device));
    form.append(hiddenInput("channel", channel.name));
    const label = document.createElement("label");
    const title = document.createElement("b");
    title.textContent = channel.name;
    const input = document.createElement("input");
    input.name = "track_name";
    input.value = trackName;
    label.append(title, input);
    const state = document.createElement("span");
    state.className = "channel-state";
    state.textContent = channel.state;
    const button = document.createElement("button");
    button.textContent = "Save";
    form.append(label, state, button);
    return form;
  }

  function updateChannels(channels) {
    const container = document.getElementById("channels");
    if (document.activeElement.closest("#channels .level")) return;
    const names = new Map(
      [...container.querySelectorAll(".level")].map(form => [
        `${form.dataset.device}\\u0000${form.dataset.channel}`,
        form.querySelector("[name=track_name]").value,
      ]),
    );
    container.replaceChildren(...channels.map(channel => channelForm(
      channel,
      names.get(`${channel.device}\\u0000${channel.name}`) ?? channel.name,
    )));
  }

  function updateRecsErrors(errors) {
    const container = document.getElementById("recs-errors");
    container.replaceChildren();
    if (!errors.length) return;
    const heading = document.createElement("p");
    heading.textContent = "Recs errors:";
    const list = document.createElement("ul");
    for (const error of errors) {
      const item = document.createElement("li");
      item.textContent = error;
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
      updateRecsErrors(status.recs.errors);
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
.level {
  border-radius: 0.5rem;
  color: white;
  display: grid;
  gap: 0.5rem;
  grid-template-columns: minmax(8rem, 1fr) auto auto;
  align-items: end;
  padding: 0.75rem;
}
.level label {
  margin: 0;
}
.level input, .level button {
  margin: 0.25rem 0 0;
  min-height: 2rem;
}
.level button {
  width: auto;
}
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
