# Acceptance tests

Run these tests when the Raspberry Pi arrives and again after every meaningful
hardware, OS, network, or service change.

Do not treat the box as stage-ready until every required test passes.

## 1. Boot and access

Pass criteria:

- Pi boots headless.
- tablet joins the Pi show network.
- SSH works from a trusted machine.
- Showco opens on the tablet.
- system clock is correct enough for logs and file names.

Commands:

```bash
hostname
date
systemctl --user status showco
```

## 2. Storage

Pass criteria:

- external storage is mounted at the expected path.
- Recs recording path is on external storage.
- the SD card is not used for show recordings.
- a write test succeeds.

Commands:

```bash
findmnt /mnt/recs
df -h /mnt/recs
touch /mnt/recs/write-test && rm /mnt/recs/write-test
```

## 3. X18 USB audio

Pass criteria:

- X18 appears as an input device.
- all expected input channels are visible.
- sample rate is 48 kHz.
- Recs can open the device.

Commands:

```bash
arecord -l
arecord -L
```

## 4. Recs recording

Pass criteria:

- Recs starts as a daemon.
- Showco reports Recs connected.
- Showco shows level state changes for active inputs.
- a ten-minute recording writes files to external storage.
- logs show no buffer overruns, dropped blocks, or write stalls.
- stopping or rebooting leaves a recoverable manifest/session file.

Commands:

```bash
systemctl --user status recs
journalctl --user -u recs --since "10 minutes ago"
```

## 5. Showco actions

Pass criteria:

- noise-floor calibration button reports success when Recs is configured for
  calibration.
- muting Twitcho updates Showco status.
- unmuting Twitcho updates Showco status.
- destructive or show-ending buttons require confirmation.
- failed actions remain visible in the recent-action log.

## 6. Twitcho local stream process

Pass criteria:

- Twitcho starts as a service.
- Showco reports Twitcho connected.
- Twitcho audio seconds increase while streaming.
- Twitcho mute produces silence in the stream path.
- Twitcho stop terminates the streaming process cleanly.

Commands:

```bash
systemctl --user status twitcho
journalctl --user -u twitcho --since "10 minutes ago"
```

## 7. Twitch API side effects

Only required when Twitch streaming is part of the show.

Pass criteria:

- token has the required scopes.
- update stream information succeeds.
- send chat message succeeds.
- send announcement succeeds.
- create clip succeeds when the channel is live.
- create stream marker succeeds when the channel is live.
- Showco displays failures clearly when Twitch rejects a request.

## 8. Network isolation and reachability

Pass criteria:

- tablet can reach Showco.
- tablet can control the X18 through the planned network path.
- X18 wired Ethernet remains reachable while the Pi access point is active.
- internet path for Twitcho works if Twitch is enabled.
- losing internet does not stop Recs recording.

## 9. Reboot recovery

Pass criteria:

- after power cycle, all enabled services return to the expected state.
- Showco returns without manual shell commands.
- Recs does not record to the wrong path if external storage is missing.
- logs clearly explain any failed service.

## 10. Field-length soak

Pass criteria:

- run for at least the maximum expected show length plus setup time.
- no Recs dropouts.
- no unbounded log growth.
- no disk-full or storage-stall warnings.
- Showco remains responsive from the tablet.
- Twitcho remains connected or reports failures clearly.

Recommended minimum:

- 5 hours recording.
- Twitcho enabled if it will be used live.
- tablet connected to the Pi network throughout.
