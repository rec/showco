# Raspberry Pi provisioning

Provisioning starts from a Raspberry Pi that already boots, has networking, and
accepts SSH login. This can be a freshly imaged Raspberry Pi OS Lite machine or
an existing Pi.

The provisioning script runs setup commands over SSH so progress and failures
are visible from the development machine. It does not write cloud-init files to
an SD card.

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
swap_wifi = false
is_x18_wired = true
network_topology = ""
twitcho_enabled = false
private_wifi_ssid = "showbox"
external_wifi_ssid = ""
showco_pi_host = "recs-stage.local"
showco_pi_ssh_port = "22"
showco_pi_x18_ethernet_subnet = "10.43.0.0/24"
showco_x18_wired_ethernet_ip_address = "10.43.0.18"
recs_repo = "git@github.com:rec/recs.git"
twitcho_repo = "git@github.com:rec/twitcho.git"
showco_repo = "git@github.com:rec/showco.git"
```

If `showco_pi_user` is omitted, the provisioning script uses the local `USER`
environment variable. It is an error if neither is set.

Set the Raspberry Pi hostname in Raspberry Pi Imager before first boot. Use that
same name for `showco_pi_host`, including `.local` when connecting by mDNS.

`showco/provision/secrets.toml` contains secret operational values. Provisioning
uses key-based SSH only.

Wi-Fi passwords belong in `showco/provision/secrets.toml`:

```toml
private_wifi_password = "..."
external_wifi_password = "..."
```

## Network configuration

After provisioning, run the network configuration tool on the Pi:

```bash
showco run network-config --dry-run
showco run network-config
```

The network tool detects Wi-Fi interfaces with NetworkManager. By default, the
first Wi-Fi interface is primary and an optional second Wi-Fi interface is
secondary. Set `swap_wifi = true` to make the second interface primary when one
is present.

When `is_x18_wired = true`, the network tool also configures the Pi Ethernet
jack as the X18 control link. It uses the first usable address in
`showco_pi_x18_ethernet_subnet` for the Pi and expects the X18 at
`showco_x18_wired_ethernet_ip_address`.

`network_topology` may be empty, `public`, `private`, or `mixed`:

- `public`: the primary Wi-Fi connects to the external network; secondary Wi-Fi
  is disconnected.
- `private`: the primary Wi-Fi provides the show network for the tablet and X18;
  secondary Wi-Fi is disconnected.
- `mixed`: the primary Wi-Fi provides the show network, and the secondary Wi-Fi
  connects to the external network.

When `network_topology` is empty, the tool selects it from the configured
external network, second Wi-Fi presence, and `twitcho_enabled`.

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

## What the script does

The script:

- checks the remote system with `uname`, `id`, `sudo`, and `apt-get`
- copies a temporary provisioning script to the Pi
- installs base packages
- configures `en_US.UTF-8` as the system locale
- installs `uv` for the configured user if needed
- creates code, config, state, and recording directories
- clones or updates `recs`, `twitcho`, and `showco`
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
