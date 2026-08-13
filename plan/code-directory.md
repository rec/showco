# Configurable Project Directory

## Goal

Allow Showco and its sibling projects, Reccy, Recs, and Twitcho, to live in
any one common directory instead of assuming `$HOME/code` or `/code`. The
provisioning machine, the target machine, provisioning template, services,
verification commands, and `showco update` must all use the same configured
target directory.

The current working directory must never affect this behavior. A developer
may invoke `showco provision` or `showco update` from any directory.

## Configuration And CLI

1. Add a top-level `[paths]` table to `showco/provision/config.toml`:

   ```toml
   [paths]
   code_dir = "/home/tom/code"
   ```

   `code_dir` is the directory containing the `showco`, `reccy`, `recs`, and
   `twitcho` checkouts on the target machine. It is not the directory containing
   a developer's local checkouts.

2. Add a frozen `Paths` Pydantic model with `code_dir: Path` and add
   `paths: Paths` to provisioning `Config`, preserving the TOML structure
   exactly. Parse environment variables and `~` before converting the value to
   a path. Reject an empty or relative value before opening an SSH connection.

3. Add `--code-dir PATH` to `showco provision`. It overrides
   `paths.code_dir` for that run and persists the value in the selected
   `config.toml`, matching the current behavior of `--host`.

4. Add the same `--code-dir PATH` option to `showco update`.

   - On a provisioning machine it overrides the remote target directory and is
     forwarded in the remote `showco update --target-machine` invocation.
   - On a target machine it overrides the configured target directory for that
     invocation.
   - Without the option, both commands read `paths.code_dir` from their local
     provision configuration.

5. Keep developer repository discovery separate. `provision.local_code_dir()`
   should be renamed to describe its purpose, such as
   `local_checkout_directory()`, and continue to derive the parent of the
   installed Showco checkout rather than the process CWD. This supports local
   sibling checkouts in any directory without adding a second configuration
   value. It must fail clearly when the expected sibling checkout is absent.

## Provisioning Changes

1. Pass `provision_config.paths.code_dir` as the existing `CODE_DIR` remote
   environment variable. Remove the constructed `/home/<user>/code` value.

2. In `provision_locally.tmpl.sh`, replace every remaining literal
   `/home/$SHOW_USER/code/...` with `$CODE_DIR/...`, including virtualenv
   `PATH` entries for Recs and Showco.

3. Keep the existing guarded directory creation and ownership change, but
   operate on `$CODE_DIR`. Repository cloning, `uv sync`, service installation,
   and the generated network configuration command already have a `CODE_DIR`
   variable and should use it exclusively.

4. Add `--code-dir "$CODE_DIR"` to the remote `showco run install-service`
   command. Extend `ShowcoDaemon` and the `install-service` Tyro options so
   the service metadata executable is
   `<code_dir>/showco/.venv/bin/showco`, rather than deriving it from
   `Path.home() / "code"`.

5. Continue to use the configured code root only for project checkouts. Leave
   user configuration, state, logs, recordings, and Twitcho configuration
   under `$HOME`; those are not source-checkout paths.

## Update And Verification Changes

1. Make `update_target()` obtain its default target root from provisioning
   configuration rather than `Path.home() / "code"`. Preserve its injectable
   `code_dir` argument for focused tests.

2. Change `remote_update_command()` to accept a target code directory, quote
   it with `shlex.quote`, change into `<code_dir>/showco`, and forward
   `--code-dir <code_dir>` to the target command. It must not interpolate
   `$HOME/code`.

3. Thread the configured code directory through post-reboot verification:
   `project_status_command()` and `showco_service_status_command()` should
   accept a `Path` and produce safely quoted remote commands. This covers
   clean-worktree checks and the Showco service-status command.

4. Keep all Git commands built from `Program.directory`, which already derives
   sibling project paths from an injected root. Update target and provisioning
   tests to use non-default temporary-looking roots, ensuring no fallback to
   `/code` or `$HOME/code` remains.

## Tests And Documentation

1. Add configuration tests for a configured root, a `--code-dir` override,
   environment/home expansion, and rejection of relative or missing values.

2. Extend provisioning tests to assert the remote assignment, provisioning
   template paths, service executable, and verification commands use a custom
   root such as `/srv/show-projects`.

3. Extend update tests to assert both local and remote target updates use the
   custom root and that the remote command quotes a root containing spaces.

4. Update `doc/setup.md`, `doc/smoke-tests.md`, `doc/mac-test.md`, and
   `doc/pi-setup.md` to describe the configured project directory rather than
   instructing users to `cd ~/code/...`.

5. Finish with a repository-wide search for `/code/`, `$HOME/code`,
   `HOME/code`, and `Path.home() / "code"`. Remaining occurrences must be
   either deliberate test fixtures or removed. Run focused provisioning,
   update, and service tests, then the full Showco checks.

## Non-Goals

- Do not support placing the four projects in different directories.
- Do not infer the target project directory from the SSH username or the
  current working directory.
- Do not move user data, systemd state, recordings, or configuration files
  under the project directory.

## Additional Work Beyond The Prompt

None.
