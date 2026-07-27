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
showco/scripts/config.toml
showco/scripts/secrets.toml
showco/scripts/provision-pi.py
```

## Configuration

The script reads defaults from:

- `scripts/config.toml`
- `scripts/secrets.toml`

`scripts/config.toml` contains non-secret operational values, including:

```toml
swap_wifi = false
network_topology = ""
twitcho_enabled = false
private_wifi_ssid = "showbox"
external_wifi_ssid = ""
SHOWCO_PI_HOST = "recs-stage.local"
SHOWCO_PI_USER = "tom"
SHOWCO_PI_SSH_PORT = "22"
RECS_REPO = "git@github.com:rec/recs.git"
TWITCHO_REPO = "git@github.com:rec/twitcho.git"
SHOWCO_REPO = "git@github.com:rec/showco.git"
```

`scripts/secrets.toml` contains secret operational values. `SHOWCO_PI_PASSWORD`
is optional. Key-based SSH is preferred; if `sshpass` is already installed and
`SHOWCO_PI_PASSWORD` is set, the script can use it. Otherwise SSH may prompt
interactively.

Wi-Fi passwords belong in `scripts/secrets.toml`:

```toml
private_wifi_password = "..."
external_wifi_password = "..."
```

## Network configuration

After provisioning, run the network configuration tool on the Pi:

```bash
showco network-config --dry-run
showco network-config
```

The network tool detects Wi-Fi interfaces with NetworkManager. By default, the
first Wi-Fi interface is primary and an optional second Wi-Fi interface is
secondary. Set `swap_wifi = true` to make the second interface primary when one
is present.

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
showco/scripts/provision-pi.py
```

Or override the connection on the command line:

```bash
showco/scripts/provision-pi.py \
  --host recs-stage.local \
  --user tom \
  --port 22
```

Before running the provisioning script, this should work:

```bash
ssh tom@recs-stage.local
```

## What the script does

The script:

- checks the remote system with `uname`, `id`, `sudo`, and `apt-get`
- copies a temporary provisioning script to the Pi
- installs base packages
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

## What this does not do yet

The current script does not configure the final access point or external
recording mount. Those remain explicit follow-up steps.

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
python -m py_compile showco/scripts/provision-pi.py showco/scripts/twitch-auth.py
```

Do not run `scripts/provision-pi.py` as a routine verification step. It mutates a
real Raspberry Pi.

## Open decisions

The provisioning flow still needs these final values before it can become a
complete show-box installer:

- final Twitcho config contents
- mixer probe port and protocol
- external recording mount point
