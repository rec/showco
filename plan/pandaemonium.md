# Pandaemonium: A Configured Reccy Base

## Goal

Provide a `Reccy` base class in the Reccy package that assembles the existing
daemon-related facilities for Recs, Showco, Twitcho, and Lyte. A derived class
declares only the facilities it needs and overrides narrow methods for its
application behavior.

The result must not turn Reccy into a mandatory framework. `reccy.service`,
`reccy.paths`, `reccy.rpc`, `reccy.logging`, renderers, and configuration
helpers remain directly usable as they are today. `Reccy` is the convenient
composition layer for applications which want their complete lifecycle.

`__init__.py` must remain empty, so the class should live in
`reccy/reccy.py` and be imported as `from reccy.reccy import Reccy`. It is not
re-exported from the package root.

## Design Constraints

- Do not change Recs's internal GUI protocol. Its GUI remains an application
  facility alongside the new external Reccy RPC endpoint.
- Control RPC and event subscription traffic use distinct endpoints and
  connections. A connection has exactly one reader.
- A service is optional. A program may use logging, settings, RPC, or status
  persistence without becoming a service.
- IPC is optional. A service may have no public commands or events.
- Status and saved settings are distinct files with distinct semantics:
  status is an atomic, current runtime snapshot; settings are durable,
  user-controlled configuration.
- Derived applications own their public command names, status fields, events,
  and setting model. Reccy owns transport, lifecycle ordering, paths, and
  standard failure reporting.
- The base class does not run an event loop or create a background worker for
  application logic. It only starts and stops facilities; the derived class
  retains its existing synchronous or threaded runtime model.

## `Reccy` Contract

`Reccy` is a Pydantic `BaseModel` with `frozen=True` where possible. Mutable
runtime resources are private attributes created during `start()` and cleared
during `close()`.

Derived classes override class members and methods rather than pass a large
constructor configuration object:

```python
class LyteDaemon(Reccy):
    service_spec = LYTE_SERVICE
    settings_model = LyteSettings
    status_model = LyteStatus
    rpc_enabled = True
    rpc_role = 'lyte'

    def rpc_response(self, request: rpc.Request) -> rpc.Response:
        ...

    def status_snapshot(self) -> LyteStatus:
        ...

    def run(self) -> int:
        ...
```

The base provides these declared members, with conservative defaults:

| Member | Default | Meaning |
| --- | --- | --- |
| `service_spec` | `None` | A `ServiceSpec`; enables service paths and controller helpers. |
| `settings_model` | `None` | A Pydantic model; enables settings loading and atomic saving. |
| `status_model` | `None` | A Pydantic model; enables atomic runtime-status snapshots. |
| `rpc_enabled` | `False` | Enables the paired external control and event endpoints. |
| `rpc_role` | class name | Handshake role for the RPC server. |
| `logger_name` | module and class name | Logger name used by standard setup. |
| `platform` | `current_platform()` | Overridable for tests and service rendering. |
| `home` | `Path.home()` | Overridable root for all generated paths and tests. |

The base exposes properties only when their required declaration exists:

- `paths`: standard service paths when `service_spec` is set.
- `service_controller`: a `ServiceController` when `service_spec` is set.
- `settings_path`: a conventional configuration path when `settings_model` is
  set.
- `status_path`: a conventional state path when `status_model` is set.
- `control_endpoint` and `event_endpoint`: paired paths when RPC is enabled.

Attempting to use an unavailable facility raises a direct `RuntimeError`
explaining which class member must be supplied. This is better than silently
creating service or IPC files for a program that did not request them.

## Lifecycle

`Reccy.start()` performs only enabled steps in this order:

1. Configure logging.
2. Load settings, validate them through the declared settings model, and call
   `apply_settings(settings)`.
3. Start the RPC server when enabled.
4. Write the initial status snapshot when enabled.
5. Call the derived `on_started()` hook.

`Reccy.close()` performs the reverse order:

1. Call `on_stopping()` so the application can stop rendering, recording, or
   subprocesses while RPC clients can still receive a final event.
2. Publish the final status and `stopped` event when enabled.
3. Close the event and control endpoints.
4. Call `on_closed()` for application-only cleanup.

`run_daemon()` is a small template method: it calls `start()`, then the derived
`run()`, catches only expected termination signals, records failures as status
and error events, and always calls `close()`. Applications that already own
their process lifecycle may call `start()` and `close()` directly.

## RPC And Events

The current `reccy.rpc` request, response, event, and subscription primitives
remain the wire format. `Reccy` supplies the server, endpoint derivation, and
standard dispatch:

- `rpc_response(request)` is required when `rpc_enabled` is true. It returns a
  `Response`; unknown commands become a failed response.
- `publish_event(name, **data)` sends an application event.
- `publish_error(message, *, exception=None)` appends a timestamped error to
  the status snapshot and publishes an `error` event.
- `publish_status()` calls `status_snapshot()`, atomically writes it when
  status is enabled, and publishes a `status` event.

The base should define a minimal standard status envelope: `running`,
`updated_at`, and `errors`. Application status fields live in the derived
status model rather than an untyped catch-all dictionary. A client can always
subscribe to errors and status without knowing an application's other events.

## Settings

Add a small `reccy.settings` facility rather than putting persistence in the
base class itself. It provides:

- TOML or JSON loading selected by the declared path extension.
- Pydantic validation through the application's `settings_model`.
- Atomic write with a temporary sibling file and replace.
- A missing-file result distinct from malformed settings.
- Explicit `load_settings()` and `save_settings(value)` APIs.

`Reccy` only calls `load_settings()` during startup and exposes
`save_settings()` for a derived RPC command or local UI action. It never
silently saves every runtime mutation. Recs in particular must decide which
mutable controls are durable before opting into a settings model.

## Services

Keep `ServiceSpec`, `ServiceController`, renderers, metadata, and paths as
separate Reccy facilities. Add `Reccy.install_service(daemon_argv)`,
`uninstall_service()`, `start_service()`, `stop_service()`, `restart_service()`,
and `service_status()` as thin convenience methods when `service_spec` exists.

The derived class supplies `daemon_argv()` and may override metadata or status
model conversion. Reccy retains platform-specific rendering and does not make
derived classes know about systemd, launchd, or scheduled-task details.

Service metadata needs both control and event endpoints. Extend
`DaemonMetadata` and `ServicePaths` with an optional `event_endpoint`, while
preserving the existing control endpoint field. Renderers need no endpoint
arguments because endpoints are derived from the service path layout.

## Application Migration

### Recs

1. Leave `recs.daemon.gui_protocol` and `gui_ipc` unchanged.
2. Make the recorder daemon derive from `Reccy` for its external control/event
   interface, status writing, logging setup, and service lifecycle.
3. Translate external RPC requests into the existing recorder control handlers;
   do not expose GUI messages or GUI endpoint names as the public protocol.
4. Publish rows, recording state, and timestamped errors through external
   status/events.
5. Do not add saved settings until the existing Recs settings plan defines
   durable mutable fields and replay semantics.

### Showco

1. Keep the HTTP server as Showco's application runtime.
2. Derive its target-machine runtime from `Reccy` only if it needs a service,
   saved UI settings, or external RPC. Do not force an RPC server merely to
   serve HTTP.
3. Use `Reccy` clients for Recs, Twitcho, and Lyte commands and event
   subscriptions; fold received status/error events into its existing `/status`
   snapshot.
4. Keep the provisioning-machine CLI outside the daemon base.

### Twitcho

1. Replace the current manual RPC server construction with a `Reccy` subclass.
2. Keep streaming, Twitch API actions, and ffmpeg supervision in Twitcho.
3. Expose status, mute, unmute, stop, and Twitch actions through
   `rpc_response()`.
4. Publish streaming transitions, ffmpeg failures, clipping state, and errors
   through standard status/error events.
5. Add saved settings only for local user choices that are genuinely mutable;
   credentials remain external secret configuration.

### Lyte

1. Replace the current manual RPC server construction with a `Reccy` subclass.
2. Keep the synchronous MIDI/render loop in Lyte.
3. Expose `status`, `blackout`, `stop`, and `select_patch` through
   `rpc_response()`.
4. Publish patch selection, Twinkly recovery, blackout completion, and errors
   as events.
5. Use settings persistence only after distinguishing durable playback choices
   from the daemon TOML source configuration.

## Implementation Order

1. Add `reccy.settings` and tests for missing, invalid, valid, and atomic
   saves.
2. Extend Reccy service paths and metadata with paired event endpoints; update
   service renderer tests.
3. Add the `Reccy` base class with no application migration, covering every
   optional-facility combination in focused tests.
4. Migrate Twitcho and Lyte first because their external RPC contracts are
   small and have no internal GUI compatibility constraint.
5. Migrate Recs's external interface while retaining its GUI protocol intact.
6. Migrate Showco clients to RPC/event subscriptions and preserve its HTTP
   polling fallback.
7. Add cross-project protocol tests using real local sockets, then perform
   target-machine service and hardware checks.

## Evaluation

This would improve the four systems if the base remains a thin opt-in
composition layer. They now repeat service metadata, local endpoint setup,
status/error publication, lifecycle cleanup, and client conventions. Centralizing
those mechanics would make the daemon contracts more consistent and reduce
deployment errors. It would make the code worse if `Reccy` absorbed recording,
streaming, lighting, or HTTP behavior, or if every program had to enable every
facility. The proposed optional members and retained standalone modules keep
that boundary explicit.

## Additional Work Beyond The Prompt

None.
