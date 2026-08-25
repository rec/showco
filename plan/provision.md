# Break Up `provision.py`

## Goal

Split `showco/provision/provision.py` by responsibility while preserving the
`showco provision` command, its configuration, generated remote script, and
the order of its local and remote operations. Do not introduce dependencies or
a new provisioning architecture.

## Target Modules

Keep `showco/provision/provision.py` as the Tyro entry point and high-level
orchestrator. It should read configuration, run the existing validation and
autosquash sequence, create the temporary remote script, call the remote
orchestrator, and report completion.

Move the remaining responsibilities into these modules:

- `showco.update`: remains the sole owner of local repository branch checks,
  autosquash, and push/recovery commands. Provisioning calls its local-only
  preparation function and does not create a second local-Git module.
- `showco/provision/ssh.py`: SSH and SCP command construction, command
  execution, reachability waits, known-host removal, remote command capture,
  reboot waiting, and SSH error rendering. Keep the explicit target argument
  on the command builder because `showco logs` and `showco update` can override
  the configured host.
- `showco/provision/remote.py`: remote provisioning sequencing: preflight,
  remote worktree validation, upload, execution, reboot decision, cleanup, and
  post-provision verification.
- `showco/provision/verify.py`: verification result data, remote service and
  mixer checks, readiness polling, result reporting, and the shell commands
  used solely by those checks.
- `showco/provision/script.py`: remote-script template loading, environment
  command construction, mixer and OSC TOML rendering, and the small helpers
  used to render that data.

Leave TOML parsing, data models, and the cached `Config.ssh_target` property
in `showco/provision/config.py`. Move the two persistence helpers for command
line host/root overrides there only if they remain after the orchestration
extraction; they are configuration-file operations, not remote provisioning.

## Steps

1. Add focused import-level tests for each proposed module boundary before
   moving code. Preserve existing behavioral tests as the primary contract:
   local validation precedes autosquash, remote worktrees precede network
   preflight, cleanup follows an uploaded-script failure, host-key deletion
   happens before the initial SSH wait, and readiness retries only startup
   checks.

2. Extract `script.py` without changing call order or generated strings.
   Move `REMOTE_SCRIPT`, `script_dir`, `remote_command`, `mixers_toml`,
   `osc_nodes_toml`, `unique_selectors`, and their direct helpers. Compare the
   rendered command and TOML in the existing tests, including X18 subscription
   settings.

3. Extract `ssh.py`. Move all SSH/SCP execution, known-host handling, and
   reboot/wait functions as one unit so no partial transport layer remains.
   Update `logs.py`, `python.py`, and `update.py` to import the command builder
   from the new module. Continue deriving the normal provisioning target from
   `Config.ssh_target`; retain an explicit target only where the caller exposes
   a host override.

4. Extract `verify.py`. Move `VerificationResult`, readiness polling, service
   checks, mixer audio/MIDI detection, and verification command builders. Keep
   device absence as a note rather than an error, and keep the verification
   timeout behavior unchanged. The X18 physical-link and recording-persistence
   checks remain manual acceptance work, not provisioning assertions.

5. Extract the local-only part of `showco update` as a reusable preparation
   function. Provisioning calls it before contacting the target. Preserve the
   current special handling for dirty `uv.lock`, and do not add destructive
   reset behavior to the provisioning-machine checkout.

6. Extract `remote.py`. Move remote worktree validation, network preflight,
   and the `provision_remote` sequence. Its public entry should take
   `config.Config`, the temporary local script path, and the remote script path.
   It should continue to delegate network topology selection to
   `showco.network_config`, rather than duplicating network logic.

7. Reduce `provision.py` to options, `main`, `run`, and any minimal
   configuration-override persistence still needed there. Update tests to
   import behavior from its owning module rather than testing private
   orchestration details through the command module.

8. Commit each extraction independently. For every commit, run its focused
   tests plus `uv run pytest`, Ruff, Ty, pyupgrade, and `git diff --check`.
   Treat live SSH, reboot, Wi-Fi reconfiguration, X18 detection, external
   storage, and audio persistence as manual acceptance checks.

## Non-Goals

- Do not change the remote Bash template's behavior or migrate it to Python.
- Do not alter the provisioning TOML schema, including
  `accept_changed_host_key`.
- Do not change update behavior, service lifecycle behavior, or network
  topology rules while relocating imports.
- Do not add retries, background tasks, a web framework, or new dependencies.

## Additional Work Beyond The Prompt

None.
