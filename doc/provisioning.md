# Raspberry Pi provisioning

Provisioning starts from a Raspberry Pi that already boots, has networking, and
accepts SSH login. This can be a freshly imaged Raspberry Pi OS Lite machine or
an existing Pi.

The provisioning script runs setup commands over SSH so progress and failures
are visible from the development machine.

## Files

```text
doc/provisioning.md
showco/provision/config.toml
showco/provision/secrets.toml
showco/provision/provision.py
```

## Configuration

The script reads defaults from:

- `showco/provision/config.toml`
- `showco/provision/secrets.toml`

`showco/provision/config.toml` contains non-secret operational values, including:

```toml
[network]
host = "recs-stage.local"
web_port = 10000
swap_wifi = false
topology = ""

[networks.internal]
subnet = "10.0.0.0/24"

[networks.internal.wifi]
name = "showbox"
ip_address = 1

[networks.external.wifi]
name = "Venue WiFi"

[[mixers]]
name = "X18"
ip_address = 18
port = 10024

[mixers.probe]
protocol = "udp"

[mixers.osc]
subscription_path = "/xremote"
resubscribe_period = 10

[twitch]
enabled = false
```

Each repository defaults to `https://github.com/rec/NAME.git`; add a
`[git.NAME]` table only to override that location or refname.

If `network.user` is omitted, the provisioning script uses the local `USER`
environment variable. It is an error if neither is set. The SSH port defaults to
22; override it with `showco provision --port 2222` when needed.

Set the Raspberry Pi hostname in Raspberry Pi Imager before first boot. Use that
same name for `network.host`, including `.local` when connecting by mDNS.

`showco/provision/secrets.toml` contains secret operational values. Provisioning
uses key-based SSH only.

Wi-Fi passwords belong in `showco/provision/secrets.toml`:

```toml
[networks.internal.wifi]
password = "..."

[networks.external.wifi]
password = "..."
```

## Network configuration

After provisioning, run the network configuration tool on the Pi:

```bash
showco run network-config --dry-run
showco run network-config
```

The network tool detects Wi-Fi interfaces with NetworkManager. By default, the
first Wi-Fi interface is primary and an optional second Wi-Fi interface is
secondary. Set `network.swap_wifi = true` to make the second interface primary
when one is present.

`networks.internal.subnet` defines the show network. `ip_address` values for
the private Wi-Fi and mixers are host-number offsets within that subnet. A
mixer is networked only when it defines both `ip_address` and `port`; defining
only one is an error. A networked mixer named `X18` makes the network tool
configure the Pi Ethernet jack as the X18 control link.

`network.topology` may be empty, `public`, `private`, or `mixed`:

- `public`: the primary Wi-Fi connects to the external network; secondary Wi-Fi
  is disconnected.
- `private`: the primary Wi-Fi provides the show network for the tablet and X18;
  secondary Wi-Fi is disconnected.
- `mixed`: the primary Wi-Fi provides the show network, and the secondary Wi-Fi
  connects to the external network.

When `network.topology` is empty, the tool selects it from the configured
external network, second Wi-Fi presence, and `twitch.enabled`.

## Single command

After confirming SSH works, run:

```bash
showco go
```

`showco go` provisions when the target has no applied configuration fingerprint,
or when the resolved local configuration or provisioning script has changed. When
the fingerprint matches, it performs `showco update` instead. The target records
only the fingerprint after a successful provision and verification; it never
stores configuration or secrets in that marker.

Use `showco provision` to force provisioning, including `--system` for an APT
refresh. Override the connection on either command line:

```bash
showco go \
  --host bertrand.local \
  --port 22
```

Before running the provisioning script, this should work:

```bash
ssh "$USER@recs-stage.local"
```

Before the Pi's first boot, prepare its newly written boot volume from the
developer machine:

```bash
showco prepare-card
```

This updates Raspberry Pi Imager's `user-data` cloud-init file to enable
passwordless `sudo` for the configured Showco user. It selects a single mounted
external physical disk of 256 GiB or smaller and requires confirmation before
changing it. Use `--card /dev/disk4` when more than one card is present.
Provisioning checks this with `sudo -n` before it runs remote setup, so it
cannot block waiting for an unknown account password.

## What the script does

The script:

- checks the remote system with `uname`, `id`, `sudo`, and `apt-get`
- copies a temporary provisioning script to the Pi
- installs base packages
- configures `en_US.UTF-8` as the system locale
- installs `uv` for the configured user if needed
- creates code, config, state, and recording directories
- clones or updates `reccy`, `recs`, `twitcho`, `lyte`, and `showco` from public HTTPS URLs
- runs `uv sync` in each checkout
- enables lingering for the configured user so user services start at boot
- installs or refreshes the `recs` user service
- installs or refreshes the `showco` user service
- writes `~/PROVISIONING-NEXT-STEPS.txt`

The script is intended to be rerunnable. Existing git checkouts fetch their
tracked upstream branch and reset to it.

On an already provisioned target, it skips unchanged base-package setup, locale
and journal configuration, the Lyte Python installation, and unchanged active
service definitions. It prints the duration of every phase. Use
`showco provision --system` when you specifically want to refresh APT packages;
ordinary provisioning installs missing packages but does not perform an APT
upgrade.

## Secrets

Do not commit real secrets to a pushable branch.

Do not publish:

- real login passwords
- private SSH keys
- Twitch stream keys
- Twitch OAuth tokens
- real Wi-Fi passwords, unless the repository is private and the risk is
  accepted deliberately

Use placeholders on shared branches. Keep real values only on the local machine,
in a private ignored overlay file, or on a private/unpushable branch used for
local builds.

## Validation

Before relying on script changes:

```bash
python -m py_compile showco/provision/provision.py showco/twitcho/auth.py
```

Do not run `showco provision` or `showco go` as a routine verification step.
They mutate a real Raspberry Pi when provisioning is needed.

## Open decisions

The provisioning flow still needs these final values before it can become a
complete show-box installer:

- final Twitcho config contents
- mixer probe port and protocol
