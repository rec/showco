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
- reads defaults from `scripts/config.env` and `scripts/secrets.env`
- writes `user-data`, `network-config`, `meta-data`, and `pi-first-boot.sh`
  directly to the card
- relies on cloud-init first-boot behavior to run the setup later

This is too indirect for the current workflow. It depends on image support for
cloud-init and makes failures harder to observe because the real work happens
after the card leaves the development machine.

## Target flow

Use the SSH-based script `scripts/provision-pi.sh`.

The operator flow should be:

1. Build or choose a Raspberry Pi OS Lite machine.
2. Make sure it is reachable over SSH.
3. Put connection and show-box values in `scripts/config.env` and
   `scripts/secrets.env`.
4. Run `scripts/provision-pi.sh`.
5. Watch all setup output locally while the script runs commands over SSH.

The script should not require direct access to the SD card.

## Environment values

Add non-secret connection values to `scripts/config.env`:

```bash
SHOWCO_PI_HOST="recs-stage.local"
SHOWCO_PI_USER="show"
SHOWCO_PI_SSH_PORT="22"
```

Add secret connection values to `scripts/secrets.env` only if password login is
needed:

```bash
SHOWCO_PI_PASSWORD="..."
```

Prefer key-based SSH when it is already configured. Password support should be
for first setup only and should not introduce a new dependency unless the script
cannot reasonably do the job without one.

Keep existing show-box values in `scripts/config.env` and `scripts/secrets.env`.
Reuse them rather than creating duplicate names.

## Script behavior

`scripts/provision-pi.sh` should:

1. Source `scripts/config.env` and `scripts/secrets.env`.
2. Require `SHOWCO_PI_HOST` and `SHOWCO_PI_USER`.
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
2. Install `uv` for the show user if missing.
3. Create the show user if needed, or reuse the logged-in user when that is the
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

- `hostname` becomes `sudo hostnamectl set-hostname "$SHOWCO_PI_HOSTNAME"`
- package lists become remote `apt-get install`
- `write_files` becomes SSH heredoc or uploaded file content
- `runcmd` becomes explicit remote script phases

## Authentication details

Start with key-based SSH:

```bash
ssh "$SHOWCO_PI_USER@$SHOWCO_PI_HOST"
```

If password login is required, use one of these approaches:

1. Ask the operator to run the script in a terminal and type the SSH password
   interactively.
2. Add `sshpass` only after explicit approval, because it is a new dependency
   and stores password material in process arguments or environment.

Do not silently add `sshpass`.

## File transfer

Prefer standard SSH tooling:

- `ssh` for remote commands
- `scp` for copying the temporary remote script
- `rsync` only if a later task needs directory synchronization

Do not introduce Python SSH libraries or async code.

## Verification

For the implementation task, run:

```bash
bash -n scripts/provision-pi.sh
bash -n scripts/1-authorize-url.sh
bash -n scripts/2-exchange-code.sh
bash -n scripts/3-validate-token.sh
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

1. Added `SHOWCO_PI_HOST`, `SHOWCO_PI_USER`, and `SHOWCO_PI_SSH_PORT` to
   `scripts/config.env`.
2. Added `SHOWCO_PI_PASSWORD` to `scripts/secrets.env`.
3. Created `scripts/provision-pi.sh`.
4. Moved reusable setup commands out of `scripts/pi-first-boot.sh` into the new
   remote script path.
5. Deleted `scripts/provision-pi-card.sh`.
6. Deleted the cloud-init templates.
7. Updated documentation.
8. Ran syntax checks and committed the migration.

## Additional work beyond the prompt

None.
