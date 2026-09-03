# Showco Problems And Reliability Plan

## Purpose

Audit Showco as it exists now, then address the problems in priority order. The
goal is an operator-facing control surface that reports what has actually
happened, remains responsive when a dependency fails, and has a small enough
implementation to reason about during a show.

This plan distinguishes confirmed defects from physical acceptance work. It
does not propose a new web framework, a redesign of the Actions page, or a
second implementation of Recs, Twitcho, or Lyte control.

## Confirmed Problems

### P0: Lyte Test Can Report Success Without Any Light Output

`Test lights` currently reports success once Lyte accepts a `test` RPC and
returns `{"state": "queued"}`. That only proves the daemon accepted work. It
does not prove that the test ran, that frames reached the controller, or that
the controller configuration matches the fixture.

The observed target log demonstrates the failure mode: Lyte discovered a
controller with 250 LEDs while its configuration expected 200 LEDs, then
refused to send frames. Showco still reported that the test was queued, leaving
the Actions page with no explanation for the lack of flashing.

1. Add a small Lyte client adapter, parallel to `TwitchoClient`, which reads
   Lyte's public daemon status and sends its `test` request.
2. Add the relevant Lyte lifecycle and output fields to `ShowStatus`: daemon
   state, current output error, target identity, frame-send progress, and
   active or recently completed test state when provided by Lyte.
3. Render Lyte status and its error unconditionally in Health. A disabled Lyte
   service must have an explicit disabled state, not disappear.
4. Keep the action result precise: say that a test was queued, never that it
   flashed. The subsequent Health refresh must show the test/output outcome and
   expose errors such as an LED-count mismatch.
5. Move `services.test_lyte_lights` into that adapter so there is one Lyte RPC
   boundary, with focused tests for offline, malformed, queued, active,
   completed, and output-error status responses.
6. Correct the Lyte fixture's configured LED count separately, then perform a
   physical flash acceptance test. This configuration correction belongs to
   Lyte's deployment configuration, not to a Showco fallback.

### P0: Waveform Event Streams Bypass The HTTP Concurrency Limit

`ShowcoHandler.do_GET` sends `/waveforms` directly to `_waveforms` before it
acquires `ShowcoServer.request_slots`. Each EventSource connection therefore
keeps an unbounded `ThreadingHTTPServer` request thread alive. Enough browser
tabs, reconnect loops, or stalled clients can exhaust threads while ordinary
Health and Actions requests have a nominal limit of eight.

1. Give waveform streams an explicit, small connection limit, independent of
   short-lived request slots. Reject excess streams with `503` before sending
   event-stream headers.
2. Release that slot on every disconnect and every setup or write failure.
3. Do not count an EventSource as a normal request for its entire lifetime,
   because doing so would allow a few waveform tabs to starve all controls.
4. Add handler-level tests for the limit, `503`, disconnect cleanup, and a
   normal page request while the waveform limit is full.

### P0: Some Malformed Or Failed Actions Can Still Produce An Empty Response

The recent unsupported Recs action exposed this class of problem. Recs now
turns its validation error into an `ActionResult`, but `_form()` can still
raise `UnicodeDecodeError`, and `run_action()` relies on each integration to
avoid unexpected exceptions. Those exceptions bypass both the redirect and
the action log.

1. Treat invalid UTF-8 and malformed form data as `400 Bad Request`, while
   retaining `413` only for invalid or oversized content length.
2. Make each integration action boundary return an `ActionResult` for its
   documented transport and response-validation failures. Do not use an
   indiscriminate `except Exception` to hide programming bugs.
3. Add one narrow handler safeguard for known request/model failures that logs
   the action and returns a well-formed error result instead of dropping the
   connection.
4. Test invalid content length, invalid UTF-8, malformed JSON attributes,
   unsupported Recs commands, offline RPC endpoints, and an invalid RPC reply.
5. Confirm that every failed action is visible in the recent action history and
   persistent Showco log with a concise reason.

## Correctness And Operational Truthfulness

### Recs Status Must Not Make The Entire UI Depend On A Second RPC

`RecsClient.status()` reads the status file, then synchronously calls
`status_snapshot` to obtain OSC and MIDI details. A slow or unavailable GUI
socket can therefore delay every `/status`, Channels, Health, and Errors page
request even when the durable Recs status file is current. The result from that
second call is also not represented as an independent diagnostic.

1. Define the maximum acceptable status-page latency and make the external
   snapshot fetch bounded to it.
2. Preserve status-file data when the snapshot is unavailable, and include a
   clear, separately named snapshot diagnostic rather than turning a healthy
   recorder into an unexplained failure.
3. Cache only the snapshot data for its short freshness interval, not the
   whole Recs health status. A new file-based recorder error must remain
   visible immediately.
4. Test a timeout, an RPC error, invalid snapshot data, and recovery without a
   server restart.

### Generalize The Incorrect Singular X18 Status Model

Recs now owns generic OSC recording, but Showco models the data as `x18` in
both `RecsStatus` and `ShowStatus`, and `_x18_status()` selects the node named
`x18`. This is a stale name and will either hide another OSC recorder or force
new device-specific paths as mixers expand.

1. Replace the singular `x18` status with a list of named OSC recorder
   statuses returned by Recs.
2. Render those statuses by name in Health, including disabled, running, log
   location/size where appropriate, and errors.
3. Remove the unused `ShowcoApp.x18_status` constructor dependency and its
   server wiring. `ShowcoApp.status()` already ignores it.
4. Update fixtures and tests for zero, one, and multiple OSC recorders.
5. Keep mixer reachability separate from OSC-recording state. A reachable
   mixer and a running recorder are different facts.

### Validate Mixer Probes Against Actual Device Semantics

`MixerMonitor.udp_status()` uses `/xremote` as both a keepalive/probe and an
expectation that a UDP byte will be returned. X18 uses `/xremote` to maintain
its subscription, so treating lack of an immediate reply as reachability
failure may be wrong. This must be verified before changing it, rather than
guessing from a generic OSC convention.

1. Capture the expected X18 traffic while the recorder is active and compare it
   with the current probe behavior.
2. Decide explicitly whether the health signal is a successful send, a reply,
   a separate query, or recorder feedback seen by Recs.
3. Change the probe only after that evidence, and add packet-level unit tests
   for the chosen semantics.
4. Preserve the current long-lived `waiting` state for hardware that may join
   much later. Do not convert an absent Flow 8 into an error solely because
   time has passed.

### Make RPC Result Validation Consistent

`TwitchoClient.action()` treats every dictionary reply as success. Its current
RPC client raises transport errors, but a syntactically valid dictionary can
still represent an unsupported or failed command according to Twitcho's
protocol. Lyte and Recs have separate hand-written response checks.

1. Document the successful result shapes for Recs, Twitcho, and Lyte actions.
2. Validate each adapter's declared successful shapes and turn unexpected
   replies into `ActionResult(ok=False, ...)` with the service and command in
   the message.
3. Avoid a generic cross-service result abstraction unless their protocols are
   genuinely identical. The adapters are the appropriate translation boundary.
4. Add one focused invalid-reply test per adapter.

## Waveform Completeness

### Recover From Invalid Events And Slow Clients

`WaveformBridge` has bounded history, but a client whose cursor has fallen
behind the retained event deque receives no explicit resynchronization. Also,
validation failures in its asynchronous Reccy event callback need an explicit
failure/reconnect policy; otherwise waveform delivery can silently stop.

1. Extend the bridge cursor contract to report when history was missed.
2. On a missed cursor, send a fresh complete layout and current batches before
   incremental events, with an explicit resynchronization marker if the browser
   needs one.
3. Handle invalid waveform events as a logged protocol error and re-establish
   the subscription according to a bounded, testable lifecycle.
4. Ensure `ShowcoServer.server_close()` stops the bridge and waits only a
   bounded amount for its worker to exit.
5. Test event eviction, reconnect, malformed event input, bridge shutdown, and
   browser-side replacement of a layout after resynchronization.
6. Run the existing physical waveform acceptance sequence: sustained source,
   quiet source, reconnect, page navigation, and simultaneous Health updates.
   Judge smoothness from the visual result, not from event count alone.

## Usability, Errors, And Documentation

### Keep Every Important State Visible

1. Continue rendering Errors even when Recs has none, with an explicit empty
   state. Verify the `/errors` page and Health error summary use the same Recs
   error source and cannot diverge.
2. Add Lyte output state to the same visible-health convention rather than
   requiring users to infer it from a button click or a separate log command.
3. Keep action history useful: include service, command, timestamp, outcome,
   and the bounded error detail needed to diagnose a failure. Do not expand it
   into a redesign of the Actions page.
4. Review status-script error rendering with the new fields so a refresh never
   hides an existing error section or replaces a diagnostic with blank content.

### Correct Stale Documentation Before It Causes Another Invalid Action

Several documents describe behavior that current code no longer has:

1. Remove `start_recording` and `stop_recording` from `doc/recs-protocol.md`.
   The current Recs GUI protocol supports pause/resume rather than those
   commands, and stale documentation directly led to the removed controls.
2. Update `doc/architecture.md` to describe Recs-owned OSC recording, multiple
   mixer monitoring, the actual file-log policy, and the current `showco go`
   deployment workflow.
3. Cross-check command names, ports, and service ownership against the code,
   not historical notes.
4. Mark physical claims separately from code facts. In particular, retain the
   unverified recording-persistence acceptance test in `doc/handover.md` until
   a real removable-disk recording has been inspected after stopping.

## Maintainability Cleanup After Behavior Is Covered

### Consolidate Configuration Loading

`update.py`, provisioning, network configuration, and Twitcho authentication
each manually compose TOML values in slightly different contexts. Some callers
need command-line overrides and some only need the default configuration, but
the underlying read-and-merge operation should have one named home.

1. Add a narrowly named configuration helper for loading and merging a config
   file with its secrets file.
2. Keep option-specific validation in the calling command, where the paths and
   overrides are known.
3. Migrate the repeated default-loading sites one at a time with existing test
   seams, including the target update path.
4. Do not merge unrelated card-image configuration, which intentionally reads
   a different input shape.

### Split Only The Oversized Operational Modules At Existing Boundaries

`update.py` combines local repository validation/pushes, target update shell
construction, service restarts, and reporting. `recs.py` combines file status,
GUI actions, external snapshot parsing, and waveform subscription. Their size
makes a small operational change harder to review, but a wholesale rewrite
would be riskier than the current code.

1. After the correctness work above, extract local repository preparation from
   `update.py` and retain its current public command functions and tests.
2. Extract Recs snapshot and OSC-status parsing from GUI action transport only
   if the generalized OSC-status work leaves a clear, tested boundary.
3. Retain dependency injection through paths, sockets, and command runners.
   Do not introduce a service container, framework, or generic command engine.
4. Remove names and constructor arguments made obsolete by the extraction,
   especially the unused `x18_status` hook, rather than retaining compatibility
   shims.

## Reliability Acceptance Matrix

Automated tests should cover protocol parsing, bounded resources, response
status, recovery, and visible errors. They cannot establish the following:

1. Recs writes complete audio to a removable disk and the files remain present
   after recording stops and the volume is remounted.
2. X18 USB audio, Flow 8 audio/MIDI, and OSC recording transition correctly as
   devices arrive, disconnect, and return at different times.
3. The tablet can remain on the private network while it uses Health, Actions,
   and waveform pages for an extended session.
4. A Lyte test visibly flashes the intended controller and Health reports its
   actual output failure if it cannot.
5. `showco go` reports completion only after target service verification and
   leaves enough file-log evidence to diagnose a failed deployment.

Record the date, deployed revisions, device presence, and result for each
physical run in the handover rather than treating a passing unit suite as
hardware acceptance.

## Suggested Delivery Order

1. Lyte truthfulness and the malformed-action response path.
2. Waveform connection limit and cursor recovery.
3. Recs snapshot latency/diagnostics and named OSC status model.
4. Documentation corrections and the physical acceptance matrix.
5. Configuration-loading consolidation and only then small module extractions.

Each step should be independently committed with focused tests. Run the full
Showco suite before any deployment, and keep target/hardware validation as a
separate recorded step.

## Additional Work Beyond The Prompt

None.
