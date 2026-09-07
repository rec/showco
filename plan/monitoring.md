# Simple Performance Monitoring

## Goal

Add lightweight CPU, memory, and recording-disk monitoring to Showco and render
it on the existing Health page. The data must update with the existing
once-per-second `/status` polling, remain useful when one source is unavailable,
and add no background service, database, dependency, configuration, or new web
page.

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
until the next status request. Treat a zero or negative total delta as
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

Extend `SystemMonitor` directly; do not create a second monitor or worker
thread.

- Inject the `/proc/stat` and `/proc/meminfo` paths for deterministic tests.
- Protect CPU counters and the most recent sample with a lock because the HTTP
  server handles requests concurrently.
- Cache a complete system sample for approximately one second so simultaneous
  page requests do not consume the CPU baseline or repeatedly read procfs.
- Continue reading temperature through the existing injected path.
- Return partial `SystemStatus` data when any individual metric fails.

The monitoring code runs only when `/status` or a status page is requested. It
must not perform network I/O, spawn commands, write files, or retain historical
samples beyond the previous CPU counters and latest short-lived result.

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
5. Concurrent or back-to-back status calls using the short cache without
   corrupting the CPU baseline.
6. Parsing a complete Recs recording-disk snapshot.
7. Invalid disk fields without losing valid OSC or MIDI snapshot data.
8. Preserving the last valid disk sample during a temporary snapshot failure
   and replacing it after recovery.
9. Initial Health HTML containing all three metric rows, meters, numeric values,
   units, and unavailable states.
10. Browser refresh code updating all values, meter positions, and warning
    states while accepting zero values.
11. Existing temperature, Recs snapshot, waveform, and Health behavior remaining
    unchanged.

Run the full Showco test suite, Ruff, formatting, Ty, pyupgrade, lock validation,
and `git diff --check`. Automated tests do not establish Raspberry Pi load or
disk behavior; validate those separately on the target after deployment.

## Documentation

Update `doc/architecture.md` to state that Showco samples Linux CPU and memory
on demand and displays Recs' current recording-disk capacity. Document that the
metrics are short-lived operational status, not historical telemetry.

## Acceptance Criteria

- Health shows CPU, memory, and current recording-disk usage without a new tab.
- Values refresh through the existing `/status` request once per second.
- Monitoring adds no dependency, process, persistent data, or configuration.
- One failed metric does not hide or delay the other metrics.
- Disk usage refers to Recs' actual recording destination.
- CPU sampling is thread-safe and does not misinterpret the first sample.
- Recs disk alerts and pauses are visible but remain controlled by Recs.
- Existing status and control behavior remains unchanged.

## Additional Work Beyond The Prompt

None.
