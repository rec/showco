# showCo code review

Initial static review of checkout `5125349` on 2026-10-19. This replaces the
previous issue list. That review ran no application, service, deployment, hardware
flow, or test suite. This plan contains outstanding findings only; remove
completed findings rather than retaining resolution notes. The findings are not
failures observed on
the Pi. P1 means potentially disruptive or misleading during operation, P2 means
an ordinary correctness or recovery problem, and P3 means a maintainability
concern. Line numbers refer to the reviewed checkout. Paths below are relative
to `showco/` unless prefixed with `site/` or `test/`.

## Additional work beyond the prompt

None.

## Intended behavior and scope

Cable testing is a separate utility, not an expected runtime workflow. Its
results do not need to become live-show faults.

`showco-errors.txt` intentionally describes exactly the last invocation. Starting
another invocation replaces the previous diagnostic; retaining failure history
across invocations is not a requirement.

## Operational correctness and recovery

## Browser and waveform behavior

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
protection, workflows, recs configuration, musicians, service
lifecycle, lighting, and music. `runtime/music.py` is 678 lines and combines
configuration, persistence, synchronous transitions, an observer thread, closing
recovery, and shutdown. Ownership rules are spread across string sets, locks,
and controller methods, making the transition contracts difficult to follow.
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

This source review is not exhaustive execution coverage. Checks suggested for
the outstanding findings have not been run.
Pi networking, X18 routing, broadcast delivery, and physical audio acceptance
still require the existing hardware plans. Previous lack of physical verification
is not evidence of a newly discovered code defect.
