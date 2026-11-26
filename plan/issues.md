# showCo issues

Keep this file for outstanding findings only. Remove each issue's section in the
commit that completes it rather than retaining resolution notes.

The remaining numbered priorities from the 26 November 2026 audit are P2
and P3. The older operational issues below have their own acceptance criteria.
Findings are based on source and existing tests.

## P2: Waveforms can stop reconnecting after a routine recs busy reply

`WaveformBridge._run()` and `close()` omit `rpc.RemoteError`. A busy or
rejected subscription can exit the worker instead of retrying, and a rejected
unsubscribe can bypass the stop flags and join. `start()` cannot replace the
dead thread because it checks only whether the reference exists. Catch expected
structured errors, publish unavailable state, and make cleanup unconditional.
Test both busy subscription recovery and rejected unsubscribe.

## P2: Closing observation can wait forever with stale operation evidence

The restart deadline for an unconfirmed saved request covers only the initial
reconciliation. After a successful observation, missing or mismatched operation
IDs can still hold the recording-control gate indefinitely while `remote`
describes the last successful reply. Black timing uses remote and local
wall-clock timestamps, so a clock step can mistime the fade. Bound sustained
loss of observation and use elapsed or monotonic timing where the protocol
permits. Test streamO restart and wall-clock steps during credits.

## P2: Music decoder and audio I/O failures can leave a playing status behind

`MusicPlayer._play_track()` blocks on a pipe read or PortAudio write, and
`MusicPlayer.stop()` only terminates FFmpeg before joining. Killing a decoder
does not unblock an audio write. FFmpeg stderr is discarded, and a partial
final sample frame raises `ValueError` outside the worker's handled exceptions.
A skipped track is represented as playing and does not become an active fault.
Bound and report decoder/output failure, retain a short diagnostic, and test a
short read, stalled device and mixed valid/invalid playlist.

## P2: Recording destination warning is separate from readiness

`RecsStatus.destination_text` flags a system-disk destination on Show and
Performance. `readiness._disk_check()` can still call the same destination
ready, and a failed filesystem probe returns an unclassified destination
without saying so. The mount rule only initiates the mount; it cannot tell recs
to move a healthy session off its fallback disk. Show expected versus actual
destination in the readiness check without silently changing the recording
destination or splitting a session. Add missing, late-mounted, wrong-volume,
and full-drive cases, including automount behavior on the Pi.

## P2: The Show page lacks per-input recording evidence

The Show page reports recorder faults and aggregate write progress. It still
cannot show whether each required input is delivering fresh samples, being
silence-filtered, or writing to the current session. Expose that evidence
through recs' public snapshot and retain bounded failure summaries with session
identity and destination. Test that continued writes on one input cannot hide
a failure on another.

## P2: Soundcheck can credit a different recording from the sample it started

`Soundcheck.record_start()` saves an aggregate recorded-seconds baseline but
no session or per-input identity. `Soundcheck.check()` can count growth from
another input or a replacement session on the same disk. Its playback check
records the operator's attestation without knowing which session was played.
Bind the sample evidence to the session and selected inputs, and keep the
operator's playback confirmation explicit. Test a new session and loss of
the main instrument while another channel keeps writing.

## P2: Monitoring reacts to missed deadlines by catching up at full speed

`PerformanceMonitor._run()` increments an absolute sample deadline even if
`sample()` takes multiple periods. When a probe recovers, the monitor can
perform back-to-back work against daemons the panel needs. Samples are grouped
by wall-clock minute. A clock step can distort aggregation and delay pruning.
Incident writes have their own worker, but a full disk can still trigger a
retry on each fresh observation. Skip expired sample periods, bound sample
age/count, and pace retries for failed writes. Exercise a long probe, clock
correction, and slow or full disk.

## P2: Waveform and HTTP bounds do not fully bound live resource use

`WaveformBridge` caps batches per source and total replay events but does not
retire inactive source keys until reconnect. The server subscribes even with
no waveform viewer and serializes the same data separately for every SSE
client. Browser history is pruned using incoming timestamps, not a bucket
limit. `ShowcoServer.process_request()` also reserves connections before it
knows whether they are status or waveform requests; socket read timeouts are
inactivity timeouts, so slow input can hold those shared slots longer than
expected. Establish budgets for sources, bytes, polling, and absolute request
time. Test reconnect churn, slow readers and multiple tabs rather than just
semaphore counts.

## P2: Reports traverse and retain more recording history than they need

`recording_roots()` can supply overlapping directories, and a configured path
that starts with a dynamic component can produce a broad search root.
`recording_sessions()` recursively discovers all journals and loads each in
full before selecting up to ten qualifying shows. Running a report next to
recording can compete for storage bandwidth and RAM; the SSH timeout limits
waiting time, not peak memory or directory reads. Bound discovery and journal
work, avoid repeated walks, and report when coverage is incomplete. Keep the
existing qualification rules while exposing loss evidence from sessions that
never managed a qualifying write in a separate diagnostic path. When no show
qualifies, report the scanned roots and why candidates were excluded.

## P2: Live diagnostic collection can fail on normal file churn

`bundle.sources()` builds a path list, then `create_bundle()` stats and opens
each path later. A rotated log or unmounted disk can abort the entire bundle.
HTTP collection also compresses a temporary archive on the Pi, and CLI bundles
have no accumulated retention limit. Keep the per-bundle limits, record missing
or changed files in the manifest, and bound repeat archive creation and storage.
Test rotation between enumeration and open, an unavailable recording disk,
and bounded work alongside live capture.

## P2: Performance actions lose the distinction between rejection and uncertainty

`RecsClient._control_command()` and `StreamoClient.action()` collapse
structured outcomes into plain failure messages. Once a mutation was sent,
a missing result can mean the operation still happened. Preserve
`not_started` versus `unknown`, show what to inspect before retrying, and log
the correlation ID when present. Test calibration deadlines, new session,
and broadcast start/stop without resending the operation.

## P2: Fresh install and target update differ for a new lyte dependency

`templates/main.sh` installs the Git URL rewrite needed for tinkly's SSH Git
URL on the Pi. `target_update.install_revisions()` uses the lock directly.
An older target without the rewrite or GitHub credentials can discover this
only after services have been stopped. Verify package acquisition before
stopping services and test the transition from a pre-tinkly target.

## P2: Resource evidence is still missing at show load

The private ALSA/encoder diagnostics in `scripts/pi-capture-headroom.py` and
`pi-streamo-loopback.py` give useful local evidence, but the guard applies only
to the private test and its tests exercise the decision function, not the
transient service's cleanup or a real USB path. Build an acceptance run with
all intended X18 channels and the main instrument, the USB recording drive,
the showbox adapter, and the real stream media and encoding profile. Measure
ALSA queue headroom and loss, CPU throttling, USB resets, disk latency and
free space, network delivery, and panel responsiveness. Include disk
disconnect, mixer restart, and uplink loss. Advance or pause nonessential work
only after measuring which resource actually limits capture.

## P3: Names, boundaries, and duplicate assertions obscure operator behavior

- `readiness.status()` is a service checklist, while “Recording” means
  enabled and “Audio writes” means aggregate progress. Use names that distinguish
  armed state, actual writes, per-input evidence, and intended storage.
- “Reusing verified media package” describes both metadata and content
  verification. Say whether the bytes were checked.
- `music-stop` powers off the Pi, unlike the player and streamO stop actions.
  Keep the poweroff consequence clear in the action and diagnostic naming.
- `test_recs.py` and `test_recs_snapshot.py` both assert most of the same
  successful snapshot fields. Keep one wire-contract test and a narrower
  adapter test for track and totals mapping; retain distinct stale, malformed
  and recovery cases. Prefer testing operator outcomes over exact section
  indexes or shell text when the behavior can be checked directly.
- The main gaps are composed failure sequences: clock jump plus slow RPC,
  panel/mixer restart while recording, recs disk fallback then hotplug, real
  resource contention, and interrupted media or service replacement. The
  existing browser VM tests check behavior and presentation parity but cannot
  establish operator visibility or recovery timing on an actual show network.

## streamO publishes automatically at boot without ready audio

The device-free October 21 boot created an unwanted short Twitch broadcast.
Normal updates preserve an intentionally stopped service. Enabled provisioning
can still enable and start streamO, and Start is not gated by fresh recording,
audio, and clock evidence. The manual-start lifecycle and three-attempt
recovery policy are in [streamo-startup.md](streamo-startup.md).

## Slow Pi connections over showbox Wi-Fi

The original slowdown occurred on **showbox Wi-Fi**. The fast October 21
measurements used **house Wi-Fi**. On October 25, the Mac initially retained
its house-network address after a restart. Once it obtained the showbox address,
SSH to both `10.0.0.1` and `t.local` took less than 0.4 seconds. The
intermittent slowdown remains unexplained. If it recurs, distinguish Wi-Fi and
DHCP readiness from mDNS lookup and SSH connection time.

## Intended behavior and scope

Cable testing is a separate utility, not an expected runtime workflow. Its
results do not need to become live-show faults.

`showco-errors.txt` intentionally describes exactly the last invocation. Starting
another invocation replaces the previous diagnostic; retaining failure history
across invocations is not a requirement.

Automated verification does not replace physical acceptance of Pi networking,
X18 routing, audio output, or broadcast delivery. Those checks remain in the
existing hardware plans.

## Additional work beyond the prompt

None.
