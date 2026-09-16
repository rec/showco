# Repository issues

Reviewed against `04e1a48` on 2026-09-16. This is a source review, not a hardware acceptance result. No application, services, deployment, or tests were run for this documentation-only task. Private configuration contents were not inspected.

Entries marked **Bug** follow directly from the source. **Risk** describes a failure path whose real-world occurrence still needs verification. **Design** identifies an operator or maintenance concern, rather than necessarily incorrect behavior. Suggested changes are follow-up work, not changes made by this review.

## Highest priority

### 1. Browser status refresh crashes on the old streaming field

**Resolved.** Browser fields and the health element now use `streamo`. An executable JavaScript regression consumes serialized `ShowStatus` for connected and disabled streamO and verifies rendering continues to the end of the refresh. The test requires Node.js.

**Bug.** `site/status-script.js:updateStatus` reads `status.twitcho.service`, `status.twitcho.output_bitrate_kbps`, and other `twitcho` fields. `showco/runtime/models.py:ShowStatus` exposes only `streamo`. Every normal response therefore throws before channel, readiness, incident, input-check, temperature, and mixer updates execute. The empty `.catch(() => {})` hides the failure, leaving much of the page frozen at its initial values.

Use the current response schema throughout the browser. Verify a complete refresh using a real serialized `ShowStatus`, including disabled streamO.

### 2. Cable-test cleanup unconditionally unmutes the main mix

**Resolved.** The main mix state is now saved and restored with the other mixer settings. Tests cover both initial states after success, audio failure, and setup failure.

**Bug.** `showco/x18/cable_test.py:X18TestRouting.__enter__` sets `/lr/mix/on` to zero without saving it. `_restore` always sets it to one, including after setup failure. A mixer that was intentionally muted is unmuted by the test. Existing restoration tests assert this behavior rather than preservation of the original state.

Save and restore the master state just like the other changed settings. Verify both initially muted and initially unmuted cases, including failure during setup.

### 3. Remote updates reset unselected repositories before stopping services

**Resolved.** Remote invocation no longer resets checkouts or synchronizes environments before the target transaction. The target preflights selected repositories and consumers, snapshots revisions, stops affected services, and restores all selected revisions and locked environments on deployment failure before restarting. Recovery failures are reported rather than declared successful. Older targets must install the new updater before these guarantees apply.

**Risk.** `showco/deployment/update.py:remote_update_command` fetches and hard-resets showCo and all four sibling repositories before invoking the selected update operation. Only the showCo worktree receives the optional dirty check. Thus `showco go recs` can replace changes in other target checkouts and move their source while their services remain running. A failure halfway through bootstrap leaves a partially updated installation before the normal service lifecycle even starts.

Make the bootstrap scope and service-stop boundary explicit. Preserve the documented disposable-checkout policy where intended, but do not present repository selection as limiting all target mutations. Verify partial-bootstrap failures.

### 4. Updates erase saved recs settings before preflight checks

**Resolved.** Settings preservation is the default. Explicit clearing applies only when recs is affected and after preflight and service shutdown; failed updates restore the original settings.

**Bug / design.** `showco/provision/provision.py:GoOptions.clear_settings` defaults to true. `showco/deployment/update.py:update_target` clears settings before constructing the selected program list and checking branches. A rejected update can therefore still remove operator configuration; the clearing is not conditional on selecting recs. The documentation warns about the default, but ordinary deployment remains a trap for track names and stereo groups.

At minimum, perform non-mutating preflight checks first and scope clearing to the relevant operation. Reconsider whether deleting saved settings should be the default.

### 5. Reading status can change live lighting, repeatedly

**Resolved.** Per operator decision, light tests run only through the explicit Test lights action. Status and reconnection no longer produce physical lighting output.

**Design / risk.** `showco/runtime/server.py:ShowcoApp._lyte_status` calls the light-test action when lyte becomes connected. Any non-connected status resets the flag, so a transient error followed by recovery triggers another test. Opening a page or polling `/status` can therefore change physical lighting during a performance. `doc/README.md` describes a test “once when Lyte first connects,” which understates the reconnect behavior.

Define whether this belongs at startup, on explicit operator request, or on every reconnect. Keep status polling free of unexpected output changes, or make that behavior explicit in the operational contract.

## Other correctness and reliability issues

### 6. Accepted cable-test ranges can raise an uncaught exception

**Resolved.** Validation rejects ranges without a spare source channel before requesting pause.

**Bug.** `validate_channels` accepts `1-16`, `1-17`, and `1-18`. `CableTester.run` then selects an unused source from channels 1 through 16 with `next(...)`, which raises `StopIteration` for all those ranges. Neither the CLI error handler nor the web action handler catches it. The test has already requested a recording pause by this point.

Validate the spare-source requirement before pausing recs, and report a useful range error or explicitly support a different routing arrangement.

### 7. Cable-test playback and capture have no bounded cleanup

**Resolved.** Playback and capture have finite deadlines. Capture is killed and reaped on failure, with cleanup errors reported without replacing the primary failure. Tests cover missing playback executable and both process timeouts.

**Risk.** `audio_round_trip` launches `arecord`, calls `subprocess.run` for `aplay` without a timeout, then calls `recorder.communicate()` without a timeout. If playback startup raises, the recorder has no `finally` cleanup. If a process hangs, recs remains paused and mixer restoration is delayed indefinitely; a web-triggered test also holds the application action lock.

Bound both operations and ensure child processes are reaped on every exit path. Preserve the original audio error if cleanup also fails.

### 8. Cable-test capture and playback start independently

**Risk.** `audio_round_trip` records for exactly the tone duration but does not synchronize recorder readiness with playback. `analyze` discards only 250 ms at each end. A startup offset beyond that margin can include silence or miss part of the tone, reporting a cable fault caused by process scheduling.

Define and measure the capture timing contract on the Pi. Use sufficient capture pre-roll/tail or align the analyzed steady-state interval to the recorded tone.

### 9. Cable-test routing does not isolate the selected buses

**Risk.** `X18TestRouting` enables the test source on the selected buses, but does not remove other channels' contributions to those buses or disable the source's existing sends to unselected buses. Results can depend on the pre-existing scene: unrelated audio can contaminate measurements, and the test tone can reach additional destinations. Physical AUX output assignments are also assumed rather than configured or checked. The updated documentation dropped the previous output-assignment prerequisite.

Specify the required mixer scene and verify it, or save, isolate, and restore all routing needed for the test. Confirm this with an actual X18 scene containing other active sends.

### 10. “Distorted signal” also means clean audio at an unexpected level

**Bug in reporting.** `CableChannelResult.line` calls every non-silent failure distorted, but `analyze` fails a pure sine solely for a level ratio outside 70–130 percent. This sends an operator looking for distortion when the waveform is clean but quieter or louder. `MINIMUM_SIGNAL_RMS` is a fixed threshold, not a measured noise floor, and the level tolerance is not backed by a recorded hardware calibration in this repository.

Separate waveform distortion, clipping, absent signal, and incorrect level in the result, or state precisely which failures the combined label covers. Validate tolerances against known-good cables on the actual gain configuration.

### 11. OSC queries have an inactivity timeout, not a total deadline

**Resolved.** Queries enforce a monotonic total deadline and report the configured timeout. Unrelated replies cannot extend the deadline.

**Risk.** `X18OscClient._request` loops until it sees the requested path. Unrelated packets can keep arriving and prevent the socket timeout forever. Its error message also reports the global one-second constant even if the constructor received a different timeout.

Use a total query deadline and report the configured timeout. Exercise continuous unrelated replies with a fake transport.

### 12. HTTP concurrency limits do not bound connection threads

**Risk.** `ShowcoServer` inherits `ThreadingHTTPServer`; the eight-request semaphore is acquired only inside `do_GET` or `do_POST`, after a thread already exists and request headers have been read. Connections with incomplete headers bypass the intended admission limit. Accepted POSTs can also block indefinitely in `_form` waiting for their declared body because no socket read deadline is set here.

Apply connection-level admission and finite read deadlines. Clarify that the current limits cover active handlers, not all connections or threads.

### 13. Network clients can perform actions without authentication or origin checks

**Design / risk.** `ShowcoHandler._do_post` accepts form submissions without authentication, a CSRF token, or an Origin check. Reachable clients can pause recording, shut down recs, or run a cable test. An unrelated browser page can attempt ordinary cross-origin form posts where browser network policy permits them; reading the response is not required to cause the action.

Document the trust boundary of the show network and choose protection appropriate to it. Do not rely on response CORS restrictions as action authorization.

### 14. Browser failures retain apparently live information

**Resolved.** Status and playback polling share a five-second abort deadline and visible connection indicator with the last successful update time. Failed status refreshes mark readiness unknown rather than retaining a ready label.

**Bug in observability.** Both `site/status-script.js:updateStatus` and `site/playback-script.js:poll` swallow refresh failures. Fetches have no explicit deadline. A disconnected tablet can show old ready/recording values without a visible connection age, and a hanging request prevents the next poll from being scheduled.

Display a stale/disconnected state and last successful refresh time. Bound each poll and preserve useful error details for diagnosis.

### 15. The browser's lyte display uses a superseded schema

**Resolved.** The browser renders running state, animations, and per-string state/frame counts from the current model. Executable JavaScript tests cover connected and disabled responses.

**Bug.** `site/status-script.js:lyteDetail` reads `daemon_state`, `output_state`, `host`, and `frame_send_count`. `models.LyteStatus` contains `running`, animation fields, and a `strings` mapping instead. Once issue 1 is fixed, connected lighting still displays undefined or missing details.

Render the current lyte model and verify connected, disabled, and per-string failure cases.

### 16. Editing while a track-name save is in flight loses the saved-value boundary

**Resolved.** Save acknowledgments record the submitted name; a subsequent edit remains dirty. A delayed-response JavaScript regression verifies this case.

**Bug.** `site/status-script.js:saveTrackName` submits the current input value, but after the response it assigns `form.dataset.savedTrackName = input.value` again. If the operator changes A to B while the request for A is pending, B is marked saved even though only A reached recs. A later save can skip B and revert can restore the wrong value.

Capture the submitted value and use it when acknowledging the response. Keep later edits dirty.

### 17. Readiness ignores the recording-progress failure displayed beside it

**Design.** `ShowcoApp.status` calculates readiness before calculating `recording_progress` and `input_checks`. `readiness.status` checks service state and recording flags, but not actual progress. “Ready to perform” can coexist with “recorded audio has not advanced.” Silence filtering may make this legitimate, but the UI does not explain that distinction.

Define what ready guarantees, including silence-filtered recording, and make contradictory-looking indicators actionable rather than simply combining all checks.

### 18. Progress and incidents depend on viewers and share unprotected state

**Risk / incomplete idea.** `ProgressMonitor.observe` and `IncidentTimeline.observe` run from `ShowcoApp.status`, not the background performance sampler. Transitions between browser visits are lost. Multiple request threads mutate the same monitors without locks. Progress state is also retained across pauses and decreases in recorded duration, so a new session can initially inherit an old stall timer.

Decide whether these are continuous monitoring features or observations made while someone polls. Serialize updates and reset progress at meaningful session/recording boundaries.

### 19. Input checks report clipping as healthy signal presence

**Design.** `showco/runtime/input_check.py:checks` marks every state except `silent` as okay, including `clipping` produced by `recs.level_state`. It also omits all channels whose `on` flag is false. “Input checks” therefore neither checks all configured inputs nor treats clipping as a fault.

Name this narrowly as signal presence on recording channels, or expand it to the operator checks the heading implies.

### 20. `--autosquash 0` is ignored on the normal update path

**Bug.** `showco/deployment/go.py:run` passes `options.autosquash or 50` to `update_from_provisioning_machine`, turning an explicit zero into 50. The push/sync path correctly distinguishes `None` from zero. A user trying to disable rewriting gets different behavior depending on the selected mode.

Use the same explicit `None` handling for both paths and verify zero separately from omission.

### 21. Diagnostic bundle names collide within one second

**Bug.** `showco/deployment/bundle.py:create_bundle` names the destination using whole-second UTC time and calls `mkdir` without collision handling. Two invocations in one second raise `FileExistsError`; a partial previous attempt in that second has the same effect.

Use unique directory creation and report partial bundle failures clearly.

### 22. Static assets rely on a source-checkout layout

**Packaging risk, not build-verified.** `server.SITE_DIRECTORY` resolves to a top-level `site/` outside the Python package. `pyproject.toml` has no explicit wheel asset mapping. A source checkout works, but an installed artifact needs both correct asset inclusion and a different dependable lookup location. `.gitignore` also ignores `/site`, so new assets can be silently omitted even though the current assets are tracked.

Choose whether installed wheels are supported. If so, verify an isolated wheel installation; otherwise document the checkout requirement. Remove the misleading ignore rule for actual application assets.

## Naming, structure, documentation, and verification

### 23. Names and documentation no longer match the source layout

**Maintenance.** `AGENTS.md` points to moved paths such as `showco/server.py`, `showco/recs.py`, and `showco/update.py`; their current homes are `runtime/` and `deployment/`. The cable test still calls its simultaneous channel analysis `_test_pairs`. The browser retains `twitcho` naming, including identifiers. Public prose inconsistently capitalizes project names instead of lyte, streamO, recs, uFor, reccy, tuney, enge, and showCo.

Update references and public wording to current concepts. Preserve actual lowercase executable/module names where they are required, rather than mechanically changing code identifiers to branding.

### 24. Several files combine too many responsibilities

**Maintenance.** Current sizes are approximately:

| File | Lines | Responsibilities to reconsider |
| --- | ---: | --- |
| `showco/runtime/server.py` | 1,331 | orchestration, actions, HTTP admission, event streaming, HTML, presentation helpers |
| `showco/runtime/recs.py` | 793 | waveform transport/cache, control operations, response validation, presentation conversion |
| `showco/deployment/update.py` | 783 | target sequencing, shell generation, rollback, subprocess policy, service verification |
| `showco/provision/provision_locally.tmpl.sh` | 823 | OS packages, repositories, network configuration, credentials, service setup |
| `site/status-script.js` | 600 | status polling, track editing, attributes, channels, health rendering |
| `test/test_update.py` | 1,575 | multiple deployment and publication concerns |
| `test/test_provision.py` | 1,315 | configuration, generated scripts, provisioning behavior |
| `test/test_server.py` | 1,093 | HTTP, actions, rendering, service behavior |

Split by existing responsibilities when those areas next change, without adding generic frameworks. `test/` has roughly three dozen test modules plus fixtures; that is a navigation concern, but there is no evidence that directory entry count itself causes a performance problem. Production subdirectories are not unusually crowded.

### 25. Tests do not cover the browser/runtime boundary or real cable acquisition

**Verification gap.** `test/test_smoke.py` checks HTTP payloads and HTML strings but does not execute the browser scripts. This allowed issues 1 and 15 to survive. Cable-test tests inject `round_trip`, so they do not establish subprocess cleanup, acquisition timing, or physical routing. The simultaneous-routing test checks one enabled send; the multiple-send test does not inspect routing at capture time. Audio tests use arrays rather than the WAV regression artifacts required by the repository instructions.

Add focused coverage for the identified failure modes, including a full status refresh and all selected sends enabled before capture. Keep hardware acceptance separate from fakes. `doc/handover.md` explicitly records that the exact live installation still lacks a recorded end-to-end acceptance result.

## Suggested order

Fix the broken browser refresh and mixer-state restoration first. Then address deployment mutation boundaries and unbounded operations. Resolve monitoring and naming contracts before broad file splitting, so structural cleanup follows clear behavior rather than preserving ambiguous interfaces.
