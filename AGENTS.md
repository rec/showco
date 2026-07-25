# Agent Instructions

## Project Overview

Showco is a small Python 3.10 show-control web UI for coordinating local
recording with `recs`, Twitch streaming with `twitcho`, mixer reachability, and
Raspberry Pi health checks.

The runtime entry point is `showco.cli:main`, exposed as the `showco` script.
The default app is a stdlib `ThreadingHTTPServer` with hand-rendered HTML in
`showco/server.py`. Avoid introducing web frameworks, template engines, or new
client-side tooling unless the user explicitly asks for that direction.

## Important Modules

- `showco/cli.py`: command-line parsing, `showco git-pull`, rehearsal mode, and
  optional Twitcho supervision.
- `showco/server.py`: request handling, app orchestration, action dispatch, HTML,
  CSS, and form behavior.
- `showco/models.py`: shared status/result dataclasses. Keep these simple and
  explicit.
- `showco/recs.py`: adapter for Recs status files and GUI daemon protocol.
- `showco/twitcho.py`: socket protocol adapter for Twitcho control/status.
- `showco/twitcho_supervisor.py`: subprocess supervision and restart policy for
  a managed Twitcho process.
- `showco/mixer.py`: TCP/UDP mixer reachability probes.
- `showco/system.py`: Raspberry Pi temperature probe.
- `showco/rehearsal.py`: in-process fakes for local rehearsal and tests.
- `showco/git_pull.py`: operational update helper for recs, twitcho, and showco.

## Coding Conventions

- Prefer the standard library and existing dependencies. Current runtime
  dependencies are local editable `recs` and `twitcho` packages.
- Keep implementations direct. This codebase uses small classes, dataclasses,
  plain functions, dependency injection for tests, and explicit status objects.
- Preserve the current adapter boundaries: Recs, Twitcho, mixer, system, and
  supervisor behavior should remain independently testable.
- Keep user-visible strings stable unless changing the UI behavior is the point
  of the task. Tests often assert visible HTML and action messages.
- Use injected paths, sockets, subprocess runners, and fakes in tests instead of
  contacting local services.
- Do not add retries, background tasks, broad compatibility paths, or new
  abstractions unless the existing failure mode or user request justifies them.
- Do not store secrets in the repository. Treat `doc/secrets.toml` and
  `scripts/twitch-oauth/` as local operational material unless the user
  explicitly scopes work there.

## Testing

Use `uv` for local checks.

Focused tests:

```bash
uv run pytest test/test_server.py
uv run pytest test/test_twitcho.py
uv run pytest test/test_recs.py
uv run pytest test/test_twitcho_supervisor.py
```

Historical note: `ty check showco` has had baseline diagnostics in adapter code.
Recheck before reporting, and separate pre-existing diagnostics from regressions.

## Runtime And Hardware Boundaries

- Do not launch `uv run showco`, rehearsal mode, system services, Twitch flows,
  or hardware-facing checks unless the user explicitly asks.
- Do not run `showco git-pull` as a verification step. It stops/restarts user
  services and performs git pulls in sibling repos.
- Recs and Twitcho are sibling editable dependencies. Changes that belong in
  those projects should be made there only when the user scopes the task that
  way.
- Hardware/Twitch acceptance details live in `doc/`. Automated tests do not prove
  Raspberry Pi networking, X18 audio, external storage, Twitch credentials, or
  venue behavior.

## Git And Scope

- Preserve unrelated working-tree changes. This repository often has local
  operational files and secrets present.
- Stage exact files only.
- Keep dependency/tooling changes in a separate commit from behavior changes.
- If `uv` updates `uv.lock`, inspect for unrelated `exclude-newer` metadata
  churn before committing.
- Leave `doc/checklist.md` untouched unless the user explicitly asks to update
  checklist content.
