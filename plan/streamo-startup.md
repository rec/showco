# Keep streamO off until an intentional, checked start

## Goal and decisions

Plugging in, booting, provisioning, updating, or rolling back the Pi must not
start a broadcast. streamO is available to showCo but publishing is off by
default. Attaching devices or restoring network access must not itself publish.

The proposed flow is explicit **Start Twitch** after readiness passes. The user
approved recording, audio, and clock prerequisites only; lighting readiness must
not block publishing. Keep silence filtering. Quiet input with fresh samples
is valid; absent or stalled capture is not.

Pending user decisions:

- Confirm explicit Start rather than automatic publication on becoming ready.
- After an authorized broadcast crashes, remain off until explicit Retry, or
  recover automatically only after fresh prerequisites pass. Neither option
  permits boot or maintenance to resurrect previous broadcast intent.

This is a planning change. It does not disable autostart on the current Pi.

## Why plugging in currently publishes

1. `provision/templates/services.sh: install_streamo_service()` calls
   `streamo daemon install` and explicitly restarts the service.
2. streamO delegates installation to reccy's `ServiceController.install()`,
   which enables and starts the systemd unit. Its generated unit has
   `Restart=always` and `WantedBy=default.target`.
3. Starting streamO immediately enters the capture/publish runtime; there is
   no idle daemon state requiring a separate publishing action.
4. `streamo/recs_input.py: RecsAudio` can supply waiting silence while audio is
   unavailable. It stops for a missing input after a 30-second outage allowance,
   rather than preventing publication before the first real samples arrive.
5. `deployment/target_update.py` starts services after updates and during
   rollback/error recovery. Definition refresh can reinstall and start them.

October 21's device-free boot is consistent with this path: approximately
27 seconds of encoder output before the missing-input failure, and a short
broadcast independently listed in Twitch's dashboard. Today's API HTTP 401
does not establish why publishing did or did not work: API credentials and the
ingest stream key are separate concerns.

## Implementation plan

### 1. Install a manual-start service without briefly publishing

Use an installed, inactive streamO service on the Pi, excluded from boot
activation. Keep `stream.enabled` meaning the feature is available. Do not
globally disable or mask it, because explicit Start must work.

`StreamoClient.status()` already represents an installed inactive Linux unit as
stopped. Reuse that lifecycle. Do not introduce a showCo observer which starts
publishing whenever conditions become healthy.

The existing installer always enables and starts the unit. Calling it and then
stopping/disabling streamO leaves a publishing window and is insufficient.
Plan a small explicit manual-install capability in the existing service API,
instead of duplicating reccy's unit rendering and metadata in showCo. This is a
necessary sibling prerequisite to scope before implementation, not permission
to change reccy or dependencies during this documentation task.

Cover initial install, repeated provisioning, already-current services,
definition refresh, normal updates, rollback, and interrupted-update recovery.
None may enable or start publishing. Existing installations need the old boot
activation removed through a tested provisioning/update step; changing only
new-install defaults leaves current Pis unsafe.

Verification must accept the intended inactive/manual state without broadcasting.
Apply the selected crash-recovery policy to the service restart behavior too.
Stop must remain stopped.

### 2. Share one non-publishing readiness decision

The backend and UI must use the same result and reasons. Do not rely solely on
`readiness.status()`: it checks service flags and disk space, not verified audio.
Do not blindly require every soundcheck step either: soundcheck permits skips
and includes a stream check which would create a circular prerequisite.

| Prerequisite | Evidence before publication |
| --- | --- |
| Clock | Current trusted time, with specified offset/age limits; unknown time blocks Start. Network connectivity or a running time-sync process is insufficient. |
| recs | Fresh status from the intended daemon, recording enabled, not paused, with no unresolved capture/writer/storage errors. |
| Storage | Intended recording destination mounted and usable, without disk-space pause/alert. A writable fallback home directory must not silently substitute. |
| Stream audio | Configured source and exact stereo pair present, forwarding enabled, fresh real samples arriving at the native rate. Generated waiting silence does not count. |
| Recording | A saved, verified sample for required inputs in the current capture/configuration/destination scope. Elapsed time or one unrelated input advancing is insufficient. |
| Publisher | Valid configuration, required media, and encoders, checked without opening a publishing connection. |

Reuse streamO preflight, recs' public status/subscription APIs, and suitable
soundcheck evidence. Extend missing checks in their owning component instead
of copying capture code into showCo. Do not open audio hardware separately
while recs owns it. Check reused remote probes for credential-refresh or
metadata-changing side effects before calling them from readiness.

Skipped checks are not verified samples. Invalidate evidence when capture or
session identity, configuration, selected inputs, or mount identity changes.
Fresh silent callbacks are distinct from no callbacks; file-growth pauses due
to silence filtering must not automatically invalidate prior sample evidence.

Clock checks must address the demonstrated unsynchronized-boot case. An
independent server timestamp may provide an anchor, with the request interval
represented as uncertainty. Specify and test freshness/offset limits before
implementation. Do not silently change the clock or rename recordings as a
side effect of checking readiness.

### 3. Gate every route that can publish

Audit `streamo-toggle` when starting, `streamo-restart`, streamO recovery
restart, and `MusicController.start_stream()`. Include Record/Resume show and
closing-abandonment recovery, which currently restart recording and broadcasting.

Those show-recovery actions already express intent to restore broadcasting.
Preserve Resume show's approved ability to interrupt credits. After replacing
or resuming a recs session, establish fresh prerequisites before publishing;
a check against the old session is insufficient.

Use existing action ownership to serialize competing start/stop/recovery
requests. Recheck readiness at dispatch, not only when rendering the page.
A stale browser button or direct POST must not bypass the gate. Stop must
remain available even when readiness fails.

A UI gate alone cannot protect the interval between checking and opening the
provider output. streamO must establish its selected real audio input before
opening that output, with bounded failure and no publication while waiting.
Scope this small publisher-side prerequisite explicitly before implementation;
do not claim a showCo-only precheck protects direct daemon starts.

### 4. Keep the front page explicit and truthful

Display **Twitch off** after boot and show specific unmet prerequisites. Disable
Start while blocked; rejected backend requests return the same reasons.
Readiness becoming healthy must not itself start publishing.

After an intentional start, distinguish starting, encoder active, and provider
publication confirmed or unverified. FFmpeg progress is not proof of Twitch
receipt. Unavailable confirmation must remain explicit. Put startup failures on
the front page, keeping Stop available if process state is uncertain.

Coordinate this with [recording-failure.md](recording-failure.md), especially
front-page errors and per-input recording evidence. Do not bundle unrelated
recovery, naming cleanup, or recording-date migration into this change.

## Commits and acceptance

1. Implement any approved sibling service-installation prerequisite in its
   owning repository, with a scoped commit and tests. Keep dependency/lockfile
   changes separate from behavior changes.
2. Commit showCo lifecycle changes with command-runner tests for initial and
   existing installs, refresh, update, rollback, and interrupted recovery.
   Prove these paths cannot enable/start publishing, even briefly.
3. Commit shared readiness, all entry-point gates, and front-page behavior.
   Test missing devices, wrong pair, stale capture, paused recs, bad storage,
   missing/invalid sample evidence, untrusted clock, stale POSTs, and concurrent
   starts. Quiet fresh samples must pass when other prerequisites pass. Stop
   must remain available.
4. Commit any approved publisher-side first-audio protection with tests proving
   no provider connection opens while waiting for real input.
5. Run required project checks for each code commit. Separately authorize target
   acceptance: cold boot with no devices creates no Twitch session; attaching
   devices and passing readiness still creates none; only intentional Start
   publishes. Exercise Stop, reboot, update, rollback, startup input loss, and
   the selected crash-recovery policy.

No deployment, service changes, hardware checks, or test broadcasts are performed
by creating this plan. Implementation and target acceptance remain outstanding.

## Additional work beyond the prompt

None.
