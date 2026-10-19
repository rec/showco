# showCo code review

Static review of checkout `5125349` on 2026-10-19. This replaces the previous
issue list. No application, service, deployment, hardware flow, or test suite was
run. Findings follow the current source paths; they are not failures observed on
the Pi. P1 means potentially disruptive or misleading during operation, P2 means
an ordinary correctness or recovery problem, and P3 means a maintainability
concern. Line numbers refer to the reviewed checkout. Paths below are relative
to `showco/` unless prefixed with `site/` or `test/`.

## Additional work beyond the prompt

None. Resolved findings record their implementation and verification below;
suggested work for the remaining findings has not been implemented.

## Operational correctness and recovery

### 1. Resolved: Autosquash excludes the commit that needs the fixup

Fixed by rebasing from the parent of the oldest matching fixup target, including
`--root` when the target is the first commit. Regression coverage uses real
temporary Git histories with a separated fixup and both root/non-root targets.
Validation: focused tests, full pytest suite, Ruff, ty, pyupgrade, and diff checks.

`deployment/publication.py:172`, `autosquash_program()` and
`oldest_fixup_commit()`, choose the parent of the oldest **fixup**, rather than
its target, as the rebase boundary. In history `A (target), B, C (fixup! A)`,
rebasing `C^` excludes A, so Git cannot squash C into A. Preflight checks that A
appears in the log but not that it is inside the rebase range. Publication can
report a successful autosquash while leaving fixups outstanding.
`test/test_local_update.py:test_provisioning_update_autosquashes_from_oldest_fixup_parent`
currently asserts the incorrect boundary using a fake runner.

Choose a range containing the affected target commits. Verify with a disposable
Git history where a target and fixup are separated by ordinary commits.

### 2. Resolved: Closing recovery can claim Record mode with recording paused

Abandoning closing now uses the existing Resume show transition: restore the
room, start a new recording session, resume recording, and restart broadcasting.
Closing evidence is cleared only after success; failed recording resumption
keeps recovery evidence and does not start broadcasting. This follows the user's
explicit recovery decision. Validation: music regressions, full pytest suite,
Ruff, ty, pyupgrade, and diff checks.

`runtime/music.py:409`, `MusicController.recover_closing(False)`, restores room
routing, clears closing evidence, and saves mode `record`. It does not resume
recs or restart the stream. A reachable trigger is failure in
`_finish_closing()` after `_pause_recording()` succeeds. The cancellation message
asks the operator to check recs, but a subsequent `record()` returns “Already
in Record mode” without repairing anything (`runtime/music.py:161`). Deleting
closing evidence also removes the closing-recovery controls.

Preserve an unresolved state until recording is reconciled, or make explicit
recovery perform and verify the intended transition. Cover failure after pausing
recs, then cancellation and a Record request.

### 3. Resolved: Failure to save closing failure can kill its only observer

Closing failure handling now retains the failed operation in memory, publishes
fault/failed status and the persistence error, and logs the failure even when
storage rejects the failure record. Regression coverage exercises stream failure
and finalization failure with an unwritable failure record. Validation: music
tests, full pytest suite, Ruff, ty, pyupgrade, and diff checks.

`runtime/music.py:298,376`, `_observe_closing()` and `_fail_closing()`: after
catching an operational or persistence error, the observer calls `_fail_closing()`.
That performs another unguarded `_save_closing()` before assigning mode `fault`
and its error message. Full or unwritable state storage makes this second write
raise out of the thread. The controller can remain in closing mode with no
observer, no automatic restart, and no published finalization-failure message.
The same problem occurs when streamO reports failure and saving it fails.

Publish the in-memory fault even if persistence fails, and expose observer death.
Check failed saves during both finalization and failure handling.

### 4. P1: Closing cleanup races an observer that has not stopped

`runtime/music.py:613`, `MusicController.close()`, joins the observer for five
seconds, then proceeds without checking `is_alive()`. Unlike `record(restart=True)`
and `recover_closing()`, it stops the player and disables returns even if the
observer is still in `_finish_closing()`. That method does not check cancellation
between its RPC, routing, and playback steps. It can subsequently start teardown
playback and re-enable returns after cleanup has muted them.

End ownership of finalization before cleanup, or implement cancellation boundaries
inside the transition. Check close during a blocked finalization step with fakes.

### 5. P1: Recording controls bypass show-transition serialization

`runtime/app.py:274`, `ShowcoApp.run_action()`, exempts pause/resume from
`action_lock`. They can interleave with `music-record`, setup, or teardown and
undo the recording state those sequences just established. The cable-test check
is also separate from the RPC: resume can pass the check before
`start_cable_test()` marks the test running, then resume recs during the test.
`RecsControlClient.lock` serializes individual RPCs, not checks and transitions.

Define recording ownership across transitions and cable tests while preserving
intended pause/resume responsiveness. Check interleavings with barriers, including
resume crossing cable-test startup and pause crossing Record mode.

### 6. P1: Disabling streamO leaves an existing broadcaster running

`provision/templates/services.sh:161`, `install_streamo_service()`, prints that
streamO is disabled and returns when `STREAMO_ENABLED` is false. It never stops
or disables an existing unit. showCo then hides streamO while an existing
broadcast can continue. `install_lyte_service()` explicitly stops and masks its
disabled service, so these flags have materially different meanings.

Reconcile the existing streamO service with the disabled configuration. Check
an enabled-to-disabled transition with an existing active unit using fakes.

### 7. P2: Monitoring-history write failures are omitted from live status

`runtime/monitoring.py:171`, `PerformanceMonitor._write()`, retains history-write
failures in `storage_error`. `ShowcoApp.status()` at `runtime/app.py:145` includes
`observation_error` but never this field in `monitoring_error`. Live values can
look healthy while no history is retained, including on a full filesystem. The
operator only gets a log warning.

Include the storage failure in published status. Check an unwritable history
directory while system and recs sampling remain successful.

### 8. P2: Music and cable-test failures never become active faults

`runtime/incidents.py:130`, `fault_details()`, considers readiness and audio-write
progress only. `ShowcoApp.status()` observes incidents before adding music and
cable-test state to the response. A failed player, failed cable test, or faulted
closing transition can coexist with the global “No active observed faults”
banner. Detailed errors on another page do not enter the shared incident history.

Feed relevant terminal failures into fault observation, with explicit recovery
conditions. Check a music fault while all monitored services remain healthy.

### 9. P2: Workflow polls can occupy all health slots behind a long action

`runtime/server.py:69` takes `action_lock` for `/workflow-status`; actions hold
that lock across fades, RPCs, restarts, and filesystem operations. Workflow and
ordinary status requests share two `status_slots`. Two workflow requests can
occupy both while waiting for an action, causing ordinary `/status` polls to get
503 even though those polls do not require `action_lock`. Browser aborts do not
cancel the server threads waiting for the lock.

Avoid reserving all health capacity for requests waiting on actions. Check two
workflow polls during a blocked action, then an independent `/status` request.

### 10. P2: Provisioning subprocess timeouts can hang in output cleanup

`provision/ssh.py:305`, `run_command()`, starts a new session, kills only the
direct process on timeout or interruption, then unconditionally joins the output
reader. If a descendant inherited stdout and remains alive, the pipe stays open
and `reader.join()` has no deadline. Commands spawning helpers, including rsync's
SSH transport, can therefore exceed the configured timeout indefinitely.

Terminate the owned process group and bound output draining. Check a child that
leaves a descendant holding its pipe open, without any remote connection.

### 11. P2: CLI diagnostics grow for the entire web-server lifetime

`cli.py:119`, `main()`, redirects output into a temporary file and attaches a
root log handler to it for the whole command. For `showco run`, log records
accumulate until server exit, without a size limit or rotation. This duplicates
normal logging and can fill temporary storage during a long run or repeated
failure, worsening the original incident.

Bound retained diagnostics or use the existing rotated logs. Check retained size
under repeated output without starting the application.

### 12. P2: Successful helpers erase earlier failure reports

`cli.py:119`, `main()`, unlinks the shared `showco-errors.txt` at entry and again
after success. All subcommands use the same path. A successful health check,
log fetch, or service command deletes earlier diagnostics, and overlapping
processes can delete each other's evidence. This happens without the operator
using the explicit `clear-logs` command.

Separate command-local capture from retained failure history. Check failure
followed by a successful unrelated invocation.

### 13. P2: Non-finite recs totals can break the status response

`runtime/recs.py:32`, `RecsClient.status()`, uses `_int()` for `file_count`.
`_int()` accepts floats and calls `int(value)`, which raises on NaN or infinity.
`runtime/recs_snapshot.py:snapshot_status()` requires rows to be dictionaries
but does not validate their numeric totals, so this payload passes the snapshot
boundary. The exception escapes the HTTP status path instead of becoming an
invalid-snapshot diagnostic. The other floating-point row fields already use
`recs_channels._float()`, which rejects non-finite values; file counts do not.

Validate totals at the snapshot boundary. Check non-finite counts and metrics.

### 14. P2: Malformed channels silently change calibration scope

`runtime/app.py:530`, `_channel_numbers()`, returns an empty list for malformed
integers. `_dispatch_action()` treats empty device and channels as global
calibration. For example, `recs-calibrate` with `channels=bad` and no device runs
global calibration instead of rejecting the selection. With a device, it replaces
the parse error with a misleading missing-channels error.

Reject malformed lists; reserve global calibration for an absent selection.
Check that malformed selections never call calibration.

## Browser and waveform behavior

### 15. P2: Transport actions have no browser timeout

`site/playback-script.js:14` uses raw `fetch()` without an abort deadline. A
stalled response leaves the transport button `aria-busy` and disabled indefinitely,
even while status polling succeeds. This also affects the Twitch transport.
`showAction()` already implements a bounded request and an unknown-outcome message,
but this path bypasses it.

Reuse the existing action request behavior. Check a request that never resolves.

### 16. P2: Empty service status replies are classified as healthy

`streamo/client.py:50`, `StreamoClient.status()`, accepts an empty dictionary,
assigns stream state `unknown`, and publishes service state `connected` because
`_health_error()` only checks active states and explicit errors. Similarly,
`runtime/lyte.py:LyteClient.status()` accepts an empty dictionary as connected
while setting `running=False`. Readiness uses service connectivity, so these
unusable replies pass the service checks rather than reporting invalid status.

Require the existing status contract's essential fields before declaring health.
Check empty replies and missing state/running fields with fake RPC clients.

### 17. P2: Concurrent waveform arrivals can be delivered twice

`runtime/server.py:120`, `_waveforms()`, gets `updated` from `wait_for_change()`,
then separately calls `events_since(changed)`. Events arriving between those
reads are sent too, but the cursor is set to the older `updated` value. The next
iteration sends the tail again. The browser appends samples without deduplicating
these sequence numbers.

Advance to the last emitted event or read events and cursor together. Check
arrival between the two reads.

### 18. P2: Waveform reconnection retains removed sources indefinitely

`runtime/waveforms.py`, `WaveformBridge._run()`, `_layout()`, and `snapshot()`,
never clear layouts or batches on disconnection or a new subscription. A layout
only replaces samples for the same source. After recs restarts with a source
removed or renamed, its old layout and samples remain in every new browser
snapshot. Storage is unbounded across distinct source names even though each
deque is bounded.

Define subscription-generation reset behavior and notify browsers. Check
reconnection from sources A/B to A only.

### 19. P2: Performance disk values lose their stale-data warning

`site/performance.js:37`, `renderPerformance()`, renders `recs.disk` whenever
present, ignoring `disk_error` and `snapshot_error`. Recs intentionally retains
the last disk snapshot after transport failure. The performance page keeps
showing old free space and remaining minutes without marking those figures stale.
`site/status-script.js:updatePerformance()` handles these errors, so the two
presentations disagree for the same response.

Label retained values stale on the performance page. Check a successful disk
sample followed by transport failure.

## Configuration, authentication, and reporting

### 20. P2: Removing a managed movie leaves the old movie configured

`provision/media.py:246`, `stream_media_configuration()`, replaces `video` only
when `movie` is non-null. Removing a saved movie from `media.toml` supplies no
movie argument and retains the old managed `video` path in streamO. Photos have
an explicit removal branch; movies do not. Streaming can keep using an asset
removed from the desired configuration.

Reconcile removal of managed movie paths without clearing independently managed
video settings. Check configuration with a movie followed by no movie selection.

### 21. P2: OAuth token files use ambient filesystem permissions

`streamo/auth.py:103`, `exchange_code()`, uses plain `write_text()` for the full
OAuth response, access token, refresh token, and secrets update. New token files
and their directory get ambient umask permissions; a 022 umask makes them
readable by other local users. Existing permissive files are not tightened either.

Create credential files and directories privately and preserve that policy on
replacement. Check modes under a permissive test umask with fake token responses,
without printing credentials.

### 22. P2: OAuth callback state is generated but never checked

`streamo/auth.py:81,184`, `authorize_url()` saves random state, but
`callback_code()` extracts only `code`, and `exchange_code()` never compares
returned state with the saved value. A callback from a different authorization
attempt is accepted. A denial callback without `code` raises `KeyError` instead
of explaining the authorization error.

Validate callback URLs against pending state and report denials/missing fields.
Make the intended raw-code policy explicit. Check mismatch, denial, and valid
callbacks with fake HTTP.

### 23. P2: OAuth HTTP requests have no timeout

`streamo/auth.py:192`, `request_http()`, calls `urlopen()` without a timeout.
Exchange and validation can block on stalled connections indefinitely. Expected
transport failures also escape without the command's own diagnostic context.

Give requests a finite deadline and useful transport-error messages. Verify
with a fake opener; no real authorization request is needed.

### 24. P2: TOML updates corrupt valid alternative formatting

`streamo/auth.py:200`, `write_toml_value()`, recognizes a key only if the line
starts with exactly `name =`, and a table only if its whole line equals
`[section]`. Valid `access_token="old"`, indented assignments, or
`[stream] # comment` are missed. Appending a duplicate key/table makes previously
valid TOML unreadable. `streamo/config.py` also uses this writer for credentials.

Handle supported TOML syntax consistently and validate before replacing the
original. Check spacing and comments, not just the writer's own output.

### 25. P2: Broadcast-duration arithmetic can join unrelated encoder attempts

`deployment/report_scan.py:log_evidence()` sorts progress records by time and
sums every nondecreasing counter difference. It detects a restart only when the
counter decreases. If a new encoder's first retained counter exceeds the previous
encoder's last retained counter, their difference is counted despite crossing
attempts. With missing/rotated logs this can overcount, so the reported “at least”
duration is not reliably a lower bound.

Associate progress with encoder attempts or omit uncertain boundary differences.
Check two attempts whose retained counters increase across the restart.

## API shape, duplication, and module boundaries

### 26. P3: Status names imply stronger guarantees than they provide

`runtime/recs.py:32` sets `RecsStatus.recording` whenever a snapshot exists and
the service is connected, regardless of row contents, enabled inputs, or writes.
`ServiceStatus.fresh` in `runtime/models.py` is a connectivity comparison, not
an age check. Callers often correctly combine these with pause/progress checks,
but a bare boolean appears to mean active capture or recent observation. This
is confusing next to `snapshot_available` and `recording_progress`.

Document exact guarantees and align names/call sites around one meaning rather
than adding parallel flags with the same semantics.

### 27. P3: Action dispatch and music lifecycle concentrate too many contracts

`runtime/app.py` is 629 lines. `_dispatch_action()` combines performance
protection, cable ownership, workflows, recs configuration, musicians, service
lifecycle, lighting, and music. `runtime/music.py` is 678 lines and combines
configuration, persistence, synchronous transitions, an observer thread, closing
recovery, and shutdown. Ownership rules are spread across string sets, locks,
and controller methods; findings 2-5 demonstrate the resulting inconsistencies.
The problem is mixed responsibility rather than line count alone.

Make the existing ownership/transition contracts explicit when fixing these
paths. A wider architectural extraction needs separate scope; splitting files
alone will not fix behavior.

### 28. P3: Workflow controllers repeat file-persistence mechanics

`SetListController.save()` (`runtime/setlist.py:48`),
`LightingController.save()` (`runtime/lighting.py:38`),
`Soundcheck.save()` (`runtime/soundcheck.py:45`), and
`Recovery.save()` (`runtime/recovery.py:32`) repeat directory creation, fixed
`.tmp` files, JSON writing, replacement, and in-memory assignment. Performance
lock and incident storage have related copies; music uses the existing
`reccy.runtime.files.atomic_output`. Persistence fixes can drift between copies.
Soundcheck's pending-save behavior is distinct and should remain explicit.

Consider reusing the existing atomic-file primitive for mechanical writes,
without introducing a generic controller hierarchy.

### 29. P3: A one-use module and trivial auth helpers add indirection

`runtime/input_check.py` contains a 17-line channel presentation function called
in production only by `ShowcoApp.status()`. It does not own a separate lifecycle
or adapter. In `streamo/auth.py`, `auth_options()` just constructs `AuthOptions`
for three wrappers that immediately unpack it; `provision_dir()` has no caller.
These are specific consolidation candidates, not a reason to merge all small
modules. `workflow_storage.py` and `deployment/repositories.py` have multiple
production consumers and clear shared responsibilities.

### 30. P3: Browser/server presentation and action behavior have parallel copies

`runtime/views.py` and `site/status-script.js` independently format recording,
playback, streaming, lighting, and mixer state and calculate disabled controls.
`site/performance.js` adds another disk presentation, and
`site/playback-script.js` adds another action transport. Initial rendering and
polled rendering must be maintained separately. Findings 15 and 19 are existing
behavioral drift, not just theoretical duplication.

Keep the initial-render/live-update contract explicit and check parity where
it matters. Reuse existing action transport rather than adding another request
implementation or a browser framework.

## Review limits

This source review is not exhaustive execution coverage. Suggested checks describe
verification for eventual fixes; they were not run for this documentation change.
Pi networking, X18 routing, broadcast delivery, and physical audio acceptance
still require the existing hardware plans. Previous lack of physical verification
is not evidence of a newly discovered code defect.
