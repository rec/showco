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
showco/scripts/config.env
showco/scripts/secrets.env
showco/scripts/provision-pi.sh
```

## Configuration

The script reads defaults from:

- `scripts/config.env`
- `scripts/secrets.env`

`scripts/config.env` contains non-secret operational values, including:

```bash
SHOWCO_PI_HOST="recs-stage.local"
SHOWCO_PI_USER="show"
SHOWCO_PI_SSH_PORT="22"
RECS_REPO="git@github.com:rec/recs.git"
TWITCHO_REPO="git@github.com:rec/twitcho.git"
SHOWCO_REPO="git@github.com:rec/showco.git"
```

`scripts/secrets.env` contains secret operational values. `SHOWCO_PI_PASSWORD`
is optional. Key-based SSH is preferred; if `sshpass` is already installed and
`SHOWCO_PI_PASSWORD` is set, the script can use it. Otherwise SSH may prompt
interactively.

## Single command

After confirming SSH works, run:

```bash
showco/scripts/provision-pi.sh
```

Or override the connection on the command line:

```bash
showco/scripts/provision-pi.sh \
  --host recs-stage.local \
  --user show \
  --port 22
```

Before running the provisioning script, this should work:

```bash
ssh show@recs-stage.local
```

## What the script does

The script:

- checks the remote system with `uname`, `id`, `sudo`, and `apt-get`
- copies a temporary provisioning script to the Pi
- installs base packages
- installs `uv` for the show user if needed
- creates code, config, state, and recording directories
- clones or updates `recs`, `twitcho`, and `showco`
- runs `uv sync` in each checkout
- writes `~/PROVISIONING-NEXT-STEPS.txt`

The script is intended to be rerunnable. Existing git checkouts are updated with
`fetch --all --prune` and `pull --ff-only`.

## What this does not do yet

The current script deliberately stops short of installing final systemd
services, because exact service commands and config-file formats are not fully
settled.

It also does not configure the final access point or external recording mount.
Those remain explicit follow-up steps.

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
bash -n showco/scripts/provision-pi.sh
bash -n showco/scripts/1-authorize-url.sh
bash -n showco/scripts/2-exchange-code.sh
bash -n showco/scripts/3-validate-token.sh
```

Do not run `scripts/provision-pi.sh` as a routine verification step. It mutates a
real Raspberry Pi.

## Open decisions before service installation

The provisioning flow needs these final values before it can become a complete
show-box installer:

- final `recs` daemon install command
- final Twitcho config path passed to Showco
- final `showco` command
- X18 wired Ethernet address
- mixer probe port and protocol
- external recording mount point
