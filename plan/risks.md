# Showco Reliability Risks

This document records the reliability review and the resulting decisions. It
does not claim that a specific Pi has been tested against every failure mode.

## Resolved risks

- RPC requests have bounded response timeouts. Showco limits concurrent HTTP
  requests and does not retry dependencies inside request handlers.
- Provisioning treats enabled Lyte, Recs, Showco, and Twitcho failures as
  errors. Its post-reboot wait includes Lyte, checks the Showco HTTP revision,
  and checks Twitcho through the same RPC health definition used by the web UI.
- Provisioned Showco probes the X18 OSC endpoint on UDP port 10024 with the
  protocol-defined `/xremote` request.
- The X18 recorder reports child exit status, current log path and size, and
  write errors in the Showco health response. Logs rotate by size and count.
- HTTP action bodies are capped, concurrent requests are bounded, all accepted
  actions are journaled with source and bounded detail, and control actions are
  serialized.
- Recs requires a numeric `updated_at` before it is reported connected. Recs
  track-name updates are serialized within Showco.
- Twitcho distinguishes a reachable daemon from an unhealthy active stream:
  encoder failure, missing audio, stale audio, and daemon-reported stream
  failures are all displayed as errors. Provisioning uses that same health
  check when Twitcho is enabled.
- Network reconfiguration retains a rollback profile until the replacement
  topology is active. Provisioning commands have explicit execution timeouts.
- A changed SSH host key requires the explicit
  `--accept-changed-host-key` option. Autosquash validates fixup targets before
  rebasing and leaves target machines free of history rewriting.
- Provisioning configures persistent journald and verifies that it can write
  and read a journal marker. Operational diagnostics remain available through
  `journalctl` rather than being copied into the polling response.

## Deliberate decisions

- The private Wi-Fi is the Showco control boundary. The web UI has no separate
  browser authentication.
- X18 logs are low priority. Write errors are reported and retried after a
  short delay, but they do not terminate Showco or delete recordings to reclaim
  space.
- X18 recorder restart remains an explicit operator action. There is no
  automatic child restart loop that could repeatedly fail or fill logs.

## Additional work beyond the prompt

None.
