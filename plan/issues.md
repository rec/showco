# showCo issue audit

This is a static audit of the current showCo checkout on 2026-09-29. It covers
the runtime, browser code, X18 audio and OSC paths, provisioning, deployment,
configuration, documentation, and tests. **Confirmed** means the code path has
the stated behavior. **Risk** means the failure is plausible but needs a
targeted test or physical observation. Priorities reflect possible impact
during a show, not implementation effort. No service, deployment, or hardware
flow was run for this audit. The existing physical checks remain in
[hardware.md](hardware.md).

## P2: operator clarity, edge cases, and maintainability

25. **A stale browser edit can overwrite newer recs track names (risk).**
    `set_track_name()` fetches the current name map and replaces it, but has
    no revision or compare-and-swap with recs
    (`showco/runtime/recs.py:174-218`). Its local lock protects only this
    showCo process; another client can change names between the read and
    write. The set-list and lighting editors already reject stale revisions.
    Seek a recs-side conditional update or make the potential overwrite clear.

## Additional work beyond the prompt

None.

## Hardware verification required

1. **P0: The private-network ingress policy is implemented but not verified on the Pi.**
   Provisioning installs a persistent nftables policy when
   `network.restrict_external_ingress = true`. It admits traffic from the Pi's
   private hotspot, while the external interface admits only SSH and UDP 5353
   for `t.local` mDNS discovery. The `public` topology is rejected because it
   has no private hotspot. The Pi was unavailable for a controlled deployment,
   so this boundary has not been proven in use. Set a long, random password in
   the ignored `showco/provision/secrets.toml`, deploy when no show is running,
   and verify: the UI works on the private network; UI and diagnostics are
   unreachable on external Wi-Fi; SSH and `t.local` work externally; and the
   policy survives a reboot. Keep this finding open until those checks pass.

40. **The physical installation remains unverified.**
    [hardware.md](hardware.md) tracks capture timing, routing isolation,
    signal thresholds, and full Pi/X18 acceptance. These should remain open
    until measured on the actual setup. Automated browser, WAV, and fake OSC
    tests cannot close them.

41. **The closing-credits timing and room routing need live acceptance.**
    streamO and showCo now implement the sequence in
    [closing-credits.md](closing-credits.md), but tests with fake OSC and
    rendered frames cannot prove Twitch delivery or X18 timing. Follow the
    target rehearsal in that plan and record the output and room measurements
    before relying on it during a show.
