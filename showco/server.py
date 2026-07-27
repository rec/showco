from __future__ import annotations

import html
import time
import urllib.parse
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

from .mixer import MixerMonitor
from .models import ActionResult, ShowStatus
from .recs import RecsClient
from .system import SystemMonitor
from .twitcho import TwitchoClient
from .twitcho.supervisor import TwitchoSupervisorLike


class ShowcoApp:
    def __init__(
        self,
        recs: RecsClient,
        twitcho: TwitchoClient,
        system: SystemMonitor,
        mixer: MixerMonitor,
        twitcho_supervisor: TwitchoSupervisorLike | None = None,
    ) -> None:
        self.recs = recs
        self.twitcho = twitcho
        self.system = system
        self.mixer = mixer
        self.twitcho_supervisor = twitcho_supervisor
        self.action_log: list[ActionResult] = []

    def status(self) -> ShowStatus:
        twitcho = self.twitcho.status()
        if self.twitcho_supervisor and not twitcho.service.fresh:
            twitcho = replace(twitcho, service=self.twitcho_supervisor.status())
        return ShowStatus(
            recs=self.recs.status(),
            twitcho=twitcho,
            system=self.system.status(),
            mixer=self.mixer.status(),
        )

    def run_action(self, form: dict[str, str]) -> ActionResult:
        action = form.get("action", "")
        if action == "recs-calibrate":
            result = self.recs.calibrate()
        elif action == "twitcho-restart":
            if self.twitcho_supervisor:
                result = self.twitcho_supervisor.restart()
            else:
                result = ActionResult(False, "twitcho supervisor is not configured")
        elif action in TWITCHO_ACTIONS:
            result = self.twitcho.action(
                TWITCHO_ACTIONS[action], **_twitcho_fields(form)
            )
        else:
            result = ActionResult(False, f"unknown action {action}")
        self.action_log = [result, *self.action_log[:9]]
        return result

    def start(self) -> None:
        if self.twitcho_supervisor:
            self.twitcho_supervisor.start()

    def close(self) -> None:
        if self.twitcho_supervisor:
            self.twitcho_supervisor.close()


class ShowcoHandler(BaseHTTPRequestHandler):
    app: ClassVar[ShowcoApp]

    def do_GET(self) -> None:
        if self.path in {"/", "/home"}:
            self._html(home_page(self.app.status()))
            return
        if self.path == "/actions":
            self._html(actions_page(self.app.action_log))
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
        parsed = urllib.parse.parse_qs(body)
        return {k: v[-1] for k, v in parsed.items() if v}

    def _html(self, body: str) -> None:
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
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
) -> ThreadingHTTPServer:
    handler = type("ConfiguredShowcoHandler", (ShowcoHandler,), {})
    app = ShowcoApp(
        recs or RecsClient(),
        twitcho or TwitchoClient(),
        system or SystemMonitor(),
        mixer or MixerMonitor(),
        twitcho_supervisor,
    )
    handler.app = app
    server = ShowcoServer((host, port), handler)
    server.app = app
    app.start()
    return server


def home_page(status: ShowStatus) -> str:
    recs = status.recs.service
    twitcho = status.twitcho.service
    channel_html = "".join(level(c.name, c.state) for c in status.recs.channels)
    if not channel_html:
        channel_html = "<p>No channel data from recs.</p>"
    return page(
        "Home",
        f"""
        <section class="cards">
          {service_card("Recording", recs.state, _recording_text(status))}
          {service_card("Streaming", twitcho.state, _streaming_text(status))}
        </section>
        <section>
          <h2>Recording channels</h2>
          <div class="levels">
            {channel_html}
          </div>
        </section>
        <section>
          <h2>Health</h2>
          <p>recs: {_service_detail(recs.state, recs.last_error)}</p>
          <p>twitcho: {_service_detail(twitcho.state, twitcho.last_error)}</p>
          <p>Pi temperature: {_temperature(status)}</p>
          <p>Twitch bitrate: {_bitrate(status)}</p>
          <p>Mixer latency: {_mixer_latency(status)}</p>
          <p>Generated: {_time(status.generated_at)}</p>
        </section>
        """,
    )


def actions_page(action_log: list[ActionResult]) -> str:
    title_fields = ["title", "category", "tags"]
    return page(
        "Actions",
        f"""
        <section class="actions">
          {button("recs-calibrate", "Calibrate noise floor")}
          {button("twitcho-restart", "Restart Twitch")}
          {button("twitcho-mute", "Mute Twitch")}
          {button("twitcho-unmute", "Unmute Twitch")}
          {button("twitcho-stop", "Stop Twitch", confirm=True)}
          {field_action("twitcho-title", "Update stream info", title_fields)}
          {field_action("twitcho-chat", "Send chat message", ["message"])}
          {field_action("twitcho-announce", "Send announcement", ["message"])}
          {button("twitcho-clip", "Create clip")}
          {field_action("twitcho-marker", "Create stream marker", ["description"])}
        </section>
        <section>
          <h2>Recent actions</h2>
          {"".join(action_result(r) for r in action_log) or "<p>No actions yet.</p>"}
        </section>
        """,
    )


def page(title: str, body: str) -> str:
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
</body>
</html>"""


def service_card(title: str, state: str, detail: str) -> str:
    return f"""
    <article class="card {html.escape(state)}">
      <h2>{html.escape(title)}</h2>
      <div class="state">{html.escape(state)}</div>
      <p>{html.escape(detail)}</p>
    </article>
    """


def level(name: str, state: str) -> str:
    safe_name = html.escape(name)
    safe_state = html.escape(state)
    return (
        f'<div class="level {safe_state}">'
        f"<b>{safe_name}</b><span>{safe_state}</span></div>"
    )


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


def action_result(result: ActionResult) -> str:
    state = "ok" if result.ok else "failed"
    return f'<p class="{state}">{html.escape(result.message)}</p>'


def _recording_text(status: ShowStatus) -> str:
    if not status.recs.recording:
        return "stopped"
    elapsed = _duration(status.recs.elapsed_seconds)
    files = status.recs.file_count if status.recs.file_count is not None else "?"
    return f"recording for {elapsed}, {files} files"


def _streaming_text(status: ShowStatus) -> str:
    state = status.twitcho.stream_state
    muted = ", muted" if status.twitcho.muted else ""
    return f"{state}{muted}"


def _service_detail(state: str, error: str | None) -> str:
    return f"{state}: {error}" if error else state


def _temperature(status: ShowStatus) -> str:
    if status.system.temperature_c is not None:
        return f"{status.system.temperature_c:.1f} °C"
    return status.system.temperature_error or "unknown"


def _bitrate(status: ShowStatus) -> str:
    if status.twitcho.output_bitrate_kbps is None:
        return "unknown"
    return f"{status.twitcho.output_bitrate_kbps:.0f} kbps"


def _mixer_latency(status: ShowStatus) -> str:
    if status.mixer.latency_ms is not None:
        return f"{status.mixer.latency_ms:.1f} ms"
    return status.mixer.error or "unknown"


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
.card, form, section {
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
  display: flex;
  justify-content: space-between;
  padding: 0.75rem;
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
