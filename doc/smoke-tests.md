# Automated smoke tests

The smoke tests are lightweight checks that the show-control stack still fits
together before real Raspberry Pi hardware is available.

They do not test audio hardware, Twitch, the X18, or the Pi network. They test
the local software contracts that can be verified on a development machine.

## Current automated checks

Run:

```bash
cd ~/code/showco
uv run python -m unittest discover -s test
```

The smoke coverage includes:

- Showco serves the Home screen in rehearsal mode.
- Showco serves the Actions screen after a submitted action.
- rehearsal Recs reports connected, recording, and eighteen channel indicators.
- rehearsal Twitcho supports mute, unmute, stop, and status changes.
- Recs protocol tests verify that real Recs protocol objects accept the commands
  Showco sends.
- Twitcho adapter tests verify status parsing and error reporting.

## Manual rehearsal check

Run:

```bash
cd ~/code/showco
uv run showco --rehearsal --host 127.0.0.1 --port 17352
```

Open:

```text
http://127.0.0.1:17352/
```

Expected result:

- Home shows Recs connected.
- Home shows Twitcho connected.
- Home shows eighteen recording channels.
- Actions page has buttons for Recs calibration and Twitcho/Twitch actions.
- pressing Calibrate noise floor reports a rehearsal success.
- pressing Mute Twitch changes Twitcho status to muted.
- pressing Stop Twitch changes Twitcho status to stopped.

## What these tests intentionally do not prove

These tests do not prove:

- X18 USB audio works on the Pi.
- external USB storage is fast enough.
- the Pi access point is reliable.
- the tablet can reach the Pi at the venue.
- Twitch credentials are valid.
- Twitch accepts live-only commands such as clips and markers.

Those remain hardware and service acceptance tests.
