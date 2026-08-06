# Reccy migration plan

Showco should adopt Reccy for daemon service installation, service control,
service status, path calculation, rendering, subprocess helpers, and the shared
IPC transport/handshake layer. Do not use this phase to extract Twitcho
supervision or X18 recorder supervision.

Backward compatibility inside Showco is not a constraint. Reccy can change when
the right shared abstraction is missing.

Additional work beyond the prompt

None.

## Current state from Recs

Recs now uses Reccy for daemon service/path/rendering internals and for the
shared GUI IPC transport/handshake layer. The useful pattern is that Recs did
not force its persisted JSON or application payloads to match Reccy's generic
models. It kept Recs-specific field names and payloads, and wrapped Reccy at the
boundary.

Important compatibility details:

- Recs metadata still uses `gui_endpoint`.
- Reccy metadata uses `control_endpoint`.
- Recs status still uses `gui_ipc_error`, `rows`, and `recording`.
- Reccy generic status uses `ipc_error`, `running`, and `fields`.
- Recs wraps `reccy.service.ServiceController` with
  `status_model=DaemonStatus`,
  `status_error_attribute="gui_ipc_error"`, and
  `status_error_label="GUI IPC error"`.
- Recs delegates paths and service renderers to Reccy, translating field names
  at the edges.
- Recs `Hello`, `Reply`, `Shutdown`, and `Error` inherit from `reccy.ipc`
  models.
- Recs still owns `Command`, `RowsMessage`, key messages, track names, and
  status row semantics.
- Recs `gui_backend` delegates Unix socket and Windows pipe transport to
  `reccy.ipc`.
- Recs `gui_ipc` uses `reccy.ipc.ProtocolListener` and `reccy.ipc.message_json`
  where that fits, while keeping Recs-specific command handling in Recs.

Showco should follow that pattern instead of trying to make Reccy own the Recs
application protocol.

## Scope

In scope for the next Showco adoption phase:

- add `reccy` as a local editable dependency
- replace Showco's subprocess wrapper with `reccy.subprocess.run`
- use Reccy service specs for installed user services
- replace hand-written Showco systemd service rendering
- replace hard-coded user-service control commands where Reccy provides the
  same operation cleanly
- replace Showco's Recs GUI IPC transport, newline JSON helpers, and shared
  hello/error/shutdown models with `reccy.ipc`
- keep Recs-specific commands, track names, rows, status mapping, and UI action
  messages out of Reccy
- keep dependency and behavior changes in separate commits

Out of scope for the next phase:

- do not extract `TwitchoSupervisor`
- do not extract `X18RecorderSupervisor`
- do not remove Showco's direct `recs` dependency yet
- do not move Recs-specific protocol payloads into Reccy
- do not change Showco's visible Recs controls while replacing the lower-level
  IPC pieces

## Reccy APIs that fit now

Use these existing Reccy modules:

- `reccy.subprocess.run`
- `reccy.models.ServiceSpec`
- `reccy.models.DaemonMetadata`
- `reccy.models.DaemonStatus`
- `reccy.models.ServicePaths`
- `reccy.models.StatusResult`
- `reccy.paths.current_platform`
- `reccy.paths.service_paths`
- `reccy.renderers.service_metadata`
- `reccy.renderers.metadata_json`
- `reccy.renderers.linux_systemd_unit`
- `reccy.service.ServiceController`
- `reccy.ipc.Connection`
- `reccy.ipc.client_connection`
- `reccy.ipc.message_json`
- `reccy.ipc.parse_message`
- `reccy.ipc.Hello`
- `reccy.ipc.Reply`
- `reccy.ipc.Shutdown`
- `reccy.ipc.Error`

Reccy also has `reccy.cli` and `reccy.config`, but those are optional cleanup
after the service migration. Recs currently uses only the daemon/service pieces,
plus IPC, so Showco should not broaden the first adoption step just because the
CLI and config modules exist.

## Showco service definitions

Add a small Showco service module, for example `showco/services.py`, with the
service specs Showco needs:

- `SHOWCO_SERVICE`
- `RECS_SERVICE`

`SHOWCO_SERVICE` should describe:

- name: `showco`
- display name: `Showco`
- systemd unit: from Reccy's `ServiceSpec.systemd_unit`
- daemon environment variable: a Showco-specific value
- Windows pipe value: only to satisfy the generic model, not because Showco
  needs Windows daemon IPC in this phase

`RECS_SERVICE` can either be local to Showco for provisioning/status checks or
imported from Recs only if doing so does not reintroduce unwanted runtime
coupling. The safer first step is to define the minimal Recs `ServiceSpec` in
Showco, matching Recs.

## Replacement steps

### 1. Add Reccy dependency

- Add `reccy` to `pyproject.toml`.
- Add `reccy = { path = "../reccy", editable = true }` to `[tool.uv.sources]`.
- Update `uv.lock`.
- Commit only `pyproject.toml` and `uv.lock`.

### 2. Replace `showco.run`

- Replace `showco.run(...)` calls with `reccy.subprocess.run(...)`.
- Remove the wrapper function from `showco/__init__.py`.
- Keep `showco.__version__`.
- Update tests that patch `showco.run` so they inject a runner or patch the
  Reccy subprocess boundary.

This step is useful on its own and does not change daemon behavior.

### 3. Add Showco service metadata helpers

Add focused helpers for Showco's own user service:

- compute service paths with `reccy.paths.service_paths`
- build metadata with `reccy.renderers.service_metadata`
- render the Linux systemd unit with `reccy.renderers.linux_systemd_unit`

If Reccy's renderer does not support a detail Showco actually needs, change
Reccy rather than copying renderer logic into Showco.

### 4. Replace provisioning service rendering

Replace the hand-written `showco.service` heredoc in
`showco/provision/provision_locally.tmpl.sh`.

Preferred shape:

- after repositories are synced, run a Showco Python command on the Pi that
  installs or refreshes the Showco service using Reccy
- keep the Bash provisioning script responsible for apt, git, uv, storage,
  network, and reboot orchestration
- move the service-file content generation out of Bash and into Python/Reccy

This should remove duplicated systemd details such as:

- `ExecStart=...`
- `Restart=always`
- `RestartSec=5`
- log paths under `~/.local/state/showco`
- `WantedBy=default.target`

### 5. Replace service control in `git_pull.py`

`showco/git_pull.py` currently shells out to:

- `systemctl --user stop recs`
- `systemctl --user stop showco`
- `systemctl --user restart recs`
- `systemctl --user restart showco`

Replace these operations with Reccy `ServiceController` where practical. Keep
the current result reporting shape so the existing UI and tests remain easy to
understand.

If using `ServiceController` makes command output less clear, preserve the
Showco `StepResult` wrapper and treat Reccy's `StatusResult.details` as the
step output.

### 6. Replace post-reboot service checks

`showco/provision/provision.py` currently checks service state with remote
`systemctl --user ...` commands. Replace the service-specific parts with a
small remote Showco command that uses Reccy service specs and controllers.

Keep checks that are not service-control abstractions in provisioning Python or
Bash:

- failed system services
- repository status
- X18 USB presence
- journal readability
- SSH reachability and reboot waiting

### 7. Replace Recs IPC transport and base messages

Keep `showco/recs.py` as the Showco UI adapter, but stop duplicating or reaching
through Recs for the transport and base protocol pieces that now live in Reccy.

Replace these Showco/Recs imports where practical:

- GUI connection creation
- hello
- replies
- shutdown
- errors
- newline JSON writing
- JSON message parsing helper

Keep these Recs-specific pieces in Showco or Recs:

- `Command`
- `RowsMessage`
- track-name payloads
- command result interpretation
- status-row parsing
- Showco `ActionResult` messages

For synchronous Showco actions, prefer using `reccy.ipc.client_connection`,
`reccy.ipc.message_json`, and the Recs `parse_message` adapter directly. Do not
force `reccy.ipc.ProtocolClient` into this path unless it makes the request/reply
flow clearer, because Showco actions currently send one command and wait for one
reply.

### 8. Consider CLI helpers later

After service adoption is complete, consider a separate small change to use
`reccy.cli.route_command` in `showco/cli.py`.

This is deliberately later because it is not needed for service adoption and
Showco is already preparing for lazy imports.

## Suggested commit sequence

1. Add Reccy dependency.
2. Replace `showco.run` with `reccy.subprocess.run`.
3. Add Showco service specs and Reccy service helpers.
4. Replace Showco service rendering in provisioning.
5. Replace `git_pull.py` service control.
6. Replace provisioning post-reboot service checks.
7. Replace Recs IPC transport and base messages with `reccy.ipc`.
8. Optionally port CLI routing to `reccy.cli`.
9. Update docs for the new Reccy boundaries.

Keep dependency changes separate from behavior changes.

## Verification

For each implementation step that changes Python:

```sh
uv run pytest test/test_provision.py test/test_git_pull.py test/test_cli.py
uv run pytest test/test_recs.py test/test_recs_protocol.py test/test_server.py
ruff check --fix --select B,E,F,I showco test
ruff format
ty check showco
version=$(cat .python-version)
version=${version//./}
find test showco -name '*.py' | xargs pyupgrade --py${version}-plus
```

For the service migration:

- provisioning tests should assert that Showco service content comes from the
  Python/Reccy path, not from a Bash heredoc
- `git_pull.py` tests should still prove Recs and Showco are stopped before git
  pulls and restarted only after successful pulls
- post-reboot provisioning checks should still report Recs and Showco service
  failures clearly
- X18 absence should remain a note, not an error

For the final state of this phase:

- `rg -n "systemctl --user (stop|restart|is-active).*\\.service|\\[Service\\]|ExecStart=" showco test`
  should show only deliberate shell orchestration that cannot yet use Reccy
- Showco may still import Recs-specific models such as `Command` and
  `DeviceTrackNames`
- Showco should not import Recs only to get socket/pipe connection helpers or
  generic hello/reply/shutdown/error models
- Showco should not have copied Reccy's systemd rendering logic
