# Handover

## Current Field Observations

- The target has detected the mixer over USB as ALSA card `X18`; `lsusb` reports
  Behringer vendor/product ID `1397:00d4`.
- The X18 Ethernet link is physically switch-controlled. After changing that
  switch, the mixer must be power-cycled. Verify the physical link with
  `cat /sys/class/net/eth0/carrier`; it must be `1` before diagnosing routing or
  tablet reachability.
- The tablet previously received the address `10.43.0.215` from the private
  show network. It must be able to reach the mixer directly before relying on
  its mixer-control application.

## Recording Verification Is Outstanding

On 17 August 2026, Recs reported a live recording with nine files and roughly
5.3 GiB recorded, while the configured output directory and its parent appeared
empty on the target. The target remained stable throughout this observation;
do not attribute it to rebooting, remounting, or a transient loss of the
recording disk without evidence.

This discrepancy has not been diagnosed. Treat successful end-to-end recording
persistence as unverified until a short recording creates and grows actual audio
files on the intended external storage, and those files remain after stopping
Recs.

## Before Live Use

Run the storage, Recs-recording, X18, tablet, and field-length checks in
`doc/acceptance-tests.md`. Do not use the Recs status counters as the only
evidence that audio is being persisted.
