# Simple Performance Monitoring

## Goal

Add lightweight CPU, memory, and recording-disk monitoring to Showco and render
it on the existing Health page. The data must update with the existing
once-per-second `/status` polling, remain useful when one source is unavailable,
retain a small recent history for diagnosis, and add no background service,
database, dependency, configuration, or new web page.

## Metrics

### CPU

Report aggregate CPU utilization as a percentage across all cores.

Read the first `cpu` row from `/proc/stat` and calculate utilization from the
difference between two samples:

```text
total_delta = current_total - previous_total
idle_delta = current_idle_and_iowait - previous_idle_and_iowait
cpu_percent = 100 * (total_delta - idle_delta) / total_delta
```

The first valid sample has no preceding interval, so report CPU as `sampling`
until the next one-second sample. Treat a zero or negative total delta as
unavailable rather than dividing by zero. Clamp the displayed result to
`0..100` to tolerate counter irregularities without concealing a read or parse
failure.

### Memory

Read `/proc/meminfo`. Use `MemTotal` and `MemAvailable`, not `MemFree`, because
Linux can reclaim caches:

```text
used_bytes = total_bytes - available_bytes
memory_percent = 100 * used_bytes / total_bytes
```

Store byte counts in the status model and calculate presentation units at the
HTML and JavaScript boundary. Missing, malformed, or contradictory values must
produce a memory diagnostic without hiding CPU or temperature.

### Recording Disk

Display capacity for the disk Recs is currently writing to, rather than the Pi
root filesystem. Recs already includes `disk` in `status_snapshot`, with
`path`, `total_bytes`, `used_bytes`, `free_bytes`, estimated remaining time,
and disk-alert state. Parse and retain those fields in `RecsSnapshotClient`.

This keeps disk identity aligned with Recs' removable-disk selection and card
replacement logic. Showco must not independently guess which mounted disk is
the recording disk. If Recs is unavailable or has not selected a disk, display
`recording disk unavailable` while continuing to show CPU and memory.

## Status Models

Extend `SystemStatus` with:

- `cpu_percent: float | None`
- `cpu_error: str | None`
- `memory_used_bytes: int | None`
- `memory_total_bytes: int | None`
- `memory_error: str | None`

Add a `RecordingDiskStatus` model with:

- `path: str`
- `used_bytes: int`
- `free_bytes: int`
- `total_bytes: int`
- `estimated_seconds_remaining: float | None`
- `alert_threshold: str | None`
- `alert_active: bool`
- `paused_for_disk_space: bool`

Add `disk: RecordingDiskStatus | None` and `disk_error: str | None` to the Recs
snapshot result and expose them through `RecsStatus`. Keep the existing snapshot
diagnostic independent: an old valid disk sample may remain visible while a
new snapshot error is reported, just as OSC and MIDI data currently survive a
temporary snapshot failure.

Do not put formatted strings or percentages derived from byte counts into the
JSON model.

## Sampling

Extend `SystemMonitor` to read one instantaneous CPU, memory, and temperature
sample. Add a `PerformanceMonitor` that owns the sampling lifecycle,
aggregation, and persistence.

- Inject the `/proc/stat` and `/proc/meminfo` paths for deterministic tests.
- Continue reading temperature through the existing injected path.
- Return partial `SystemStatus` data when any individual metric fails.
- Start one sampler thread with the web server and stop and join it during
  `server_close()`.
- Sample once per second using a monotonic deadline so the work performed by a
  sample does not accumulate timing drift.
- Have the sampler obtain recording-disk data through the existing cached Recs
  snapshot path; do not add a second RPC implementation.
- Protect the latest sample and current minute aggregate with one lock because
  the HTTP server reads them concurrently.
- Serve the latest completed sample to `/status`; request handling must never
  trigger sampling or persistence.

The thread is part of the Showco process, not a separate service or process. It
must not spawn commands. Sampling continues whether or not a browser is open.

## Storage

Persist one combined monitoring sample per minute as JSON Lines under:

```text
~/.local/state/showco/monitoring/YYYY-MM-DD.jsonl
```

Use Showco's existing state-directory convention rather than introducing a new
config setting. Each record summarizes the one-second samples collected during
that minute and contains:

- `started_at` and `ended_at`: UTC timestamps in ISO 8601 form.
- `sample_count`.
- `cpu_average_percent` and `cpu_peak_percent`.
- `memory_average_used_bytes`, `memory_peak_used_bytes`, and
  `memory_total_bytes`.
- `disk_path`, `disk_minimum_free_bytes`, `disk_total_bytes`, and the latest
  `disk_estimated_seconds_remaining`.
- `disk_alert_occurred` and `disk_pause_occurred`, which are true when any
  sample in the minute reported those states.
- `errors`: a mapping from each unavailable metric to its latest diagnostic in
  the minute.

Missing aggregates are JSON `null`. Do not treat a missing sample as zero, and
calculate each average using only valid samples for that metric. Record all
metrics together so observations from the same minute can be correlated. Do
not store formatted display strings or duplicate the full Recs snapshot.

Keep seven UTC calendar days, including the current day. Remove older daily
files when the first sample of a new day is written. This makes retention
bounded without rewriting an active log or adding a rotation dependency.

The sampler thread rolls over and appends the completed aggregate when the UTC
minute changes. Create the monitoring directory and current file on the first
completed minute. On shutdown, write the partial current minute if it contains
at least one sample so the final observations are not discarded.

A directory creation, append, or retention failure must not invalidate the
live status response. Report the storage problem through logging only when its
error state changes, and try again when the next minute's sample is due. Clear
the logged error state after a successful write.

## Health Page

Add a compact `Performance` section to `/health`, below the Recording and
Streaming cards and above detailed service health. Do not add a navigation item
or tab.

Use three stable rows in a responsive grid:

1. `CPU`: semantic meter plus `42%`, or `sampling`/the concise diagnostic.
2. `Memory`: semantic meter plus `1.2 GiB / 8.0 GiB (15%)`, or the diagnostic.
3. `Recording disk`: semantic meter plus the path and `118 GiB free / 238 GiB
   (50% used)`. Append estimated recording time when Recs supplies it. Show an
   explicit paused or alert state prominently.

Use HTML `<meter min="0" max="100">` elements so the display remains legible
without JavaScript and has accessible numeric semantics. Add restrained CSS for
fixed row dimensions, labels, values, and warning/critical states; do not put
each metric in another decorative card.

Give each meter and value a stable element ID. Extend `status-script.js` to
update values and meter positions from every `/status` response without
replacing the section. Treat numeric zero as valid data, not as an absent value.

Suggested display thresholds are:

- CPU warning at 85%, critical at 95%.
- Memory warning at 85%, critical at 95%.
- Recording disk warning at 85% used and critical at 95% used, with Recs'
  `alert_active` or `paused_for_disk_space` taking precedence over generic
  thresholds.

These thresholds affect presentation only. They must not create errors, stop
recording, or duplicate Recs' disk-space policy.

## Failure Reporting

- Keep metric read and parse failures local to the affected row.
- Do not add transient performance values to the Errors page or persistent
  action history.
- Log a metric failure only when its error state changes, so a missing procfs
  file cannot write once per second forever.
- Log monitoring-storage failures only when their state changes; never fail or
  delay `/status` because history could not be written. A storage failure must
  not stop subsequent sampling.
- A failed Recs snapshot may show the last valid disk figures together with the
  existing snapshot error; do not label stale disk data as current.
- The `/status` endpoint and Health page must remain valid if all performance
  metrics are unavailable.

## Tests

Add focused tests for:

1. CPU percentage from two deterministic `/proc/stat` samples.
2. The initial CPU `sampling` state and a zero-delta sample.
3. Memory usage from `MemTotal` and `MemAvailable`.
4. Independent missing and malformed CPU and memory inputs.
5. One-second sampling updating the latest status without any `/status`
   requests.
6. Parsing a complete Recs recording-disk snapshot.
7. Invalid disk fields without losing valid OSC or MIDI snapshot data.
8. Preserving the last valid disk sample during a temporary snapshot failure
   and replacing it after recovery.
9. Initial Health HTML containing all three metric rows, meters, numeric values,
   units, and unavailable states.
10. Browser refresh code updating all values, meter positions, and warning
    states while accepting zero values.
11. One JSONL aggregate being written for a completed minute of one-second
    samples.
12. CPU average and peak, memory average and peak, minimum disk-free space, and
    any disk alert or pause being aggregated correctly.
13. Missing values and metric diagnostics being represented correctly without
    counting missing samples as zero.
14. A partial final minute being written during clean shutdown.
15. Daily file selection and deletion of files older than seven UTC days.
16. Storage failures leaving live status and subsequent sampling intact, then
    recovering on a later successful write.
17. The sampler thread stopping and joining during server shutdown.
18. Existing temperature, Recs snapshot, waveform, and Health behavior remaining
    unchanged.

Run the full Showco test suite, Ruff, formatting, Ty, pyupgrade, lock validation,
and `git diff --check`. Automated tests do not establish Raspberry Pi load or
disk behavior; validate those separately on the target after deployment.

## Documentation

Update `doc/architecture.md` to state that Showco samples Linux CPU and memory
once per second, displays Recs' current recording-disk capacity, and stores
once-per-minute aggregate monitoring records for seven days. Document the
state-directory path, JSONL record fields, the sampler-thread lifecycle, and
the distinction between the one-second live display and one-minute historical
aggregation.

## Acceptance Criteria

- Health shows CPU, memory, and current recording-disk usage without a new tab.
- Metrics are sampled once per second even when no browser is connected.
- The Health page receives the latest sample through the existing `/status`
  refresh.
- Monitoring persists one combined minute aggregate for seven days, including
  CPU and memory peaks and minimum disk-free space.
- Monitoring adds no dependency, process, database, or configuration.
- One failed metric does not hide or delay the other metrics.
- A history write failure does not break live monitoring or the Health page.
- Disk usage refers to Recs' actual recording destination.
- CPU sampling is thread-safe and does not misinterpret the first sample.
- The sampler stops cleanly with the web server and flushes its final partial
  minute.
- Recs disk alerts and pauses are visible but remain controlled by Recs.
- Existing status and control behavior remains unchanged.

## Additional Work Beyond The Prompt

None.
