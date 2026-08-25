# Duplicate Code Consolidation

## Scope

Remove only demonstrated duplicate implementations in Showco. Preserve command
output, timeout behavior, configuration error messages, and the distinction
between provisioning-machine and target-machine workflows. Do not introduce a
general utility module merely because two functions look similar.

## Candidates

### 1. TOML Reading And Overlay

`showco/twitcho/auth.py` duplicates `read_toml`, `toml_value`,
`merge_values`, and `table_value` from `showco/provision/config.py`.

Move the shared parsing and recursive overlay behavior to
`showco/provision/config.py` and make Twitch auth call it. Keep Twitch auth's
contextual missing-value messages and its TOML-writing function local: those
are command-specific behavior, not duplication.

Tests:

- Keep the existing provisioning config parsing coverage.
- Add Twitch-auth cases proving missing files, nested secret overlays,
  environment expansion, and invalid table values retain their current
  behavior.

### 2. Configured SSH Target

`showco/python.py` reconstructs the configured SSH target rather than using
`Config.ssh_target`. `showco/logs.py` and the remote-update paths construct a
target because they permit a `--host` override.

Replace the Python command's reconstruction with `Config.ssh_target`. Add a
small helper on `Config` for the explicit host-override case only if it avoids
all remaining manual `user@host` construction without obscuring that override.
Otherwise leave those two explicit constructions alone.

Tests:

- Assert the normal path uses `Config.ssh_target`.
- Assert `showco logs --host` and `showco update --host` still send the
  override host with the configured user and port.

### 3. Showco Revision Probe Command

`showco/update.py` and `showco/provision/verify.py` each construct a shell
command that compares the running web UI revision with the target checkout.
They differ deliberately in retry policy: update waits through a restart,
while provisioning verification uses one bounded request.

Extract a single command builder that accepts its retry policy explicitly.
Keep the caller-owned execution and failure reporting in `update.py` and
`verify.py`.

Tests:

- Cover both rendered commands and their current retry flags.
- Preserve update's restart-tolerant probe and provisioning's short probe.

### 4. Repository Name Source

`showco/provision/remote.py` has a local repository-name list for target
worktree validation, while `showco/update.py` owns the update repository list.
Compare their intended membership first. If they are the same deployment set,
move the list to a dependency-light module such as
`showco/provision/repositories.py` and use it from both places. If their
membership differs, retain separate lists and document why.

Tests:

- Assert the target worktree command checks exactly the deployment repository
  set.
- Assert update's selectable repositories remain unchanged.

## Deliberate Non-Consolidations

- `logs.run_command_with_timeout` and `update.run_command_with_timeout` must
  not be merged until their contracts are made identical. Update configures
  noninteractive Git/rebase execution and lets callers turn failures into
  `StepResult`; logs returns a completed timeout result for direct CLI output.
- Mixer audio and MIDI verification share reporting shape but execute different
  discovery commands and have different selection semantics.
- The target update shell script and remote provisioning shell script are
  distinct state transitions and should remain separate.

## Order

1. Consolidate TOML parsing and run its focused auth/config tests.
2. Replace the unambiguous normal SSH-target reconstruction.
3. Extract the parameterized revision-probe command builder.
4. Consolidate repository names only after confirming identical membership.
5. After each step, run focused tests, `uv run pytest`, Ruff, Ty, pyupgrade,
   and `git diff --check`.

## Additional Work Beyond The Prompt

None.
