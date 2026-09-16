# Feature suggestions

The performance screen, performance lock, persistent incident monitoring, set list, guided soundcheck, and guided recovery are implemented. Keyboard and footswitch controls are deferred at the operator's request. The later entries remain proposals. Unresolved physical validation belongs in [hardware.md](hardware.md).

Start with features that reduce attention and mistakes during a performance. Keep recording, lighting generation, and streaming in recs, lyte, and streamO; showCo should coordinate their public interfaces. Any sibling-project work needs its own scope and coordination.

## Suggested priorities

| Priority | Feature | Main benefit | Scope |
| --- | --- | --- | --- |
| Implemented | Performance screen | Fewer page changes and missed problems | showCo |
| Implemented | Performance lock | Fewer accidental interruptions | showCo |
| Implemented | Persistent incident monitoring | Detect trouble without an open browser | showCo, using current service evidence |
| Implemented | Set list and manual cues | Less administration between songs | showCo; recs markers only |
| Implemented | Guided soundcheck | Repeatable preparation and useful evidence | showCo; physical acceptance outstanding |
| Implemented | Guided recovery | Faster, more predictable fault handling | showCo; explicit service restart and verification |
| Deferred | Keyboard and footswitch controls | Operate without reaching for a tablet | Not requested for this implementation |
| Later | Lighting looks and transitions | More expressive visual performances | lyte capability work, then showCo controls |
| Later | Highlight markers and stream presentation | Capture memorable moments and keep viewers informed | recs and streamO capability checks |
| Later | Show report | Easier troubleshooting and preparation for the next show | showCo |

## 1. Performance screen

**Implemented:** `/performance` provides marker, next-song, and pause/resume controls, status and fault visibility, persistent browser pins and dimming, and optional screen-awake mode. Automated browser tests cover operation and disconnection; physical tablet acceptance remains outstanding.

Add a dedicated screen with large controls for the few actions used during a show: mark a moment, advance the set list, pause or resume recording, and inspect the most urgent fault. Keep recording state, recording destination, remaining capacity, stream state, and connection age visible together. Let the performer pin important inputs so unused channels do not dominate the screen.

Use readable text alongside color, large touch targets, and a dim display option. Keep detailed configuration on the existing pages. Offer a screen-awake control where the browser supports it, with a visible indication when it is unavailable.

**Acceptance:** common performance actions work from one tablet screen; a disconnected screen cannot look live; buttons keep stable positions while status changes.

## 2. Performance lock

**Implemented:** server-side protection, explicit unlock confirmation, persistent state, and visible status on every page. Read failures block protected actions. Separate CLI and deployment commands remain outside this lock.

Add an explicit “Performance in progress” mode. Block cable tests, calibration, recorder shutdown, session replacement, and configuration edits until the operator deliberately unlocks them. Leave normal performance controls available. State exactly which operations the mode protects; a web lock cannot imply that separate CLI or deployment commands are blocked.

Enforce the lock on the server so another open tab cannot bypass it. Show its state on every page and retain it through a showCo restart. Entering or leaving the mode must not itself change recording, lighting, or streaming.

**Acceptance:** an old tab cannot run a protected action after the lock is enabled, and unlocking never replays a previously rejected action.

## 3. Persistent incident monitoring

**Implemented:** the target's existing sampling worker collects service observations without browsers, retains 100 history entries and up to 100 active faults, and preserves acknowledgment separately from recovery. Storage and observation failures are visible. There are no audible alerts. Rehearsal retains its in-memory, request-driven behavior.

Extend the existing background health sampling to collect service and recording observations even when nobody has the UI open. Save a bounded history across showCo restarts. Distinguish “service connected,” “recording requested,” and “audio writes observed,” including the expected effects of silence filtering.

Present one persistent banner for an ongoing fault, with its start time, latest evidence, and a useful next action. Allow acknowledgment without turning an unresolved fault green. Avoid repeated alerts for the same condition; optional audible alerts should default off because the performer may be recording.

**Acceptance:** a simulated fault while all browsers are closed appears with its observed time when the page reopens; recovery and acknowledgment remain distinct; monitoring storage stays bounded.

## 4. Set list and manual cues

**Implemented:** `/setlist` prepares songs, notes, and durations; `/performance` sends manual named markers and supports skip, repeat, and interval. Position and pending requests persist. Revision checks reject stale requests. The current recs API has no marker request identifier: an uncertain reply requires explicit acceptance without resending, or an explicit retry acknowledging duplicate risk. Lighting and title cues remain later work.

Let the performer prepare an ordered list of songs with short notes and an expected duration. During the show, display the current song, next song, elapsed set time, and one “Start next song” control. That action can create a named recs marker without requiring typing. Support skips, repeats, and an interval without rewriting earlier markers.

Later, an explicitly configured cue could also select a lyte look and update a streamO title. Show each service's result separately: a lighting failure must not imply the recording marker failed, and advancing the set list must not stop recording. Do not advance songs automatically from elapsed time or inferred silence.

**Acceptance:** a double tap creates one cue; refreshing the page preserves position; a failed component can be retried without repeating successful components. Confirm service support before promising duplicate suppression across uncertain network failures.

## 5. Guided soundcheck

**Implemented:** `/soundcheck` saves expected inputs and timestamped measured or operator-confirmed results, with explicit skipped steps. Sample recording uses the current session; playback confirmation names the sample the operator heard. Date, Linux mount identity, observed input layout, service state, and saved recs settings changes invalidate results through background observation. Physical mixer changes still require a fresh operator check. Cable testing remains excluded pending hardware validation.

Turn the manual preparation checklist into a short, resumable workflow: identify the recording disk, confirm expected inputs, inspect signal and clipping, record a sample, and confirm playback from that recording. Include optional explicit lighting and streaming checks. Display when each check last passed and which device or configuration it covered.

Keep automated observations separate from operator confirmations such as “heard playback” or “saw the lights.” Device or configuration changes should invalidate relevant checks. Add the cable test only with clear physical setup instructions and after the outstanding timing, routing, and calibration work is validated.

**Acceptance:** yesterday's check or a different disk cannot silently count as today's successful test; skipping a step remains visible; no output-producing test starts merely by opening the page.

## 6. Guided recovery

**Implemented:** `/recovery` refreshes status, downloads the existing diagnostic bundle, and offers a confirmed restart only for a failed or disconnected enabled recs, lyte, or streamO service. Restart requests and the original failure persist before execution; verification is a separate explicit action. No automatic retry or restart occurs. Connected-recorder storage and input faults require inspection rather than a restart shortcut.

For a disconnected service or recording fault, offer a short explanation and only the relevant recovery actions. Show what each action interrupts before it runs. Begin with status refresh, diagnostics download, and reconnection where supported; require an explicit choice for service restart or recorder interruption.

Show progress and verify the resulting service state. Preserve the original failure and the recovery outcome in the incident history. Do not automatically restart recs because silence-filtered files have stopped growing, and do not claim missing audio has been recovered.

**Acceptance:** a failed recovery remains visibly failed, unrelated services keep running, and retrying does not silently repeat a completed destructive action.

## 7. Keyboard and footswitch controls

Provide a small, configurable set of shortcuts for marking a moment, starting the next song, and opening the performance screen. Start with ordinary keyboard events, including pedals that act as keyboards. Ignore shortcuts while typing into an input, suppress key-repeat activation, and display a clear acknowledgment.

Treat MIDI or other pedal support as a separate device integration, with explicit bindings and a test screen. Do not assign shutdown, cable tests, or calibration to a single pedal press.

**Acceptance:** holding a pedal does not create repeated markers, reconnecting it triggers no cue, and editing a track name cannot accidentally advance the show.

## 8. Lighting looks and transitions

Expose a few named, rehearsed lyte looks such as song, interval, and finale, with a brightness limit and transition duration. Show the active look and provide a clear “Lights off” control. A set-list cue can select a look, but the performer should also be able to change it independently.

Keep animation execution and timing in lyte. Confirm or add its required public controls before implementing the UI. Start with manual transitions; audio-reactive effects and synchronized timing can follow after physical rehearsal. A browser reconnect must never replay a lighting cue.

**Acceptance:** a look continues when the browser disconnects, the UI reports the actual active look after reconnecting, and the off control remains accessible during a failed transition.

## 9. Highlight markers and stream presentation

Add a large “Highlight this moment” button that records a named marker for later editing, with an optional streamO clip request when streaming is enabled. Show these as separate outcomes. A marker identifies a moment; it must not be presented as a saved audio excerpt or successful stream clip.

Offer a preview of the next song title or interval announcement before sending it. Longer term, a read-only presentation page could expose approved song titles and interval messages for stream overlays. Keep operator notes, diagnostics, and private configuration out of that presentation surface.

**Acceptance:** highlights still work when streaming is unavailable; failed clip requests remain visible; publishing a title requires an explicit action rather than a status refresh.

## 10. Show report

After the performance, provide a concise downloadable report containing set-list markers, recording destination and observed progress, incidents and recoveries, service versions, and any operator notes. Extend the existing diagnostic bundle rather than creating a competing collection mechanism.

Where recs supplies authoritative session or file information, include it with its source. Label gaps and unknown values. Keep credentials, private configuration, chat contents, and audio out of the report by default.

**Acceptance:** the report survives a showCo restart, separates observations from confirmed recording results, and helps answer when a problem began and what happened afterward.

## Delivery approach

The first six features are delivered. Keyboard and footswitch controls remain deferred. Introduce lighting and streaming cues only after the necessary service contracts are confirmed and rehearsed.

For each feature, verify normal use, stale status, browser reload, repeated input, and partial service failure. Physical claims still require the checks in [hardware.md](hardware.md). These suggestions do not authorize running services, changing sibling repositories, or deploying anything.

## Additional work beyond the prompt

None. Implementation covers the first priorities and the next three requested workflows. Keyboard and footswitch controls are excluded; later entries remain proposals.
