# Keep streamO off until an intentional, checked start

## Goal and decisions

Plugging in, booting, provisioning, updating, or rolling back the Pi must not
start a broadcast. streamO is available to showCo but publishing is off by
default. Attaching devices or restoring network access must not itself publish.

The proposed flow is explicit **Start Twitch** after readiness passes. The user
approved recording, audio, and clock prerequisites only; lighting readiness must
not block publishing. Keep silence filtering. Quiet input with fresh samples
is valid; absent or stalled capture is not.

After an explicitly started broadcast fails, the user approved automatic
recovery only after fresh readiness checks, with at most **three automatic
relaunches**. Once that allowance is used, remain off until explicit Retry.
Neither boot nor maintenance may resurrect previous broadcast intent.

Pending user decision: confirm explicit Start rather than automatic publication
on initially becoming ready.

Implemented in source: streamO can be installed as a manual systemd service.
Updated provisioning and target updates that include streamO leave it off.
A publisher using recs waits for fresh samples from its own subscription
before opening output. The Pi still needs the migration. Use the updated
provisioning path or a deploy that loads the current updater and includes
streamO. An older updater can restart an active publisher.

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
5. Normal target updates preserve a previously stopped publisher. The
   remaining maintenance risk is enabled provisioning or refreshing an
   already-running publisher without fresh authorization and readiness.

October 21's device-free boot is consistent with this path: approximately
27 seconds of encoder output before the missing-input failure, and a short
broadcast independently listed in Twitch's dashboard. Today's API HTTP 401
does not establish why publishing did or did not work: API credentials and the
ingest stream key are separate concerns.

## Implementation plan

### 1. Install a manual-start service without briefly publishing (implemented)

Use an installed, inactive streamO service on the Pi, excluded from boot
activation. Keep `stream.enabled` meaning the feature is available. Do not
globally disable or mask it, because explicit Start must work.

`StreamoClient.status()` represents an installed inactive Linux unit as
stopped. Use that lifecycle for the proposed explicit-start flow.

reccy renders a static unit with `Restart=no` and disables and stops an
existing service before replacing its definition. Provisioning validates the
configuration and uses that installer. A target update affecting streamO
stops showCo first, takes streamO offline, and leaves it offline on rollback.
Provisioning and updates verify the systemd properties without broadcasting.

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

Gate `streamo-start`, `streamo-restart`, streamO recovery
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

streamO now waits for fresh audio from its selected recs subscription before
provider preparation, and checks again immediately before opening the encoder.
If samples never arrive, startup fails after a bounded wait. Fresh, quiet
samples qualify.

#### Bounded recovery after an intentional start

- Allow three automatic relaunches in addition to the initial explicit start.
  Charge an attempt when a relaunch is initiated, even if it subsequently fails.
- Run fresh recording/audio/clock readiness checks before each relaunch. Failed
  checks leave publishing off without consuming an attempt; any readiness
  polling must use bounded, interruptible waits, not a busy loop.
- A successful automatic recovery does not replenish the budget. A later
  failure in the same deliberately started broadcast uses the next attempt.
- After the third automatic attempt, a further failure stays off. Explicit
  Start/Retry establishes a new budget only after its readiness checks pass.
- Preserve the used allowance through controller or publisher process restarts
  within the same authorized run. Losing recovery state must not silently grant
  another three attempts or authorize publishing.
- Stop, show teardown, reboot, provisioning, and deployment cancel pending
  recovery. A recovered network or input cannot override an explicit Stop or
  rearm a broadcast after boot.
- Use one recovery owner and attempt budget. Coordinate showCo, the service
  restart policy, and streamO's own retry behavior so nested retries cannot
  multiply the allowed number or bypass fresh checks.
- Show each failure, the used/remaining attempts, readiness blockers, and the
  exhausted/off state on the front page. Recovery never restores missed audio.

### 4. Show the start blockers and recovery budget

Display **Twitch off** after boot and show specific unmet prerequisites. Disable
Start while blocked and return the same reasons from the backend. Show the
recovery budget and exhausted state. Preserve provider confirmation separately
from publisher activity and keep deliberate Stop available under uncertain health.

Coordinate this with the [per-input recording evidence issue](issues.md#p2-the-show-page-lacks-per-input-recording-evidence).

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
   must remain available. Test exactly three automatic relaunches and no fourth;
   failed readiness must launch nothing, successful recovery must not reset the
   budget, and explicit Retry must start a new budget. Cover process restarts,
   lost recovery state, concurrent requests, and Stop cancelling pending retries.
4. Commit any approved publisher-side first-audio protection with tests proving
   no provider connection opens while waiting for real input.
5. Run required project checks for each code commit. Separately authorize target
   acceptance: cold boot with no devices creates no Twitch session; attaching
   devices and passing readiness still creates none; only intentional Start
   publishes. Exercise Stop, reboot, update, rollback, startup input loss, and
   the three-attempt crash-recovery policy, including exhaustion and manual Retry.

No deployment, service changes, hardware checks, or test broadcasts are performed
by creating this plan. Implementation and target acceptance remain outstanding.

## Additional work beyond the prompt

None.
