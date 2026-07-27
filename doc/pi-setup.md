# Raspberry Pi setup

This is the field setup document for the Raspberry Pi show box. It assumes the
Pi will run two top-level local services:

- `recs`, the recorder daemon.
- `showco`, the local control UI, which starts and supervises `twitcho` when
  Twitch streaming is configured.

The first goal is repeatability. Do not improvise on show day. If a step is not
confirmed before the Pi goes into the case, mark it as unknown and test it.

## Target topology

The Pi is the stage box.

- The tablet connects to the Pi Wi-Fi access point and opens Showco.
- The Behringer X18 connects to the Pi by wired Ethernet for mixer control.
- The X18 connects to the Pi by USB audio for recording.
- Recording writes to external USB storage.
- If Twitch is used, the Pi gets internet through a separate Wi-Fi path.

## Base OS

Use Raspberry Pi OS Lite unless a touchscreen desktop is deliberately added
later. Lite is preferred because the normal field interface is Showco in a
browser.

Recommended first boot settings:

- hostname: decide before flashing
- SSH: enabled
- username/password: known before field use
- locale/timezone: set correctly
- Wi-Fi country: set correctly

After first boot, confirm:

```bash
hostname
date
ip addr
df -h
```

## Automated provisioning

Use `doc/provisioning.md` and the single command
`showco/scripts/provision-pi.py ...` after the Pi is reachable over SSH. This
is the normal way to build or update a repeatable show-box machine.

After provisioning, run `showco network-config --dry-run` on the Pi to inspect
the Wi-Fi plan, then run `showco network-config` to apply it.

## System packages

Install the base tools:

```bash
sudo apt update
sudo apt install -y \
  alsa-utils \
  ffmpeg \
  git \
  python3 \
  python3-venv \
  rsync
```

Install `uv` using the current official installer before building the final
image. Record the exact command used in the project notes after it has been
verified on the Pi.

## Users and directories

Use one normal user for the show programs. The examples below call it `tom`.

Create directories:

```bash
mkdir -p ~/code
mkdir -p ~/.config/recs
mkdir -p ~/.config/twitcho
mkdir -p ~/.config/showco
mkdir -p ~/.local/state/recs
mkdir -p ~/.local/state/twitcho
mkdir -p ~/recordings
```

The final recording path should be on the external USB storage, not the SD card.

## Install the programs

Clone or copy the repositories:

```bash
cd ~/code
git clone git@github.com:rec/recs.git
git clone git@github.com:rec/twitcho.git
git clone git@github.com:rec/showco.git
```

Install each project:

```bash
cd ~/code/recs
uv sync

cd ~/code/twitcho
uv sync

cd ~/code/showco
uv sync
```

Before field use, run each test suite on the Pi once:

```bash
cd ~/code/recs && uv run pytest
cd ~/code/twitcho && uv run pytest
cd ~/code/showco && uv run python -m unittest discover -s test
```

## USB audio check

Plug in the X18 USB connection and check that ALSA sees it:

```bash
arecord -l
arecord -L
```

Record the exact device name used by `recs` and `twitcho`.

Run a short manual capture before relying on daemon startup. The exact command
depends on the final `recs` flags, but the check must confirm:

- X18 appears as an input device.
- all expected channels are visible.
- a short recording produces files on external storage.
- Showco shows channel level states changing.

## External storage

Use a stable mount point for the recording drive, for example:

```text
/mnt/recs
```

Confirm the drive is mounted before starting recording:

```bash
findmnt /mnt/recs
df -h /mnt/recs
touch /mnt/recs/write-test && rm /mnt/recs/write-test
```

The service file should fail early if the storage path is missing. Do not allow
`recs` to silently record to the SD card.

## Wi-Fi access point

The Pi should expose a private show network for the tablet and for local control.

Decide and record:

- SSID
- password
- Pi address
- DHCP range
- whether the network is 2.4 GHz or 5 GHz
- whether internet sharing is enabled

Showco should bind to the Pi address on this network, or to `0.0.0.0` if the
firewall is simple and private.

## X18 Ethernet

Use the Pi Ethernet jack for the X18 control network.

Decide whether the Pi or X18 owns DHCP. The simplest deterministic version is:

- Pi Ethernet has a static address.
- X18 has a static address on the same subnet.
- the tablet reaches the X18 through the Pi network if routing is configured.

Showco can optionally start a read-only OSC recorder for the X18:

```bash
showco --x18-host 10.43.0.18 --x18-log-dir ~/recordings
```

The recorder writes `x18-<timestamp>.jsonl` files and keeps an `/xremote`
subscription alive so tablet control changes are visible to the Pi.

This is separate from USB audio. Audio still comes over USB.

## Service startup

Provisioning installs one user-level systemd service for each top-level program:

- `recs.service`
- `showco.service`

The expected startup order is:

1. storage mounted
2. network configured
3. `recs`
4. `showco`, which starts `twitcho` if `--twitcho-config` is set

The provisioning script enables lingering for the configured user so these user
services start at boot without an interactive login.

Showco must still start if Recs or Twitcho is unavailable, because it reports
those failures in the UI.

Useful commands:

```bash
systemctl --user status recs
systemctl --user status showco
journalctl --user -u recs -f
journalctl --user -u showco -f
systemctl --user restart recs showco
```

## Rehearsal mode

Before hardware testing, run Showco without Recs, Twitcho, Twitch, or audio
hardware:

```bash
cd ~/code/showco
uv run showco --rehearsal --host 0.0.0.0 --port 17352
```

Open:

```text
http://<pi-address>:17352/
```

The UI should show simulated recording, simulated streaming, eighteen channel
level indicators, and working action buttons.

## Final pre-show check

Before taking the box to a show, confirm:

- Pi boots without keyboard or monitor.
- tablet joins the Pi network.
- Showco opens from the tablet.
- Recs daemon is connected in Showco.
- Twitcho is connected in Showco.
- external storage path is mounted.
- X18 USB audio is visible.
- X18 Ethernet control path is reachable.
- a ten-minute test recording produces the expected files.
- no buffer/dropout warnings appear in logs.
- Twitch side effects work with the configured token, if streaming is enabled.
