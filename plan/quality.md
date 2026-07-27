# Showco quality plan

This plan captures code-quality and reliability issues found by static review.
It does not include hardware or live service validation.

## Additional work beyond the prompt

None.

## P1: Network configuration ignores command failures

`showco/network_config.py:86-90` runs each generated `nmcli` command but ignores
the returned status. A failed hotspot, Wi-Fi connect, or disconnect command can
leave the Pi half-configured while `showco run network-config` still exits 0.

Fix:

- Check every non-dry-run command result.
- Stop at the first failure.
- Print the failed command and stderr/stdout in the exit message.
- Add a unit test where the second command fails and `configure_network` exits
  non-zero before running later commands.

## P1: Recs action path can raise instead of returning an action result

`showco/recs.py:87-119` handles the normal GUI protocol but does not convert
metadata parse errors, connection failures, read failures, or validation errors
into `ActionResult(False, ...)`. A malformed daemon metadata file or missing
socket can make an HTTP action handler raise and break the request.

Fix:

- Catch specific expected failures around metadata loading, connection creation,
  reads, writes, and protocol validation.
- Return user-visible `ActionResult` messages for those failures.
- Add focused tests for invalid daemon metadata and connection failure.

## P1: Twitch OAuth HTTP failures can be saved as successful token data

`showco/twitcho/auth.py:92-103` treats any HTTP error body as ordinary response
text because `request_text` returns the body for `HTTPError`. If Twitch returns a
non-JSON body or an error JSON, `exchange_code` may raise while parsing or save
misleading response files without making the HTTP status clear.

Fix:

- Return HTTP status alongside response text, or raise a small typed error with
  status and body.
- Make non-2xx token exchange fail with a clear message after writing the
  response file.
- Catch `json.JSONDecodeError` and report the saved response path.
- Add tests with a fake request function for 400 JSON and non-JSON responses.

## P1: Remote provisioning script is not removed after remote failure

`showco/provision/provision.py:258-265` deletes the local temp script in a
`finally`, but removes `/tmp/showco-provision-pi.sh` only after the remote run
succeeds. A failed provisioning run leaves executable setup material on the Pi.

Fix:

- Track whether the upload succeeded.
- Attempt remote cleanup in a `finally` after upload, even when provisioning
  fails.
- Do not mask the original provisioning failure if cleanup also fails.
- Add tests around injected SSH/SCP runners rather than contacting SSH.

## P1: Provisioning silently ignores configured password when `sshpass` is absent

`showco/provision/provision.py:375-380` uses `sshpass` only when it is present.
If `showco_pi_password` is configured but `sshpass` is missing, the script falls
back to plain `ssh` without warning, which can hang or prompt unexpectedly.

Fix:

- If `showco_pi_password` is set and `sshpass` is missing, exit with a clear
  message before the first SSH command.
- Keep key-based SSH behavior unchanged when no password is configured.
- Add tests for password-without-sshpass and key-based mode.

## P2: Twitcho supervision does not expose process spawn failures very well

`showco/twitcho/supervisor.py:101-131` restarts after `OSError`, but the service
state quickly becomes `restarting` and only later `failed` after the restart
policy is exhausted. For bad config paths or missing executables, the UI may not
immediately show the first actionable failure clearly.

Fix:

- Preserve the most recent spawn failure in `last_error` while restarting.
- Consider exposing the failure count in tests or status text if it helps field
  diagnosis.
- Add tests for repeated `OSError` through the external policy.

## P2: `showco run git-pull` restarts services after failed pulls

`showco/git_pull.py:49-68` always restarts `recs` and `showco` after attempting
all pulls, even when one pull failed. That can restart services onto a mixed set
of old and new checkouts.

Fix:

- Stop before restart if any stop or pull step fails.
- Still print all completed step results.
- Add a test where `twitcho` pull fails and restart commands are not run.

## P2: X18 OSC recorder can die on transient send errors

`showco/x18/osc.py:151-172` catches receive timeouts, but `send_xremote` can
raise `OSError` from `sock.sendto`. A transient network failure would terminate
the recorder process instead of writing an error record and retrying later.

Fix:

- Catch `OSError` around outgoing subscription sends.
- Write a JSONL error record with direction `out`, target, and the exception
  message.
- Continue the loop and retry on the next subscription interval.
- Add a test with a fake socket or a small injected send function.

## P2: X18 recorder subprocess state is not cleared after close

`showco/x18/recorder_supervisor.py:24-38` terminates or kills the subprocess but
leaves `self.process` pointing at the finished process. Repeated start and close
cycles still work because `poll()` is checked, but status/debugging would see a
stale process object.

Fix:

- Set `self.process = None` after successful termination or kill.
- Add a small unit test for close clearing the process reference.

## P2: Recs status file reads can raise on ordinary filesystem races

`showco/recs.py:35-54` handles missing files and invalid JSON, but not `OSError`
from a status file disappearing, being unreadable, or being replaced while it is
being read. That can turn a transient daemon write race into a web request
exception.

Fix:

- Catch `OSError` around `read_text`.
- Return `ServiceStatus("recs", "error", ...)` with the OS error text.
- Add a test where the injected path raises on read.

## P3: Network interface parsing assumes unescaped colon-separated `nmcli` output

`showco/network_config.py:120-131` splits `nmcli -t` output with `line.split(":")`.
NetworkManager's terse output can escape delimiters in some fields. Device names
are unlikely to contain colons, so this is lower risk, but the parsing is still
ad hoc.

Fix:

- Either request only fields whose values are safe in this context and document
  that assumption, or parse escaped `nmcli -t` output.
- Add a regression test for escaped delimiter handling if parsing is improved.

## P3: Twitch auth rewrites TOML with line-based string replacement

`showco/twitcho/auth.py:165-174` updates `twitch_state` by replacing any line
starting with `twitch_state =`. This is simple, but it does not preserve comments
near that setting and can miss whitespace variants.

Fix:

- Keep the current approach if config stays flat and simple.
- If this file grows sections or comments that matter, switch to a small
  key-update helper that recognizes leading whitespace and preserves the rest of
  the file more deliberately.

## P3: Server action log is mutated from threaded handlers without a lock

`showco/server.py:44-57` prepends to `action_log` from request handlers running
under `ThreadingHTTPServer`. Concurrent action posts can race and lose a recent
action result.

Fix:

- Add a `threading.Lock` around action execution and log mutation, or just around
  `action_log` if actions should still run concurrently.
- Add a focused unit test for log truncation behavior. True concurrency testing
  is probably not necessary unless this becomes a recurring bug.

## Suggested order

1. Fix command-failure handling in network configuration.
2. Harden Recs action/status exception handling.
3. Harden Twitch OAuth error handling.
4. Make provisioning cleanup and password behavior explicit.
5. Address the service-supervision and X18 recorder resilience items.
6. Leave P3 cleanup until it blocks adjacent work.
