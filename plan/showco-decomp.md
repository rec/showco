# showCo decomposition

Implementation complete. This work covers review items 26, 28, 29, and 30 and
application ownership from item 27. MusicController's lifecycle structure is
unchanged; its separate work remains in
[music-controller-decomp.md](music-controller-decomp.md).

## Additional work beyond the prompt

None.

## Module ownership

| Owner | Responsibility |
| --- | --- |
| `runtime/server.py` | HTTP routing, request limits, responses, waveform transport, and diagnostic downloads |
| `runtime/app.py:ShowcoApp` | Dependency composition, action serialization, protection and ownership gates, action history, and status assembly |
| `runtime/recs.py:RecsClient` | recs RPC and reply interpretation |
| `runtime/recs_actions.py` | recs form parsing and dispatch: calibration, musicians, track/stereo/attribute edits, playback, and command fields |
| `runtime/workflows.py` | Workflow form parsing and commands using concrete controllers, status, and explicit callbacks |
| Existing workflow controllers | State transitions, revisions, pending commands, and save-failure policy |
| `runtime/readiness.py` | Readiness and enabled-input checks |
| `streamo/client.py` and existing service controls | streamO RPC, health interpretation, and service lifecycle |
| `runtime/views.py` and `site/` | Initial HTML and live presentation of the same status contract |

The recs dispatcher accepts `RecsClient`, an action name, and form data and
returns `ActionResult`. It does not acquire application locks or repeat global
protection rules. streamO orchestration remains explicit in ShowcoApp.

Workflow functions cannot access the whole application. ShowcoApp owns workflow
status assembly and locking, including soundcheck invalidation and incident
recording. Recovery receives explicit refresh, restart, incident, and invalidation
callbacks. Diagnostic download serialization and transport live in server.py.

## Status and configuration contract

- Service connectivity is tested with `service.state == 'connected'`.
  `ServiceStatus.fresh` has been removed.
- `RecsStatus.recording` has been removed. Connectivity, snapshot availability,
  `paused`, and recording-write progress remain separate observations.
- `snapshot_available` can describe retained data while disconnected. Only a
  connected, available, unpaused snapshot is described as recording enabled.
  Advancing writes are reported separately; neither observation proves physical
  output or usable audio.
- Elapsed session time, recorded audio duration, file count, and write progress
  remain distinct counters.
- OAuth exchange uses `stream.callback_url`, containing the full callback URL
  with the pending state. The former `callback_url_or_code` spelling and raw-code
  input are not supported. Private operational configuration was not edited.

Readiness, incidents, progress monitoring, soundcheck, rehearsal, initial HTML,
browser rendering, and tests use this contract without compatibility aliases.

## Persistence and helper consolidation

Set list, lighting, soundcheck, recovery, performance lock, and incidents use
`reccy.runtime.files.atomic_output` instead of fixed `.tmp` paths. Their durable
intent, side-effect, confirmation, and in-memory assignment order is preserved.
Atomic replacement does not turn uncertain delivery into a transaction or retry.

Soundcheck retains pending-save evidence after failed invalidation. Incident
storage errors remain visible and recover on a later save. No generic state store
was introduced. `workflow_storage.quarantine()` and
`deployment/repositories.py` retain their shared responsibilities.

The one-use input_check module was removed; its checks and tests now live with
readiness. The immediately unpacked auth_options/AuthOptions pair and unused auth
provision_dir helper were removed. Existing auth command syntax is unchanged.

## Presentation and action guarantees

- Server rendering remains responsible for first paint. Browser refreshes use
  the same connectivity, snapshot, paused, and write-progress distinctions.
- Both disk displays label retained values stale when disk or snapshot polling
  reports an error. Missing metrics remain visibly unavailable.
- Initial and refreshed controls apply the same recording-availability, closing,
  end-show, playback, stream-switch, and performance-protection rules.
- `ShowcoApp.run_action()` serializes actions. Manual recording controls reject
  immediately if another action owns the lock, during closing/transitions, or
  during conflicting cable-test ownership; they are never queued.
- Performance protection, confirmation, and action logging apply once per action.
  Workflow polls return busy during actions; health polls retain separate capacity.
- Set-list and lighting commands retain pending outcomes after uncertain delivery;
  revisions prevent stale requests from silently replacing current state.
- Browser actions retain the shared `showAction()` deadline and unknown-outcome
  path. Display-specific formatting remains local where wording and precision
  differ; no additional formatter wrappers were needed.

The parity test matrix compares initial HTML with browser text and disabled
controls for connected/offline, snapshot availability, paused/enabled recording,
advancing writes, closing, protection, and stale/missing disk metrics. Existing
browser tests cover performance controls, action deadlines, disconnection, and
pending requests. Controller tests retain coverage of uncertain outcomes and
storage failures.

## Operational boundaries

No application, deployment, service restart, or hardware flow was launched for
this refactor. The successful show already supplied the user's hardware acceptance.
Cable testing remains a separate utility. `showco-errors.txt` still describes
exactly the last invocation, with bounded capture and no failure archive.

Each implementation commit passed the full unit suite, Ruff, ty, pyupgrade, and
diff checks. Unrelated operational files were preserved.
