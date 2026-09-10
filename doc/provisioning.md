# Provisioning and Updating

Showco provisions a Raspberry Pi that already boots, is reachable over SSH,
and has an account matching the configured user. Provisioning is driven from a
development machine and displays remote progress and failures directly.

## Prerequisites

- Python 3.13 and `uv` on the provisioning machine.
- Local sibling checkouts of `reccy`, `recs`, `streamo`, `lyte`, and `showco`.
- Raspberry Pi OS Lite with the intended hostname and user configured.
- Key-based SSH access to the target.
- Passwordless `sudo` on the target user.

For a new Raspberry Pi Imager card, insert the card while its boot volume is
mounted and run:

```bash
showco prepare-card
```

The command finds external physical disks no larger than 256 GiB. If exactly
one is available it selects it; otherwise pass `--card /dev/diskN`. It prints
`diskutil list` output and requires the exact answer `yes` before adding
passwordless sudo commands to the existing cloud-init `user-data`. It then
ejects the card and tells you when it may be removed. It does not image or mount
the card.

## Configuration

The defaults are read from:

```text
showco/provision/config.toml
showco/provision/secrets.toml
```

The second file recursively overlays the first. Keep non-secret deployment
values in `config.toml` and credentials in the ignored local `secrets.toml`.

A representative non-secret configuration is:

```toml
[network]
host = "bertrand.local"
web_port = 10000
swap_wifi = false
topology = ""

[paths]
root = "/home/tom/code"

[networks.internal]
subnet = "10.0.0.0/24"

[networks.internal.wifi]
name = "showbox"
ip_address = 1

[networks.external.wifi]
name = "Venue WiFi"

[[mixers]]
name = "X18"
audio_device_names = ["X18", "XR18"]
port = 10024
ip_address = 18

[mixers.probe]
protocol = "udp"

[mixers.osc]
subscription_path = "/xremote"
resubscribe_period = 10

[lyte]
enabled = false
daemon_config = "patches/wearable-daemon.toml"

[stream]
enabled = false
```

Passwords are overlaid separately:

```toml
[networks.internal.wifi]
password = "..."

[networks.external.wifi]
password = "..."
```

## Streamo migration

When Streamo streaming is enabled, provisioning replaces the Twitcho service
with Streamo. If the target has no
`~/.config/streamo/config.toml` but still has Twitcho's
`~/.config/twitcho/config.json`, it converts the old JSON configuration to the
explicit Streamo TOML profile and leaves the old configuration in place. It
then installs `streamo.service`; the old `twitcho.service` is stopped and
uninstalled.

The generated Streamo configuration retains the former device, channel,
overlay, ingest, credentials, and encoding settings. It uses explicit AAC,
H.264, FLV, stereo, and two-second keyframe settings to reproduce Twitcho's
defaults. New Streamo installations require
`~/.config/streamo/config.toml` before streaming can be enabled.

Important configuration rules:

- `network.user` defaults to the provisioning machine's `USER`; `network.host`
  is required and `network.ssh_port` defaults to 22.
- `paths.root` must be absolute and defaults to `/home/USER/code`.
- `networks.internal.subnet` is required and must be an IPv4 subnet.
- Wi-Fi and mixer `ip_address` values are integer host offsets in that subnet.
  With `10.0.0.0/24`, offsets `1` and `18` become `10.0.0.1` and `10.0.0.18`.
- A networked mixer must define both `ip_address` and `port`, or neither.
- Mixer audio and MIDI name lists are prefixes matched against Recs status.
- Each repository defaults to `https://github.com/rec/NAME.git`. Add a
  `[git.NAME]` table only to override `url` or `refname`.
- `accept_changed_host_key` defaults to true. Provisioning removes the old
  local `known_hosts` entry before connecting to a newly imaged target.
- The external Wi-Fi name and a valid 8-63 character private Wi-Fi password are
  currently required by provisioning validation.
- `lyte.daemon_config` is relative to the Lyte checkout and must exist whenever
  Lyte is enabled.

Command-line `--host`, `--user`, `--port`, and `--root` override the resolved
configuration. During provisioning, host and root overrides are also written
back to the selected non-secret config file after local preflight succeeds.

## Network topologies

`network.topology` accepts `public`, `private`, `mixed`, or an empty string:

- `public`: primary Wi-Fi joins the external network; Ethernet uses the
  internal mixer subnet when an X18 is configured.
- `private`: primary Wi-Fi provides the internal access point; Ethernet is
  bridged into it when an X18 is configured.
- `mixed`: primary Wi-Fi provides the internal bridged network and secondary
  Wi-Fi joins the external network.

When the value is empty, Showco chooses from external-network configuration,
second-interface availability, and whether Stream is enabled. `swap_wifi`
reverses the first two detected Wi-Fi interfaces.

Provisioning generates temporary config and secret files on the target and
runs `showco run network-config` itself. It hashes the effective network input
and skips NetworkManager changes when that hash is unchanged. Network changes
use a temporary rollback connection so a failed command can restore the prior
private access point.

## Default command

Run:

```bash
showco
```

This is equivalent to `showco go`. Showco compares the local provisioning
fingerprint with `~/.local/state/showco/provisioning-fingerprint` on the target.
It provisions when the fingerprint is absent or different and otherwise runs a
normal update. The fingerprint is written only after provisioning and target
verification succeed.

Use `showco go --system` to force provisioning and request APT update and
upgrade work. The first provisioning also runs an APT upgrade; ordinary reruns
install missing packages without repeating the full upgrade.

Provisioning:

1. Validates configuration and local repository state.
2. Publishes all five repositories and refreshes their internal lockfiles.
3. Removes a stale SSH host key when configured and waits for SSH.
4. Requires `sudo -n` to succeed and rejects dirty target checkouts.
5. Checks the available Wi-Fi interfaces and proposed topology.
6. Uploads and runs a generated Bash script with a 30-minute timeout.
7. Configures locale, persistent journal storage, base packages, mount rules,
   uv, shared Python 3.13, repositories, networking, and enabled services.
8. Writes `~/PROVISIONING-REPORT.txt` and
   `~/PROVISIONING-NEXT-STEPS.txt` on the target.
9. Reboots only when `/var/run/reboot-required` was present.
10. Verifies enabled services, Showco's HTTP revision, Recs status progress,
    and configured audio/MIDI devices.

## Updating

Passing repository names or `--autosquash` selects update mode:

```bash
showco go recs
showco go reccy showco
showco go --autosquash 20
```

An empty repository list means all five repositories. Selection expands to
downstream consumers so internal lockfiles cannot remain pinned to an old
dependency. Selecting Reccy includes every repository; selecting Recs includes
Showco.

A normal update checks main branches and clean worktrees, autosquashes recent
fixup commits, pushes local histories, and refreshes internal Git dependencies
in lockfiles. Repositories with internal dependencies run their locked test
suites before generated lockfile changes are committed and pushed. The target
is then updated. Target checkouts are reset to their upstream commits and are
treated as disposable deployment copies. Local development checkouts are never
reset.

Use direct remote mode when the required commits are already on GitHub:

```bash
showco go --remote [repository ...]
```

This skips all local repository checks and publication. It updates the target
from GitHub and does not pass `--remote` into the target command.

Local-only publication modes are:

```bash
showco --push [repository ...]
showco --sync [repository ...]
```

`--push` checks, autosquashes, and publishes selected repositories without
fetching when local and upstream commits already match. `--sync` performs that
work and then refreshes, tests, commits, and publishes internal lockfile changes.
Neither contacts the target.

Updates clear `~/.config/recs/settings.json` by default so mutable web settings,
track names, and stereo groups return to deployed Recs configuration. Pass
`--no-clear-settings` to preserve them for one update.

## Diagnostics

Fetch combined service logs from the provisioning machine:

```bash
showco logs
showco logs recs showco --lines 500
```

Known services are `showco`, `recs`, `streamo`, and `lyte`. Their files are
`~/.local/state/SERVICE/SERVICE.log` on the target. A missing service log is
reported by `tail`; it is not silently ignored.

For a small target-side Python inspection without opening an interactive SSH
session:

```bash
showco python 'import sys; print(sys.version)'
```

This executes `.venv/bin/python -c` in the target Showco checkout.

Do not use `showco go` as a test command: it can rewrite local history, publish
repositories, reset target checkouts, clear Recs settings, restart services,
change networking, or provision the operating system.

## Secrets

Never commit or publish login passwords, private SSH keys, Wi-Fi passwords,
Stream stream keys, OAuth tokens, or client secrets. Provisioning shell
arguments necessarily carry resolved secrets to the target process, but the
generated files are mode `0600` and temporary network files are removed after
use.
