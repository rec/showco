# Raspberry Pi provisioning

Provisioning starts from a Raspberry Pi that already boots, has networking, and
accepts SSH login. This can be a freshly imaged Raspberry Pi OS Lite machine or
an existing Pi.

The provisioning script runs setup commands over SSH so progress and failures
are visible from the development machine. `showco image-card` images a fresh SD
card and writes its first-boot cloud-init configuration.

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
web_port = 17352
swap_wifi = false
topology = ""

[networks.internal.wired.x18]
name = "x18"
ip_address = "10.43.0.18"
subnet = "10.43.0.0/24"

[networks.internal.wifi.private]
name = "showbox"
ip_address = "10.42.0.1"
dhcp_start = "10.42.0.50"
dhcp_end = "10.42.0.200"

[networks.external.wifi.external]
name = "Venue WiFi"

[twitch]
enabled = false

[git.recs]
url = "https://github.com/rec/recs.git"

[git.twitcho]
url = "https://github.com/rec/twitcho.git"

[git.showco]
url = "https://github.com/rec/showco.git"
```

If `network.user` is omitted, the provisioning script uses the local `USER`
environment variable. It is an error if neither is set. The SSH port defaults to
22; override it with `showco provision --port 2222` when needed.

Set the Raspberry Pi hostname in Raspberry Pi Imager before first boot. Use that
same name for `network.host`, including `.local` when connecting by mDNS.

`showco/provision/secrets.toml` contains secret operational values. Provisioning
uses key-based SSH only.

Wi-Fi passwords belong in `showco/provision/secrets.toml`:

```toml
[networks.internal.wifi.private]
password = "..."

[networks.external.wifi.external]
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

When `[networks.internal.wired.x18]` is present, the network tool also
configures the Pi Ethernet jack as the X18 control link. It uses the first
usable address in `networks.internal.wired.x18.subnet` for the Pi and expects
the X18 at `networks.internal.wired.x18.ip_address`.

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
showco provision
```

Or override the connection on the command line:

```bash
showco provision \
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
showco prepare-card --boot /Volumes/bootfs
```

This updates Raspberry Pi Imager's `user-data` cloud-init file to enable
passwordless `sudo` for the configured Showco user. Provisioning checks this
with `sudo -n` before it runs remote setup, so it cannot block waiting for an
unknown account password.

## Imaging a card

On macOS, use the raw disk reported by `diskutil list` and pass it explicitly:

```bash
showco image-card --device /dev/disk4
```

The command displays `diskutil list /dev/disk4` and requires typing `yes` before
it erases the selected disk. Add `--yes` or `-y` only for deliberate scripted
use. It writes the pinned Raspberry Pi OS Lite image,
then creates `tom` from the provisioning configuration with SSH-key access,
passwordless `sudo`, and the configured external Wi-Fi. It does not store or
enable a login password.

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
- installs and starts the `recs` user service
- installs and starts the `showco` user service
- writes `~/PROVISIONING-NEXT-STEPS.txt`

The script is intended to be rerunnable. Existing git checkouts are updated with
`fetch --all --prune` and `pull --ff-only`.

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

Do not run `showco provision` as a routine verification step. It mutates a
real Raspberry Pi.

## Open decisions

The provisioning flow still needs these final values before it can become a
complete show-box installer:

- final Twitcho config contents
- mixer probe port and protocol
