# Mac hardware rehearsal

This plan tests as much of the show system as possible on this Mac, using the
real hardware except the Raspberry Pi.

For this test, the local Wi-Fi replaces the Pi access point. The mixer, tablet,
and Mac all join the same local Wi-Fi.

## Goal

Prove the full show workflow with the X18, tablet, storage, Recs, Twitcho, and
Showco while replacing only the Pi with this Mac.

## Test topology

Use the local Wi-Fi for everything:

- X18 joins local Wi-Fi.
- Tablet joins local Wi-Fi.
- Mac joins local Wi-Fi.
- X18 USB audio connects directly to the Mac.
- External USB recording storage connects to the Mac.
- Showco runs on the Mac and is opened from the tablet.
- Recs runs on the Mac.
- Twitcho runs on the Mac, if testing Twitch.

This does not test the Pi access point, Pi USB/storage limits, or Pi systemd
startup. It does test the actual mixer, tablet UI, audio device, recording
behavior, storage device, Twitcho control, Twitch API actions, and show
workflow.

## 1. Network check

On the Mac:

```bash
ipconfig getifaddr en0
```

Use that IP for Showco:

```text
http://<mac-ip>:17352/
```

From the tablet:

- confirm the X18 mixer app can control the mixer
- open Showco from the Mac IP
- leave both available during the test

Pass condition: tablet can control the mixer and Showco at the same time.

## 2. X18 audio check

Connect X18 USB to the Mac.

Check that the Mac sees the X18 as an audio input. Then run the expected field
`recs` command, adjusted for the Mac device name and output path.

Pass condition:

- Recs sees the X18.
- Recs can record the expected channels.
- Showco displays Recs connected.
- Showco level indicators move when mixer channels receive signal.

## 3. External storage check

Connect the exact USB key or SSD candidate to the Mac.

Record to that device, not internal Mac storage.

Pass condition:

- files are created on the external device
- file sizes grow
- no write-stall or dropout warnings appear
- unplugging is not part of this test unless explicitly testing failure modes

## 4. Showco rehearsal with real services

Run real Recs and real Twitcho if possible, then run Showco normally, not
rehearsal mode:

```bash
cd <root>/showco
uv run showco --host 0.0.0.0 --port 17352
```

From the tablet:

- open Home
- open Actions
- press Recs calibration
- mute and unmute Twitcho
- create marker, chat, and announcement if Twitch credentials are configured

Pass condition: every button gives a clear success or failure and does not hang
the UI.

## 5. Recording test

Do a short structured recording first:

- 2 minutes with obvious signal
- 30 seconds quiet
- 2 minutes with signal again
- press calibration after levels are set
- verify files and manifest afterward

Then do a longer soak:

- at least 1 hour
- ideally 2 to 5 hours if convenient
- tablet stays connected
- mixer app remains usable
- Showco stays open

Pass condition:

- recording survives the full duration
- no Recs warnings about dropped audio or buffer pressure appear
- manifests and session files are readable
- generated audio files are playable

## 6. Twitcho test

If testing real Twitch output:

- start Twitcho with the real stereo mix input
- verify Showco sees it connected
- mute and unmute from Showco
- update stream info
- send chat message
- send announcement
- create marker
- create clip only if the channel is actually live

Pass condition: Twitcho audio time advances, Showco commands return useful
results, and failures are explicit.

If not streaming publicly, still test Twitcho locally up to the point where
`ffmpeg` starts and control commands work, but skip Twitch side effects.

## 7. Tablet usability test

Use the tablet exactly as planned for the show:

- switch between mixer app and Showco
- keep Showco open for an hour
- press buttons with stage-like lighting and positioning
- verify text is large enough
- verify recent action results are visible
- verify no accidental destructive action is too easy

Pass condition: no keyboard, terminal, or Mac interaction is needed once the test
starts.

## 8. Failure-mode tests worth doing on Mac

These are safe and useful:

- stop Recs while Showco is open
- restart Recs and verify Showco recovers
- stop Twitcho while Showco is open
- restart Twitcho and verify Showco recovers
- disconnect internet while recording and verify Recs continues
- make Twitcho credentials invalid and verify Showco shows a clear failure

Avoid power-pull and storage-removal tests until explicitly choosing destructive
or failure testing.

## 9. What this proves

This Mac test proves:

- X18 audio input path works
- Recs field workflow works
- external storage candidate is probably usable
- tablet Showco UI is usable
- Showco talks to Recs and Twitcho
- Twitcho control and Twitch API side effects work
- local Wi-Fi supports tablet, mixer, and Showco together

## 10. What remains unproven until the Pi arrives

Still unproven:

- Pi CPU headroom
- Pi RAM headroom
- Pi USB/storage behavior
- Pi access-point behavior
- Pi Ethernet-to-X18 topology
- systemd startup
- headless boot recovery
- long-term thermal behavior
- whether the exact Pi case and cabling cause physical problems

The Mac rehearsal should leave only those Pi-specific risks.
