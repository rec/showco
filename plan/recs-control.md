# Recs Control API Migration

## Goal

Use the public Recs control API documented by `recs control` for all Showco
status and one-request control operations. Remove Showco's private Recs GUI IPC
client and its runtime dependence on Recs daemon metadata and `gui_protocol`
request/response classes.

Do not execute the `recs control` command from the web service. It is a thin
command-line client over `reccy.protocol.rpc`; starting a Python process for
every status refresh would be slow and would create a second implementation
boundary. Showco should call the same public RPC commands directly and validate
their documented JSON results at its own adapter boundary.

Keep the public Recs event endpoint for waveforms. `recs control` intentionally
does not expose long-lived waveform subscriptions.

## Current State

Showco currently combines three Recs interfaces:

- `~/.local/state/recs/status.json` supplies primary recording status, rows,
  errors, and service freshness.
- Public Reccy RPC supplies `status_snapshot`, mutable settings, and waveform
  subscription control.
- Private GUI IPC supplies calibration, track naming and layout, generic
  actions, and shutdown.

The public API now covers every one-request operation Showco uses:

- `status_snapshot`, `disk_status`, `list_devices`, and `capabilities`;
- `mutable_attributes`, `get_cfg`, and `set_cfg`;
- `get_track_names`, `set_track_names`, `set_tracks`, `set_noise_floor`, and
  `set_key_label`;
- `calibrate`, `mark`, `card_replace`, `pause_recording`, `resume_recording`,
  and `reload_profiles`;
- `subscribe_waveforms`, `unsubscribe_waveforms`, and `shutdown`.

The CLI does not expose every command above, but they are all part of the same
documented RPC protocol. Showco must use protocol command names, parameter
shapes, and success results rather than the CLI's human-facing subcommand and
argument spelling.

## Shared Control Client

Add a small `RecsControlClient` responsible only for one public RPC request:

- Resolve the endpoint with `recs.daemon.paths.external_control_endpoint()`.
- Construct `rpc.Client` with role `showco` for each request, as required by
  the one-request-per-connection protocol.
- Accept an explicit timeout so cached status polling can retain its current
  250 ms bound while operator actions use the protocol's six-second bound.
- Serialize requests with one lock. Recs permits only one outstanding control
  request, and Showco's status sampler, browser actions, and waveform bridge
  otherwise can contend with each other.
- Return the decoded JSON value and let the caller validate command-specific
  response structure.
- Allow `ConnectionError`, `OSError`, `TimeoutError`, `ValidationError`, and
  `ValueError` to be translated at the relevant Showco adapter boundary, where
  status failures and action failures need different behavior.

Inject this client into `RecsClient`, `RecsSnapshotClient`, and
`WaveformBridge`. Remove their separate `rpc.Client` construction paths. The
event listener remains independent because it is a long-lived subscription,
not a control request.

## Status Migration

Make cached `status_snapshot` the sole runtime source for `RecsStatus`.
Validate and map:

- `rows` into channel levels and the existing total row fields;
- `errors` into `ErrorRecord` values;
- `recording.paused` into an explicit `paused` field;
- `disk`, `midi`, and `osc` through their existing parsers.

A successful snapshot means the Recs control service is connected and
recording is active unless `recording.paused` is true. Update the Recording
card to say `paused` explicitly rather than treating a paused daemon as stopped
or actively recording.

Preserve the last valid snapshot when a later request fails, but mark the Recs
service stale or offline and show the current diagnostic. Do not label retained
rows, errors, or disk information as current. A malformed successful response
is an error, not an empty healthy snapshot.

Remove runtime parsing of `status.json`, `stale_after_seconds`, daemon metadata,
GUI endpoint selection, and the unused `RecsStatus.client_count` and
`ServiceStatus.updated_at` fields. Keep `status_changes_command()` and
`status_failure_summary()` for provisioning verification: that separate remote
check deliberately verifies that the daemon's status publisher is advancing
and is not part of the web adapter.

The one-second cache remains authoritative for both browser status and the
performance sampler, so this migration must not double the RPC rate.

## Action Migration

Replace each private `_send_request()` operation with a public control call:

1. Calibration calls `calibrate` and validates the `calibrated` object before
   returning the existing success message.
2. Track-name editing calls `get_track_names`, applies the existing locked
   read-modify-write operation, then requires `set_track_names` to return
   `"ok"`.
3. Stereo editing calls `set_tracks` with JSON lists and requires `"ok"`.
4. Mutable attributes continue to use `mutable_attributes`, `get_cfg`, and
   `set_cfg`, but move onto the shared client.
5. Actions in `RECS_ACTIONS` call their mapped public command directly. Validate
   documented data responses for status, disk, devices, capabilities,
   calibration, and card replacement; require `"ok"` for mutation-only
   commands.
6. Shutdown calls public `shutdown` and requires `"ok"` before reporting that
   shutdown was requested.

Keep current user-visible success and failure messages where their meaning is
still accurate. Include the protocol command in transport and validation
failures so the Actions page and Showco log identify the failed operation.

Remove `_send_request()`, `_metadata()`, GUI hello and response parsing,
`DaemonMetadata`, `reccy.protocol.ipc`, and private `gui_protocol` imports after
the final caller is migrated. Do not retain private IPC as a fallback; one
canonical path is the purpose of this change.

## Waveforms

Use the shared control client for `subscribe_waveforms`. On bridge shutdown,
send `unsubscribe_waveforms` before closing the event client when Recs is still
reachable. Keep the existing reconnect delay, bounded event history, malformed
event handling, and SSE resynchronization behavior unchanged.

Waveform event payloads and endpoint discovery still come from the installed
Recs package. Removing Showco's direct Recs dependency is not part of this
migration.

## Tests

Replace private GUI socket fixtures with an injected fake `RecsControlClient`.
Add focused tests for:

1. Full `status_snapshot` mapping, including totals, channels, errors, paused
   state, disk, MIDI, and OSC.
2. A transport failure preserving the previous snapshot while changing service
   health and identifying the data as stale.
3. Malformed top-level and nested snapshot fields producing diagnostics rather
   than healthy empty data.
4. The one-second cache serving browser and performance requests with one RPC
   call.
5. Shared serialization of snapshot, action, and waveform control requests.
6. Calibration response validation.
7. Atomic track-name read-modify-write through `get_track_names` and
   `set_track_names`.
8. Stereo track payloads and `set_tracks` success validation.
9. Mutable get/set response validation through the shared client.
10. Every `RECS_ACTIONS` mapping sending the documented command and parameters
    and rejecting an unexpected success value.
11. Public shutdown success and failure behavior.
12. Waveform subscribe, reconnect, unsubscribe, and event behavior remaining
    intact.
13. Absence of private GUI metadata, handshake, and socket code after the
    migration.
14. Existing Health, Actions, Channels, Errors, monitoring, and rehearsal
    behavior remaining unchanged except for explicit paused status.

Run the full Showco test suite, Ruff, formatting, Ty, pyupgrade, JavaScript
syntax validation, lock validation, and `git diff --check`. Automated tests do
not prove behavior against a running recorder; after deployment, verify status,
calibrate, mark, pause/resume, track edits, waveform display, and shutdown on
the target.

## Documentation And Delivery

Update `doc/architecture.md` and `doc/recs-protocol.md` to describe one public
Recs RPC control path plus the event subscription path. Remove descriptions of
the private GUI IPC integration and status-file runtime reads, while retaining
the separate provisioning status-progress check.

Use a separate dependency commit to refresh Showco's Recs lock entry to a Recs
commit containing the documented `recs control` contract. Put the Showco code,
tests, and documentation migration in a following commit so dependency changes
remain isolated as required by the repository.

## Acceptance Criteria

- Showco uses no private Recs GUI IPC or daemon metadata at runtime.
- All status and one-request controls use the documented public RPC contract
  behind `recs control`.
- Showco does not spawn `recs control` subprocesses.
- Status polling, performance sampling, actions, and waveform subscription do
  not contend for Recs' single active control request.
- Failed status requests preserve but clearly mark the last valid data.
- Paused recording is displayed explicitly.
- Waveforms continue through the public event endpoint.
- Provisioning's independent status-progress verification remains intact.
- No fallback or duplicate control implementation remains.

## Additional work beyond the prompt

None.
