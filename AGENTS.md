# Agent Instructions

## Project Overview

showCo is a small Python 3.13 show-control web UI for coordinating local
recording with `recs`, streaming with `streamo`, mixer reachability, and
Raspberry Pi health checks.

The runtime entry point is `showco.cli:main`, exposed as the `showco` script.
The default app is a stdlib `ThreadingHTTPServer` with hand-rendered HTML in
`showco/runtime/server.py`. Avoid introducing web frameworks, template engines, or new
client-side tooling unless the user explicitly asks for that direction.

## Important Modules

- `showco/cli.py`: command routing, `showco go`, and rehearsal mode.
- `showco/runtime/server.py`: request handling, app orchestration, and action dispatch.
- `showco/runtime/views.py`: HTML rendering. Static CSS and JavaScript live in `site/`.
- `showco/runtime/waveforms.py`: waveform subscriptions and bounded event history.
- `showco/runtime/models.py`: shared status/result Pydantic models. Keep these simple and
  explicit.
- `showco/runtime/recs_control.py`, `showco/runtime/recs_snapshot.py`, and `showco/runtime/recs.py`:
  public recs RPC, cached status, actions, and waveform integration.
- `showco/streamo/`: streamO RPC and Stream authentication adapters.
- `showco/runtime/lyte.py`: lyte status and light-test RPC adapter.
- `showco/runtime/mixer.py`: TCP/UDP mixer reachability probes.
- `showco/runtime/system.py` and `showco/runtime/monitoring.py`: Raspberry Pi health sampling
  and retained monitoring aggregates.
- `showco/runtime/rehearsal.py`: in-process fakes for local rehearsal and tests.
- `showco/provision/`: configuration, card preparation, remote provisioning,
  verification, and the generated target script.
- `showco/provision/templates/`: system, network, service, and execution sections
  assembled into one remote shell script.
- `showco/deployment/target_update.py`: target transaction and rollback.
- `showco/deployment/update.py` and `showco/deployment/local_update.py`: target deployment and local
  publication for reccy, recs, streamo, lyte, and showco.

## Coding Conventions

- Prefer the standard library and existing dependencies. showCo imports reccy
  and recs directly and manages reccy, recs, streamO, and lyte as sibling target
  checkouts.
- Keep implementations direct. This codebase uses small classes, Pydantic
  models, plain functions, dependency injection for tests, and explicit status
  objects.
- Preserve the current adapter boundaries: recs, streamO, mixer, system, and
  lyte behavior should remain independently testable.
- Keep user-visible strings stable unless changing the UI behavior is the point
  of the task. Tests often assert visible HTML and action messages.
- Use injected paths, sockets, subprocess runners, and fakes in tests instead of
  contacting local services.
- Do not add retries, background tasks, broad compatibility paths, or new
  abstractions unless the existing failure mode or user request justifies them.
- Do not store secrets in the repository unless the user explicitly scopes work
  to a private/unpushable branch. Treat `showco/provision/secrets.toml` as secret
  operational material.

## Testing

Use `uv` for local checks.

Focused tests:

```bash
uv run pytest test/test_server.py
uv run pytest test/test_streamo.py
uv run pytest test/test_recs.py
uv run pytest test/test_provision.py
uv run pytest test/test_update.py
```

`ty check showco` is expected to pass.

## Runtime And Hardware Boundaries

- Do not launch `uv run showco`, rehearsal mode, system services, streamO flows,
  or hardware-facing checks unless the user explicitly asks.
- Do not run `showco go` as a verification step. It pushes or pulls sibling
  repos and stops/restarts user services.
- reccy, recs, streamO, and lyte are sibling projects. Changes that belong in
  those projects should be made there only when the user scopes the task that
  way.
- Hardware/streaming acceptance details live in `doc/`. Automated tests do not prove
  Raspberry Pi networking, X18 audio, external storage, stream credentials, or
  venue behavior.

## Git And Scope

- Preserve unrelated working-tree changes. This repository often has local
  operational files and secrets present.
- Stage exact files only.
- Keep dependency/tooling changes in a separate commit from behavior changes.
- If `uv` updates `uv.lock`, inspect for unrelated `exclude-newer` metadata
  churn before committing.
- Reject a dirty `uv.lock` before provisioning or updating. `showco go` may
  stage and commit only lockfile changes it generated while refreshing internal
  dependencies; it must not include any other changed path.
