# Remaining Showco Reliability Work

## Purpose

This document contains only work that requires physical hardware testing. The
software defects and maintainability work from the original problems audit have
been completed.

## Validate Mixer Probe Semantics

`MixerMonitor.udp_status()` sends `/xremote` and expects an immediate UDP reply.
The X18 uses `/xremote` to maintain its subscription, so lack of an immediate
reply may not mean that the mixer is unreachable.

1. Capture X18 traffic while the recorder is active and compare it with the
   current probe behavior.
2. Decide whether mixer health should mean a successful send, a reply to a
   separate query, or recent recorder feedback observed by Recs.
3. Implement the evidence-based probe and add packet-level unit tests.
4. Preserve the long-lived `waiting` state for hardware that may join later.

## Physical Acceptance

Record the date, deployed revisions, device presence, and result for each run in
`doc/handover.md`.

1. Confirm that Recs writes complete audio to a removable disk and that the
   files remain after recording stops and the volume is remounted. The existing
   status-versus-files discrepancy remains unresolved.
2. Confirm that X18 USB audio, Flow 8 audio and MIDI, and OSC recording recover
   when devices arrive, disconnect, and return at different times.
3. Run the tablet on the private network for an extended session while using
   Health, Actions, and waveform pages.
4. Confirm that `showco go` reports completion only after target service
   verification and leaves sufficient file-log evidence after a failed
   deployment.

## Additional Work Beyond The Prompt

None.
