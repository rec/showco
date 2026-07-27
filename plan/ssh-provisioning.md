# SSH provisioning plan

## Goal

Replace the SD-card provisioning flow with an SSH provisioning flow.

Status: implemented.

The new flow should assume the Raspberry Pi already boots, has networking, and
accepts SSH login. It should work for a newly imaged Pi and for an existing Pi
that is already reachable.

## Previous state

The previous script was `scripts/provision-pi-card.sh`. It:

- finds or mounts a Raspberry Pi boot partition
- reads defaults from `showco/provision/config.toml` and `showco/provision/secrets.toml`
- writes `user-data`, `network-config`, `meta-data`, and `pi-first-boot.sh`
  directly to the card
- relies on cloud-init first-boot behavior to run the setup later

This is too indirect for the current workflow. It depends on image support for
cloud-init and makes failures harder to observe because the real work happens
after the card leaves the development machine.

## Target flow

Use the SSH-based script `showco provision`.

The operator flow should be:

1. Build or choose a Raspberry Pi OS Lite machine.
2. Make sure it is reachable over SSH.
3. Put connection and show-box values in `showco/provision/config.toml` and
   `showco/provision/secrets.toml`.
4. Run `showco provision`.
5. Watch all setup output locally while the script runs commands over SSH.

The script should not require direct access to the SD card.

## Environment values

Add non-secret connection values to `showco/provision/config.toml`:

```toml
showco_pi_host = "recs-stage.local"
showco_pi_ssh_port = "22"
```

Set the Pi hostname in Raspberry Pi Imager and use that same name for
`showco_pi_host`. Provisioning uses key-based SSH only.

Keep existing show-box values in `showco/provision/config.toml` and
`showco/provision/secrets.toml`. Reuse them rather than creating duplicate
names.

## Script behavior

`showco provision` should:

1. Read `showco/provision/config.toml` and `showco/provision/secrets.toml`.
2. Require `showco_pi_host` and either `showco_pi_user` or `USER`.
3. Build one SSH target from those values.
4. Run a cheap remote preflight:
   - `uname -a`
   - `id`
   - `command -v sudo`
   - `command -v apt-get`
5. Copy a remote provisioning script to a temporary path on the Pi.
6. Run that script over SSH with the required environment values.
7. Print each major phase before it runs.
8. Fail immediately when a remote command fails.

The script should not run `git pull` in this repository or any sibling local
repository.

## Remote provisioning behavior

Move the useful work from `scripts/pi-first-boot.sh` into the SSH-run remote
script.

The remote script should:

1. Install base packages with `apt-get`:
   - `alsa-utils`
   - `ca-certificates`
   - `curl`
   - `ffmpeg`
   - `git`
   - `python3`
   - `python3-venv`
   - `rsync`
   - `sudo`
2. Install `uv` for the configured user if missing.
3. Create the configured user if needed, or reuse the logged-in user when that is the
   configured user.
4. Create:
   - `~/code`
   - `~/.config/recs`
   - `~/.config/showco`
   - `~/.config/twitcho`
   - `~/.local/state/recs`
   - `~/.local/state/twitcho`
   - `~/recordings`
5. Clone or update `recs`, `twitcho`, and `showco` under `~/code`.
6. Run `uv sync` in each checkout.
7. Write `~/PROVISIONING-NEXT-STEPS.txt`.

Keep the remote operations idempotent. Re-running the script should update an
existing machine rather than assuming a blank OS.

## Cloud-config handling

Stop depending on cloud-init to run provisioning.

Keep cloud-config files only if they remain useful as generated documentation or
as input for a separate SD-card path. Otherwise, delete:

- `scripts/user-data.yml`
- `scripts/network-config.yml`
- `scripts/meta-data.yml`

Do not keep two active provisioning paths. The intended path should be SSH.

If any cloud-config values are still needed, translate them into direct SSH
operations. For example:

- `hostname` becomes `sudo hostnamectl set-hostname "$showco_pi_hostname"`
- package lists become remote `apt-get install`
- `write_files` becomes SSH heredoc or uploaded file content
- `runcmd` becomes explicit remote script phases

## Authentication details

Start with key-based SSH:

```bash
ssh "${showco_pi_user:-$USER}@$showco_pi_host"
```

## File transfer

Prefer standard SSH tooling:

- `ssh` for remote commands
- `scp` for copying the temporary remote script
- `rsync` only if a later task needs directory synchronization

Do not introduce Python SSH libraries or async code.

## Verification

For the implementation task, run:

```bash
python -m py_compile showco/provision/provision.py showco/twitcho/auth.py
git diff --check
```

Do not run the actual SSH provisioning script unless explicitly instructed,
because it mutates a real Raspberry Pi.

Python tests are not required unless Python files or Python-consumed data files
change.

## Documentation updates

Update:

- `doc/provisioning.md`
- `doc/pi-setup.md`
- `AGENTS.md`, if provisioning guidance changes

The docs should say that the normal provisioning path starts with a working,
SSH-reachable Pi. They should not instruct users to rely on cloud-init first
boot unless a separate SD-card provisioning path is explicitly retained.

## Completed migration steps

1. Added `showco_pi_host`, `showco_pi_ssh_port`, and optional `showco_pi_user` to
   `showco/provision/config.toml`.
2. Provisioning uses key-based SSH only.
3. Created `showco provision`.
4. Moved reusable setup commands out of `scripts/pi-first-boot.sh` into the new
   remote script path.
5. Deleted `scripts/provision-pi-card.sh`.
6. Deleted the cloud-init templates.
7. Updated documentation.
8. Ran syntax checks and committed the migration.

## Additional work beyond the prompt

None.
