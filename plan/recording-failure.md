# October 17 recording failure: investigation and prevention

## Outcome and scope

The October 17, 2026 show ran from 15:00 to 19:00 local time. The user
inspected the available recordings and identified them as rehearsal material,
not the show. The front Show page remained open and displayed no errors.
Treat the incident as loss of the show recording, together with failure to
warn the operator. Do not treat existing rehearsal audio as successful capture
of the show.

This plan covers investigation and changes to recs and showCo. Implementation,
deployment, and runtime qualification are separate work. Preserve silence
filtering during shows. There is no independent-recorder requirement.

The objective is that capture failures are detected, reported on the front page,
and recoverable when possible, with durable evidence of what was and was not
saved. Passing a service-connectivity check must never imply successful capture.
Do not claim that a plan or automated tests can guarantee recording under every
possible failure.

## Evidence collected read-only on October 21

- `/dev/sda1` was mounted at `/mnt/recordings`; the recs tree occupied 6.3 GB.
  Sessions were under `/mnt/recordings/recs/-default-/2026/10/`.
- The discovered session start labels ran from October 16 at 18:44 to
  October 17 at 04:20, followed by today's device-free session. No identified
  session establishes capture of the October 17 afternoon show.
- Most audio-bearing sessions had `session-record.jsonl`, FLAC files, and a
  recovery report, but no `recording.toml`. Some journals have a footer, so the
  missing documents cannot all be explained simply as unclosed sessions.
- `report_scan.recording_sessions()` gives journal-only sessions zero known
  captured audio duration. `select_sessions()` therefore excludes their audio
  from the greater-than-60-second qualification rule. This explains why the
  report can say no qualifying show despite substantial audio on disk.
- Calling `prepare_recording()` in memory, without calling the file-writing
  finalizer, reproduced `Audio stream has no device rate` for sessions labeled
  October 16 at 19:23:57, 22:59:51, and 23:57:50. The journal producer
  `RecordingSession.record_file_started()` omits `SourceFile.sample_rate`.
  The finalizer requires either that field or a matching device-clock rate.
  The relevant producer and finalizer code also exists in recs `532ea4d`,
  the revision initially observed on the target before today's update.
- The same in-memory check for the session labeled October 17 at 00:02:11
  failed with `Audio payload count disagrees with capture` for
  `audio/7-8 + 20261017-041823.flac`. Reading FLAC headers also returned an
  impossible aggregate duration. Header readability is not proof of valid audio.
- Journals contain repeated audio `file_started` identities, including two in
  that 00:02:11 session. The mechanism and impact need a targeted reproduction;
  repeated journal entries alone do not prove that media was overwritten.
- recs logged an uncaught OSC validation failure involving NaN at
  `/fx/1/par/14`. Its user-service journal records exit status 255/EXCEPTION
  and an automatic restart five seconds later. These entries are labeled
  October 17 at 00:02:03 and 00:02:08 local time. This is a confirmed recorder
  crash, not an established explanation for the afternoon loss.
- Retained logs also contain dropped-frame reports and source shutdown
  failures. They must not be assumed to explain a different session.
- The session labeled October 17 at 04:20:04 contains no audio-source or audio
  file records. Its end was written today after the clock correction. Its
  apparent 107-hour duration is not evidence of continuous recording.
- Today the Pi synchronized its clock 36 seconds after boot, changing its date
  from October 17 to October 21. The user powered it off on October 17 and
  started it today. This expected startup correction does not establish that
  the clock was wrong during the show. Do not use it to relabel rehearsal
  files as show recordings.
- No recs/showCo user-service entries were returned for October 17,
  14:30–19:30. Monitoring history does not establish afternoon capture either.
  Absence of retained entries does not identify which component failed.
- The target was updated during this investigation. Current source/environment
  state must be distinguished from the revisions and settings used at the show.

## What is established, and what is not

There are reproducible recording-finalization defects and a confirmed recorder
crash in retained evidence. There is a confirmed report-discovery limitation.
There are concrete front-page warning omissions. None of these, individually,
has yet been demonstrated to be the initiating cause of the afternoon loss.
Do not close the causal investigation by selecting whichever earlier error
looks most plausible.

The missing information is a trustworthy show-time account of selected inputs,
device presence, capture state, writer state, actual persisted audio, session
identity, and service restarts. The retained monitoring stores resource and
disk-space aggregates, not this complete recording lifecycle.

## Front-page warning failure

`runtime/incidents.py: fault_details()` does not promote `recs.errors` into
active faults. Those errors appear in detailed pages. A recorder can therefore
report a finalization/write failure without that error reaching the front-page
fault banner. The initially observed target showCo revision `1a52247` has this
omission too.

`runtime/views.py: page()` deliberately omits `monitoring-error` on the Show
page. A failed observer or history writer can consequently be invisible there.

`runtime/readiness.py: status()` checks service/snapshot availability, pause
state, and disk-space flags. It does not prove that required inputs are being
captured and usable audio is being saved. `recording_progress.ProgressMonitor`
watches an aggregate counter, which cannot prove that every required input is
being recorded or that files can be decoded. The Show GUI has transport buttons
and the fault banner, but no permanent recording-evidence display.

These omissions explain how important failure information can be absent from
the page the operator used. They do not reconstruct the exact banner displayed
during the show; no browser/status-response recording was retained.

## Implementation order and acceptance criteria

### 1. Preserve evidence and finish the causal investigation

Preserve the recording backup and obtain a separate diagnostic snapshot of the
retained recs/showCo logs, rotated logs, user-service journal, monitoring,
incident state, relevant non-secret settings, and revision/lockfile identities.
Keep originals untouched. Do not finalize, repair, or rename original sessions.
Any recovery experiment must use a copy and report exactly what it changed.

Correlate boot IDs and monotonic timestamps before calendar-time claims. Locate
any other configured recording roots or show-time storage. Determine whether
the inputs were absent, paused, excluded, silent, failing to write, or whether
files were later lost. If evidence cannot distinguish these cases, record that
limit instead of inventing a root cause. Identify the show-time software and
configuration independently of today's deployment.

Acceptance: an evidence-backed timeline, or an explicit account of which
questions the surviving evidence cannot answer. Keep confirmed earlier failures
separate from the October 17 initiating cause.

### 2. Make recorder failures visible on the front page

Promote actionable recs capture, writer, storage, and finalization errors into
the existing fault model. Include failed monitoring/observation on the Show
page. Retain serious incidents across transient recovery and recorder restarts;
acknowledgement must not claim the missing audio was restored.

Add a permanent, compact recording display to the front page: required inputs,
capture state, destination, current session, evidence of saved audio, and any
unknown or failed state. A connected process or an increasing elapsed timer
must not produce a successful-recording indication. Status polling failure
must visibly expire prior success.

Acceptance: focused status/UI tests for recorder errors with RPC still healthy,
observer failure, reconnect, restart, stale status, and acknowledgement. Verify
the Show page itself, not only Health. Do not require the operator to watch
another page to discover that the recording failed.

### 3. Make silence filtering distinguishable from recording failure

Keep silence filtering. Expose per-required-input evidence through recs' public
snapshot: source capture progress, deliberate suppression, writer progress and
failures, and session identity. Reuse the existing capture timeline and gap
reasons. Specify counters from the actual capture/writer path, not the UI clock.

Missing devices, disabled required inputs, absent callbacks, writer exceptions,
and lost recording destinations must be explicit states. Intentional silence
suppression must remain distinguishable from an input that stopped delivering
samples. Other inputs continuing must not hide one failed input. Saved-frame
evidence must not be labelled as proof of crash durability without a defined
flush/durability policy.

Acceptance: deterministic tests for prolonged silence, signal returning after
silence, one input failing while others advance, disk write failure, and session
replacement/reset. Bound detection time using monotonic time. Do not solve
false alarms by disabling error detection during quiet passages.

### 4. Fix the confirmed recs lifecycle defects

Preserve the actual source sample rate and clock identity in file lifecycle
records. Reproduce the sample-rate failure through the normal recording path
and verify that normal finalization produces a readable recording document.
Trace and correct repeated file-start identities without concealing malformed
capture evidence in the finalizer.

Contain malformed/non-finite OSC messages at the OSC boundary, report the
problem, and keep unrelated audio capture running. Determine why the mismatched
FLAC was not finalized correctly. Validate file closure, frame counts, and
interrupted capture; do not accept impossible header durations as saved audio.

Make finalization errors durable and observable after session replacement and
process restart. A recovery worklist must not forget an incompletely finalized
session merely because its journal has a footer. Successful finalization should
not depend on clearing a previous error before it reaches an observer.

Acceptance: meaningful 48 kHz audio regressions with at least one second of
audio, including silence transitions, normal stop, malformed OSC, source
failure, restart, and truncated/incomplete media. Assert decoded output and
journal/document consistency. Keep audio-thread deadlines independent of
expensive finalization and verification work.

### 5. Make reports disclose incomplete recordings

Report scanned roots, candidate sessions, and reasons candidates were excluded
when no show qualifies. Surface missing/invalid metadata and media errors even
when those sessions cannot qualify. Preserve the existing three-hour grouping,
greater-than-60-second audio rule, and historical selectors.

Use journal evidence and verified media where sufficient to account for
incomplete sessions without changing the originals. Keep unknown duration
explicit when evidence is insufficient. Do not add parallel-track durations
together or qualify MIDI-only sessions. Recovery is an explicit separate
operation on copies, not a side effect of reporting.

Acceptance: regression fixtures for journal-only audio, incomplete FLAC headers,
missing media, duplicated lifecycle entries, invalid recording documents, and
mixed valid/incomplete sessions. The actual incident layouts must produce useful
diagnostics instead of only `No qualifying show found`.

### 6. Retain a useful recording incident history

Extend existing evidence retention with boot/session identity, selected inputs,
capture and suppression progress, writer failures, finalization outcomes, output
destination, and deployed revisions. Record transitions and bounded summaries;
do not introduce a database or retain every audio callback.

Acceptance: a simulated silent capture failure can be reconstructed after a
restart without the browser having visited Health. A wall-clock correction
must not turn a short device-free session into evidence of days of recording.
Retain `showco-errors.txt` as a last-invocation diagnostic; this plan does not
change that intended behavior.

### 7. Qualify the complete recording path before calling it fixed

Run focused regressions and the required checks for each implementation commit.
Then schedule a separate authorized recording qualification using the intended
inputs, silence filtering, destination, and front Show page. Exercise missing
input, stalled writer, disk failure, OSC error, recorder restart, lost status,
and interrupted shutdown. Confirm faults appear within their specified bounds,
recovery does not claim to restore gaps, and produced files decode correctly.

For a show-length recording, verify saved coverage for every required input,
explicitly accounting for suppressed silence and known losses. Record the exact
revisions and results. Earlier successful show-control operation does not
qualify this recording path. No physical checks are authorized by this document.

## Additional work beyond the prompt

None.
