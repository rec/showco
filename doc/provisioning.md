# Raspberry Pi provisioning

Use `cloud-init` for first-boot provisioning. The goal is to make a freshly
flashed Raspberry Pi OS Lite card boot into a known, repeatable show-box state
with minimal manual work.

Only Markdown documentation belongs in `doc/`. The cloud-init files and setup
script live in `showco/provisioning/`.

## Files

```text
doc/provisioning.md
showco/provisioning/user-data.yml
showco/provisioning/network-config.yml
showco/provisioning/meta-data.yml
showco/provisioning/pi-first-boot.sh
```

## First-boot model

The intended flow is:

1. Flash Raspberry Pi OS Lite.
2. Mount the boot partition on the Mac.
3. Copy or adapt the files from `showco/provisioning/` onto that boot
   partition using cloud-init's expected names:
   - `user-data.yml` -> `user-data`
   - `network-config.yml` -> `network-config`
   - `meta-data.yml` -> `meta-data`
   - `pi-first-boot.sh` -> `pi-first-boot.sh`
4. Edit placeholder values before booting:
   - hostname
   - username
   - password hash or SSH key
   - temporary first-boot Wi-Fi, if used
   - repository URLs or branch names, if needed
5. Boot the Pi.
6. `cloud-init` runs `pi-first-boot.sh`.
7. Reboot once after provisioning.
8. Run the acceptance tests.

The first boot can use a temporary local Wi-Fi client configuration just to get
SSH access. The final field configuration is still the Pi access point plus
wired X18 Ethernet.

## What cloud-init should do

The cloud-init configuration should:

- create the normal show user
- install the SSH key
- enable SSH
- install base packages
- clone or update `recs`, `twitcho`, and `showco`
- install `uv`
- run `uv sync` in each project
- create config and state directories
- leave obvious TODO placeholders where final service commands are not decided

The current files deliberately stop short of installing final systemd services,
because we have not yet committed exact service commands or config-file formats
for all three programs.

## Idempotency

`pi-first-boot.sh` should be safe to rerun. It uses `mkdir -p`, updates existing
git checkouts, and avoids assuming an empty machine.

If a later version installs services, it should continue to be rerunnable:

- write service files deterministically
- run `systemctl --user daemon-reload`
- restart only the services whose files changed
- fail clearly if external storage is missing

## Secrets

Do not commit real secrets.

Do not commit:

- real login password hashes
- private SSH keys
- Twitch stream keys
- Twitch OAuth tokens
- real Wi-Fi passwords, unless the repository is private and the risk is
  accepted deliberately

Use placeholders in committed files and fill real values only on the local SD
card or in a private ignored overlay file.

## Validation before the Pi arrives

The shell script can be reviewed and partially tested on any Linux machine or
container, but it should not be treated as fully validated until it has run on
the actual Raspberry Pi OS image.

Before relying on it:

```bash
bash -n showco/provisioning/pi-first-boot.sh
```

Then, once the Pi arrives:

```bash
sudo cloud-init status --long
journalctl -u cloud-init
```

## Open decisions before service installation

The provisioning files need these final values before they can become a complete
show-box installer:

- final Linux username
- SSH public key
- temporary first-boot network, if any
- final `recs` daemon install command
- final `twitcho` command or config path
- final `showco` command or config path
- X18 wired Ethernet address
- mixer probe port and protocol
- external recording mount point
