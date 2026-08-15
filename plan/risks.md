# Showco Reliability Risks

This review covers the current Showco checkout and its interactions with Recs,
Twitcho, Lyte, Reccy, systemd, provisioning, and the X18 recorder. It is a
static review only: it does not claim that any specific Pi currently exhibits
these failures.

## Additional work beyond the prompt

None.

## P1: A hung RPC peer can consume HTTP and daemon threads indefinitely

[ShowcoApp.status](../showco/server.py#L41) calls Twitcho RPC for every
`/status` request. [RecsClient.mutable_attributes](../showco/recs.py#L174)
does the same for the initial home page and then makes one more RPC call per
attribute. Reccy's [Client.call](../../reccy/reccy/rpc.py#L44) has no response
deadline: the Unix socket connection timeout is removed immediately after
connecting in [UnixSocketConnection.connect](../../reccy/reccy/ipc.py#L271).
Showco's `ThreadingHTTPServer` and Reccy's RPC server both create a thread per
connection.

One browser does not overlap its own status polls, but a wedged Twitcho/Recs
handler leaves its request thread blocked. Several browser clients, action
requests, or malformed local clients can therefore accumulate blocked threads.
This is especially dangerous if a daemon becomes partly alive: it accepts a
socket but never replies.

Plan:

1. Add a bounded response timeout to Reccy's synchronous RPC client, with a
   clear `TimeoutError` that callers already convert into health/action errors.
2. Put an explicit, small concurrency limit around Showco's status and action
   RPC work so a bad peer cannot create unbounded request threads.
3. Keep retry policy outside request handlers. Report a timed-out dependency as
   unhealthy, and let systemd restart a crashed daemon at its existing
   five-second backoff rather than retrying it in a tight loop.
4. Add tests for a peer that completes the connection handshake but never sends
   a response.

## P1: Provisioning can report success while enabled Lyte is inactive

[verify_lyte_service](../showco/provision/provision.py#L605) converts an
enabled but inactive `lyte.service` into a note with an empty error.
[report_verification_results](../showco/provision/provision.py#L703) prints
`Success!` whenever errors are empty, so that note does not fail provisioning.
The startup retry set also omits Lyte.

This loses a real requested-service failure at the point where provisioning is
supposed to establish a working target.

Plan:

1. Treat an enabled Lyte service that is inactive as an error, not a note.
2. Include it in the startup wait set so a normal post-boot delay is tolerated.
3. Preserve hardware absence as a note only after the daemon itself has passed
   its own health check.
4. Add tests for enabled/inactive, enabled/active, and disabled Lyte.

## P1: Showco's advertised mixer health is never configured by provisioning

[WebUiOptions](../showco/cli.py#L17) accepts `mixer_port`, but
[install_showco_service](../showco/services.py#L67) and
[showco_args](../showco/services.py#L97) never accept or pass it. The
provisioning template therefore installs a service with `--mixer-host` only.
[MixerMonitor.status](../showco/mixer.py#L24) then returns "mixer probe not
configured" because a port is required.

Consequently, a displayed mixer result is not an end-to-end reachability check
on a normally provisioned target.

Plan:

1. Decide the intended probe protocol and port for the X18, and put both in the
   provisioned Showco service arguments.
2. Do not use a UDP probe that sends arbitrary bytes every second unless the
   mixer protocol explicitly defines that probe as harmless.
3. Add post-provision verification that distinguishes an omitted probe from an
   unreachable mixer.

## P1: Showco service checks are not web-UI liveness checks

Provisioning labels
[`systemctl status showco.service`](../showco/provision/provision.py#L537)
as "showco service status is healthy", but it only establishes that systemd can
describe the unit. It does not request `/status`, confirm the expected source
revision, or test the Recs/Twitcho paths used by the page. The updater already
has a stronger [showco_revision_step](../showco/update.py#L319), but
provisioning does not use it.

Plan:

1. Extract one target-side Showco HTTP health command that checks a bounded
   local `/status` request and the expected revision.
2. Use it after service installation, after provisioning reboot, and after an
   update that restarts Showco.
3. Report the failed endpoint or revision, not only the systemd result.

## P1: The X18 recorder can fail silently and its logs grow without bound

[run_web_ui](../showco/cli.py#L39) starts an
[X18RecorderSupervisor](../showco/x18/recorder_supervisor.py#L11) as a child
of Showco. The supervisor exposes no child health, does not capture its stderr,
and does not restart it if it exits. The server can therefore stay healthy while
OSC capture has stopped. Separately,
[X18OscRecorder.run_forever](../showco/x18/osc.py#L165) flushes every incoming
and outgoing datagram to a single JSONL file with no size limit, rotation,
free-space check, or retention policy.

Disk-full or write errors will stop capture, and the only evidence may be in
the Showco journal. Automatically restarting it without a rate limit would risk
repeatedly filling logs and obscuring the underlying error.

Plan:

1. Surface recorder process state, exit status, last write error, current log
   path, and log size in Showco health.
2. Stop cleanly and make the failure visible when available disk space is below
   an explicit threshold; do not silently delete recordings.
3. Define a retention or session-rotation policy before adding recovery.
4. If automatic restart is wanted, use one bounded policy with exponential
   backoff and record every failed attempt. Otherwise require an explicit
   operator restart.

## P1: The web control surface has no authentication or request-size bound

[ShowcoHandler.do_POST](../showco/server.py#L131) accepts control commands from
any client that can reach the HTTP port. The default target service binds to
`0.0.0.0`, and the actions include recording control, Recs shutdown, Twitch
chat, stream control, and Twitch restart. [_form](../showco/server.py#L147)
also trusts `Content-Length` and reads that many bytes without a cap.

The private Wi-Fi may reduce exposure, but it does not distinguish trusted
tablet users from any connected client. A bad client can issue repeated actions
or make Showco allocate a large request body.

Plan:

1. Set an explicit maximum action request size before reading the body.
2. Decide whether private-network membership is an adequate control boundary.
   If not, add a small shared-secret or local reverse-proxy authentication
   boundary before exposing destructive actions.
3. Serialize or deduplicate non-idempotent operations such as shutdown,
   start/stop recording, clip creation, and Twitch markers.
4. Log each accepted action with timestamp, source address, result, and bounded
   diagnostic detail in the system journal.

## P2: Recs can be shown as connected with an unusable status file

[RecsClient.status](../showco/recs.py#L37) treats any JSON object as a valid
status payload. If `updated_at` is absent,
[_connection_state](../showco/recs.py#L498) returns `connected`; `{}` thus
appears healthy despite containing no daemon progress information. Conversely,
the UI only exposes Recs error records and the current GUI IPC error, not the
service's journal or a failure timestamp.

Plan:

1. Validate the minimum status schema needed to call Recs connected, including
   a numeric `updated_at` and expected rows/type fields.
2. Mark missing or malformed required fields as `error`, with a short specific
   message.
3. Include the last successful update time and bounded Recs journal tail in the
   diagnostics path, without placing a large error history in the polling JSON.

## P2: Concurrent track-name edits can overwrite one another

[RecsClient.set_track_name](../showco/recs.py#L112) reads the complete track name
map, modifies it locally, then sends the whole map back. Independent HTTP
requests run concurrently under
[ThreadingHTTPServer](../showco/server.py#L180). Two edits based on the same old
map can each succeed but the later write can discard the earlier edit. The
single-page Save button is serial, but a second browser or an action retry can
still race it.

Plan:

1. Prefer a Recs protocol operation that mutates one track name atomically.
2. Until then, serialize track-name mutations in Showco and return a clear
   conflict/error if the Recs revision changed during the read-modify-write.
3. Add a deterministic two-request race test.

## P2: Twitcho's daemon reachability is conflated with streaming health

[TwitchoClient.status](../showco/twitcho/client.py#L20) calls any valid status
dictionary `connected`, even if `last_error` is populated, `ffmpeg_alive` is
false, audio has stopped advancing, or the stream state reports an error. The
provisioning check likewise verifies service status rather than the Twitcho RPC
status and encoder health.

Plan:

1. Define Twitcho health states separately: daemon reachable, encoder alive,
   audio advancing, and stream state acceptable.
2. Display the failing layer directly in Showco and use the same definition in
   post-provision verification.
3. Add a bounded liveness observation window for audio/bitrate. Do not restart
   Twitcho automatically from a web poll; expose an explicit restart action and
   use systemd's restart backoff for crashes.

## P2: Network reconfiguration has no transactional recovery path

[x18_bridge_command](../showco/network_config.py#L272) deletes existing
NetworkManager connections before creating and activating the bridge and
hotspot. A failure after deletion can remove the only known way to reach the
Pi. Provisioning deliberately runs this step at the end, but its SSH connection
can still disappear before the reboot and verification phase.

Plan:

1. Generate and validate all replacement connection profiles before deleting
   the active private connection.
2. Preserve the active management route until the replacement hotspot and
   bridge are confirmed up.
3. On failure, attempt one explicit rollback to the prior profile and print the
   exact recovery state. Avoid repeated automatic retries of `nmcli`.
4. Add command-order and rollback tests using the existing injected runner.

## P2: Provisioning and verification remote commands have no execution timeout

The SSH connect timeout in [ssh_command](../showco/provision/provision.py#L921)
only bounds connection establishment. [run_command](../showco/provision/provision.py#L939)
does not pass a subprocess timeout, so a connected Pi with a hung command can
block provisioning forever. This differs from the updater, which has command
timeouts.

Plan:

1. Add separate, explicit execution timeouts for ordinary verification,
   repository, package-install, and reboot-wait operations.
2. Preserve long package-install allowances while keeping health checks short.
3. Print the command category and captured output on timeout, then leave the
   Pi untouched rather than issuing recovery operations blindly.

## P3: Automatic SSH host-key deletion weakens host identity verification

[ssh_is_reachable](../showco/provision/provision.py#L410) detects a changed host
key and [remove_known_host](../showco/provision/provision.py#L428) removes the
existing key automatically. This is convenient after reflashing a Pi, but a
network attacker that causes a host-key warning receives the same treatment.

Plan:

1. Limit automatic removal to an explicit reflash/reprovision flag, or require
   the new fingerprint to be printed and confirmed.
2. Keep the current automatic behavior only if the private management network
   is the accepted security boundary and document that tradeoff.

## P3: Autosquash can leave a failed rebase state before an update

[autosquash_program](../showco/update.py#L538) searches a bounded recent window
and rebases from the parent of the oldest `fixup!` commit it finds. If a
fixup's target lies outside that window, was rewritten, or conflicts with later
commits, `git rebase --autosquash` can fail and leave the developer checkout in
an in-progress rebase. This correctly protects the target, but it interrupts
the developer update flow.

Plan:

1. Before rebasing, verify that each discovered fixup target is present in the
   selected rebase range.
2. On rebase failure, print the repository, base commit, and the exact
   `git rebase --abort` recovery command without attempting it automatically.
3. Keep target updates free of autosquash and all history-rewriting operations.

## Suggested Order

1. Bound RPC reads and HTTP/action concurrency, then test a hung peer.
2. Make enabled-service and Showco HTTP verification failures fatal in
   provisioning.
3. Fix the mixer probe installation gap and define Twitcho health semantics.
4. Add X18 recorder health and an explicit storage policy.
5. Harden network transaction, SSH timeout, and host-key behavior.
6. Address concurrent Recs mutations and autosquash diagnostics.
