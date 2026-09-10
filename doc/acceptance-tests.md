# Hardware Acceptance Tests

Run this checklist after first provisioning and after meaningful hardware, OS,
network, service, recording, lighting, or streaming changes. Automated tests do
not prove that external devices performed their work.

Record the date, deployed commit IDs, attached devices, recording destination,
and pass/fail result outside this document for each acceptance run.

## 1. Boot and access

Pass criteria:

- The Pi boots without interactive input.
- SSH key authentication and `sudo -n true` succeed.
- Enabled user services start after boot.
- The tablet opens Showco at the configured port.
- The target clock is correct enough for logs and recording names.

Commands:

```bash
ssh USER@HOST 'date; sudo -n true'
ssh USER@HOST 'systemctl --user status recs showco'
showco logs showco recs --lines 100
```

Replace `USER@HOST` with the target SSH login.

Add `lyte` or `streamo` to the service and log commands when enabled.

## 2. Storage

Use the recording-disk path reported on Showco's Health page rather than
assuming a fixed mount point.

Pass criteria:

- The intended removable disk is mounted and is the disk reported by Recs.
- Available space and the estimated recording duration are plausible.
- A write and sync on that filesystem succeed.
- Recordings are not being written to the Raspberry Pi SD card.

Commands, replacing `PATH` with the reported path:

```bash
findmnt -T PATH
df -h PATH
touch PATH/showco-write-test
sync
rm PATH/showco-write-test
```

## 3. Audio, MIDI, and mixer control

Pass criteria:

- Each configured USB audio device appears in `arecord -l`.
- Each configured MIDI input appears in Recs status.
- The Channels page shows the expected tracks and recording indicators.
- The tablet controls the mixer over the intended Ethernet path.
- Disconnecting and reconnecting each USB device returns it to recording
  without restarting the Pi.

Commands:

```bash
ssh USER@HOST 'arecord -l; arecord -L'
showco logs recs --lines 200
```

The current X18 UDP probe sends `/xremote` and waits for a reply. A waiting or
failed probe is not by itself proof that the mixer is unreachable. Confirm with
the tablet mixer application and, when diagnosing, packet capture or recent Recs
OSC feedback.

## 4. Recs recording

Pass criteria:

- Recs starts and Showco reports a connected, current status snapshot.
- Channel waveforms update smoothly while audio is present.
- Track-name and stereo changes survive a page reload.
- Pause and resume change recording state without losing the web response.
- A short recording creates growing files on the reported removable disk.
- After stopping Recs, files remain readable and complete.
- After unmounting and remounting the disk, those files are still present.
- Logs contain no unexplained overruns, dropped blocks, or write failures.

Do not use Showco counters as the only evidence that bytes reached storage.
Inspect the files and play or decode a sample recording.

## 5. Showco pages and actions

Pass criteria:

- Channels, Health, Attributes, Actions, and Errors all load on the tablet.
- An unavailable service does not make another page unavailable.
- The Health page updates CPU, memory, disk, temperature, mixer, MIDI, and OSC
  state without a reload.
- With no current Recs errors, Health and Errors explicitly show no errors.
- Action buttons visibly enter and leave their busy state.
- Successful and failed actions appear under Recent actions and in the Showco
  log with useful details.
- Calibration, marker, profile reload, pause, resume, and status actions return
  the expected result.
- Recs shutdown and Twitch stop require deliberate confirmation.

## 6. Monitoring retention

Pass criteria:

- CPU or memory load appears on Health within a few seconds.
- One aggregate record is appended per minute.
- A clean Showco shutdown writes the final partial minute.
- Files older than the seven-day UTC retention window are removed.
- Monitoring failures appear in the Showco log and do not stop the web UI.

Commands:

```bash
ssh USER@HOST \
  'ls -l ~/.local/state/showco/monitoring; tail ~/.local/state/showco/monitoring/*.jsonl'
```

## 7. Lyte

Only required when Lyte is enabled.

Pass criteria:

- Lyte reaches connected state and Showco reports its output details.
- Lyte connecting after Showco queues one automatic light test.
- Test lights runs for one second, peaks at 30 percent, and leaves the lights
  off afterward.
- Disconnecting and reconnecting the lighting network restores control and
  permits another automatic test.
- A failed test appears in Recent actions and both service logs.

## 8. Streamo and Twitch

Only required when Twitch is enabled.

Pass criteria:

- Streamo starts as a user service and Showco reports it connected.
- Audio time and bitrate advance while streaming.
- Mute and unmute affect the stream path.
- Restart creates a fresh streaming attempt and stop terminates cleanly.
- Updating stream information, chat, announcement, clip, and marker actions
  either succeed or display Twitch's rejection clearly.
- Loss of internet does not stop Recs recording.

## 9. Network and late devices

Pass criteria:

- The tablet receives an address on the configured internal subnet.
- Showco is reachable at the configured internal Wi-Fi address and web port.
- The mixer is reachable at its configured host offset.
- The private topology works with no external network.
- Mixed topology keeps private mixer control while using external Wi-Fi.
- Devices absent at boot can arrive later, disappear, and return without
  reprovisioning.

Test the real startup order: boot the Pi first, power the X18 several minutes
later, and attach any later mixer or MIDI device after the system has been
running for at least an hour.

## 10. Reboot and update recovery

Pass criteria:

- A power cycle restores all enabled services without manual shell commands.
- Missing removable storage does not silently redirect recording to the SD card.
- `showco` reports success only after target verification completes.
- A no-change `showco` run takes the update path rather than reprovisioning.
- A failed deployment leaves enough information in file logs to identify the
  failed repository, service, or verification step.

## 11. Field-length soak

Run for the maximum expected show duration plus setup time, with every service
that will be used live. Five hours is the current minimum.

Pass criteria:

- No audio dropout, unbounded log growth, disk-full warning, or storage stall.
- Showco remains responsive on the tablet.
- Waveforms remain smooth and recover after reconnecting the browser.
- Lyte continues sending frames when enabled.
- Streamo remains connected or reports recovery failures clearly.
- The final recordings remain readable after all services stop and the disk is
  remounted.
