# Stable uv Locks

## Scope

Make `uv.lock` a deliberate, reproducible dependency artifact across Reccy,
Recs, Twitcho, Lyte, and Showco. Routine tests, CLIs, updates, provisioning,
and service installation must neither rewrite a lockfile nor run against an
unchecked stale one.

This plan does not remove lockfiles, make them untracked, or globally ignore
them in Git. A changed lockfile must again mean that somebody deliberately
changed a dependency declaration or refreshed a dependency pin.

## Current Failure Mode

The projects use editable path sources such as `../reccy` and `../recs`.
Their package metadata is therefore part of every dependent project's
resolution input. When Recs adds a dependency, for example `tomlkit`, a normal
`uv run pytest` from Showco notices that its local `../recs` metadata has
changed and automatically rewrites Showco's `uv.lock`.

The current use of `--frozen` in target update and provisioning prevents a
rewrite but also skips the stale-lock check. It can therefore install or run
the old dependency graph after a sibling project's metadata has changed. The
existing special cases that ignore a dirty `uv.lock` hide this distinction and
make an accidental lock mutation look harmless.

## Dependency Policy

Replace every internal editable path source with an exact Git commit source in
the owning project's `pyproject.toml`. Use GitHub SSH URLs and full commit
hashes in `[tool.uv.sources]`; do not use branches, tags, or local paths for
these dependencies.

The dependency graph is:

- Reccy has no internal dependency.
- Recs, Twitcho, and Lyte each pin Reccy.
- Showco pins both Reccy and Recs because it imports public APIs from both.

`pyproject.toml` is the source of truth for an internal dependency's intended
commit. `uv.lock` records the resulting complete resolution. The two files are
always changed and committed together for a dependency-pin update. This means
that a local edit to Recs or Reccy cannot alter Showco's lock, and target
installations use exactly the code recorded by the consuming repository.

Do not introduce a central workspace or a separate dependency manifest. Those
would create another repository-wide state file and make the independently
deployable projects depend on their checkout layout. Exact source pins keep
the dependency decision with the project that consumes it.

## Deliberate Dependency Updates

Use one consistent release-bump procedure whenever an internal API or its
dependencies change:

1. Commit the upstream project change and make that commit available from its
   GitHub remote.
2. In every direct consumer that must use it, replace only the affected source
   `rev` in `pyproject.toml` with the full upstream commit hash.
3. Run `uv lock` intentionally in that consumer, inspect the resulting
   `pyproject.toml` and `uv.lock` diff, then run its focused tests.
4. Commit those two files in a dedicated dependency-update commit. Do not mix
   product code, formatting, or unrelated lock resolution changes into it.
5. When Reccy changes, update Recs, Twitcho, Lyte, and any Showco code that
   directly needs that Reccy revision as one coordinated dependency bundle.
   Recs must be updated before a Showco pin that relies on the new Recs API.

If a local uncommitted upstream edit needs dependent testing, test the upstream
project directly. Do not restore a local editable override in the dependent.
Create and pin a real upstream commit before cross-project testing. This is the
trade-off that prevents every arbitrary sibling edit from becoming a hidden
dependency update.

## Operational Commands

Replace `--frozen` with `--locked` in committed project automation:

- service installation and status commands;
- Showco target update and provisioning scripts;
- repository test, lint, type-check, and formatting commands that invoke uv;
- documented developer and target-machine commands.

Use `uv lock --check` where a command only needs to validate the lock, and use
`uv sync --locked` or `uv run --locked ...` where it needs an environment. A
stale lock must fail with an instruction to perform the deliberate update
procedure, rather than being silently used or rewritten.

After every project has immutable internal sources and locked automation,
remove the update and provisioning exceptions that ignore a dirty `uv.lock`.
Treat it exactly like any other tracked modification. Existing dirty locks are
resolved once, during migration, by intentionally regenerating and committing
their correct dependency-update diffs.

Keep normal `showco update` responsible for distributing committed repository
revisions and restarting affected services. It must not run `uv lock`, change
dependency pins, or repair a dirty worktree. Its only environment operation is
the locked sync of the already committed graph.

## Migration Order

1. Record current Git commit hashes for Reccy and Recs, and verify the target
   can fetch them through its configured SSH credentials.
2. Migrate Recs, Twitcho, and Lyte from the Reccy editable source to Reccy's
   exact Git source. Intentionally regenerate and commit each lockfile.
3. Migrate Showco from editable Reccy and Recs sources to exact Git sources.
   Regenerate and commit Showco's lockfile after the final Recs source pin is
   selected.
4. Change Showco update, provisioning, service, and verification commands from
   `--frozen` to `--locked`, updating their command-level tests.
5. Remove the dirty-`uv.lock` exclusions from repository-worktree checks and
   update the tests that currently assert those exclusions.
6. Apply the same locked-command convention in Reccy, Recs, Twitcho, and Lyte
   so ordinary local test runs cannot mutate their locks either.
7. Add the documented dependency-bump procedure to each project's developer
   guidance and perform a target `showco update` from clean checkouts.

## Tests And Verification

1. For each project, run `uv lock --check`, followed by its focused test and
   static-check commands using `uv run --locked`.
2. Confirm those commands leave `git status --short` clean when started from a
   clean worktree.
3. Inspect every internal source table and lockfile to confirm no `../reccy`,
   `../recs`, or other editable sibling source remains.
4. Update Showco's command-construction tests to require `--locked` and reject
   `--frozen` for update, provision, service, and verification commands.
5. Update worktree-check tests so a modified `uv.lock` is reported as tracked
   work rather than filtered out.
6. On a fresh target, run provisioning and then `showco update`; both must
   fetch the pinned commits, complete their locked syncs, and leave all
   checkouts clean.
7. Test a controlled Recs dependency addition: Showco's normal locked test
   command must fail without modifying `uv.lock`; after the explicit Recs pin
   and `uv lock` update, it must pass without a further diff.

## Additional Work Beyond The Prompt

None.
