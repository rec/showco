# showCo decomposition plan

Implementation in progress. Status and OAuth naming are complete; remaining
steps below are being implemented without changing MusicController's structure.
This plan takes over review items 26, 28, 29, and 30 and the application portion
of item 27. Music lifecycle decomposition is specified in
[music-controller-decomp.md](music-controller-decomp.md).

## Additional work beyond the prompt

None.

## Purpose

Make action ownership, status guarantees, and persistence behavior apparent from
their owning modules. Preserve the current stdlib HTTP server, Pydantic models,
Jinja rendering, browser scripts, and independently testable service adapters.
Do not introduce a framework, generic command bus, controller hierarchy, plugin
registry, new dependency, or parallel compatibility path.

## Existing contracts to preserve

- `ShowcoApp.run_action()` serializes actions. Manual recording controls reject
  immediately when another action owns the lock; they are never queued.
- Closing and cable-test ownership also block recording changes. Cable testing
  remains a separate utility, not a source of live-show incident requirements.
- Performance protection, confirmation checks, and action logging apply once per
  action, before or after service dispatch as appropriate.
- Workflow polls return busy immediately during actions. Ordinary health polls
  retain their separate capacity.
- Service connectivity, a retained snapshot, enabled recording, and advancing
  audio writes are different observations. None proves physical output.
- Set-list and lighting commands retain pending outcomes after uncertain delivery;
  their revisions prevent stale requests from silently replacing current state.
- Failed soundcheck invalidation updates in-memory evidence even when its save
  fails. Incident persistence failures remain visible and can recover.
- `showco-errors.txt` describes the last invocation only. Its in-run capture is
  bounded; no failure archive is introduced.

## Target ownership

| Owner | Responsibility |
| --- | --- |
| `runtime/server.py` | HTTP routing, request limits, responses, waveform transport, and diagnostic-download transport |
| `runtime/app.py:ShowcoApp` | Dependency composition, action serialization, shared protection/ownership gates, action history, and status assembly |
| `runtime/recs.py:RecsClient` | recs RPC and reply interpretation |
| `runtime/recs_actions.py` | Cohesive recs form parsing and action dispatch currently embedded in `ShowcoApp` |
| `streamo/client.py` and existing service controls | streamO RPC, health interpretation, and service lifecycle |
| `runtime/workflows.py` | Workflow commands and coherent workflow snapshots; no HTTP download implementation |
| Existing workflow controllers | Their own state transitions, revisions, pending commands, and save-failure policy |
| `runtime/views.py` and `site/` | Initial HTML and live presentation of the same status contract |

The proposed recs action module must contain the complete cohesive group:
calibration, track/stereo/attribute edits, musicians, playback, and recs command
field parsing. It should expose a direct function accepting `RecsClient`, the
action, and form data, returning `ActionResult`. It must not acquire app locks,
reach into unrelated controllers, or repeat global protection rules. Move the
old branches and helpers completely; do not leave forwarding copies in app.py.

Keep streamO orchestration explicit in the dispatcher until there is a similarly
cohesive extraction. Avoid adding a module for every short branch. Replace
`workflows.run(app, ...)`'s unrestricted application access with the particular
controllers, status, and restart/incident callbacks that its branches need;
prefer existing concrete types and plain functions over new interface layers.

## Naming and status semantics

1. Remove the misleading `ServiceStatus.fresh` property. Use the explicit
   connectivity comparison at consumers; do not introduce another alias with
   the same ambiguity. Actual observation times remain separate data.
2. Remove the connectivity-derived `RecsStatus.recording` flag. Use service state,
   snapshot availability, `paused`, and `recording_progress` for their respective
   purposes. A connected unpaused snapshot may be described as recording enabled
   or reported active, never as proof that usable audio was written.
3. Update readiness, incidents, recording-progress logic, soundcheck, rehearsals,
   initial rendering, browser rendering, and tests together. Trace all references
   first, including the JSON contract. Do not maintain both old and new fields.
4. Keep genuinely different counters distinct: elapsed session time, recorded
   audio duration, file count, and recording-write progress are not synonyms.
5. Record the callback setting's requirement for a full OAuth URL. Renaming
   `callback_url_or_code` belongs in this naming pass, including configuration
   parsing, instructions, and tests; do not add a second accepted spelling.

These are intended schema changes for later implementation, not changes made by
this plan. Check for documented consumers before executing that step and surface
any newly discovered external consumer rather than inventing compatibility.

## Persistence and small modules

Replace mechanical fixed-`.tmp` writes in setlist, lighting, soundcheck, recovery,
performance lock, and incidents with `reccy.runtime.files.atomic_output`. Retain
each controller's ordering of durable intent, side effects, confirmation, and
in-memory assignment. Atomic replacement alone is not a transaction and does not
justify retries or hiding an uncertain command outcome.

Preserve soundcheck's pending-save handling and incident storage-error reporting.
Do not introduce a generic state-store class just to share JSON serialization.
Keep `workflow_storage.quarantine()` and `deployment/repositories.py`; both own
shared responsibilities with multiple consumers.

Move `input_check.checks()` into an existing cohesive status/presentation owner
and remove the one-use module and stale imports. Remove `auth_options()` and its
immediately unpacked `AuthOptions` if no independent use remains, and remove the
unused auth `provision_dir()` after checking callers. Do not fold unrelated auth
HTTP or credential responsibilities into `ShowcoApp`.

## Browser and server parity

Transport forms now use the shared `showAction()` deadline and unknown-outcome
behavior. Preserve that single request path. Both disk presentations now label
retained values stale; retain parity during any presentation extraction.

Keep server rendering for first paint and progressive page behavior. Do not
replace it with a new frontend framework or move the entire UI into JSON-driven
client rendering. Document the shared formatting/disable rules and test a compact
matrix: connected/offline, retained snapshot, paused/enabled recording, advancing
writes, active closing, protected actions, and unavailable disk metrics. Prefer
existing browser harnesses and behavior checks over snapshots of all generated
markup. Extract shared browser formatters only where they have multiple actual
consumers; do not create one wrapper per label.

## Implementation sequence

1. Inventory production imports and status consumers; confirm the contracts above
   against current tests. Resolve newly discovered behavioral choices before edits.
2. Change connectivity/recording names and their consumers in one coherent commit.
3. Extract recs form dispatch and parsing; keep application ownership gates central.
4. Narrow workflow dependencies and put HTTP downloads with HTTP handling.
5. Reuse atomic output one controller group at a time, retaining distinct failure
   semantics and tests for uncertain outcomes.
6. Consolidate the named one-use helpers and remove their old definitions.
7. Check presentation parity and extract only repeated browser behavior that
   remains after the functional fixes.

Each implementation commit must stand alone, pass its focused tests and the full
suite, and preserve unrelated operational files. Run Ruff, ty, pyupgrade, and diff
checks for Python changes. Do not launch the app, deploy, restart services, or
contact hardware to validate a structural refactor without explicit instruction.
