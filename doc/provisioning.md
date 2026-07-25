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
showco/provisioning/provision-pi-card.sh
```

## Single command

After flashing Raspberry Pi OS Lite, either pass the mounted boot partition:

```bash
showco/provisioning/provision-pi-card.sh \
  --boot /Volumes/bootfs \
  --ssh-key-file ~/.ssh/id_ed25519.pub \
  --password-hash '$y$j9T$REPLACE_WITH_REAL_PASSWORD_HASH' \
  --wifi-ssid 'REPLACE_WITH_TEMPORARY_FIRST_BOOT_WIFI_SSID' \
  --wifi-password 'REPLACE_WITH_TEMPORARY_FIRST_BOOT_WIFI_PASSWORD'
```

Or pass the imaged SD card disk before mounting it yourself:

```bash
showco/provisioning/provision-pi-card.sh \
  --disk /dev/disk4 \
  --ssh-key-file ~/.ssh/id_ed25519.pub \
  --password-hash '$y$j9T$REPLACE_WITH_REAL_PASSWORD_HASH' \
  --wifi-ssid 'REPLACE_WITH_TEMPORARY_FIRST_BOOT_WIFI_SSID' \
  --wifi-password 'REPLACE_WITH_TEMPORARY_FIRST_BOOT_WIFI_PASSWORD'
```

Use `diskutil list` to identify the SD card disk. The script mounts the disk's
partitions, looks for exactly one Raspberry Pi boot partition, and fails if it
does not find the expected `config.txt` and `cmdline.txt` structure.

The script writes:

- `user-data`
- `network-config`
- `meta-data`
- `pi-first-boot.sh`

to the mounted boot partition using cloud-init's expected filenames.

Then eject the card, boot the Pi, wait for cloud-init to finish, reboot once,
and run the acceptance tests.

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
because we have not yet committed exact service commands or config-file formats.

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
bash -n showco/provisioning/provision-pi-card.sh
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
- final Twitcho config path passed to Showco
- final `showco` command
- X18 wired Ethernet address
- mixer probe port and protocol
- external recording mount point
