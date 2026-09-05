# Improve Cross-Repository Updates

## Goal

Keep Reccy, Recs, Showco, Twitcho, and Lyte as independent GitHub repositories
while making their normal development workflow consistently follow each
dependency's `main` branch.

`showco update` should remain the integration point. It should publish folded
local histories once, refresh and test internal dependencies, commit the
resulting lockfiles, push those new commits normally, and only then update the
target machine.

Tuney is not currently managed by `showco update` and is outside this plan.
Moving the projects to Python 3.13 is also independent work and is not required
for this design.

## Current Behavior

`update_from_provisioning_machine()` currently:

1. Checks that selected local repositories are on `main`.
2. Autosquashes recent fixup commits.
3. Pushes each repository, falling back to `--force-with-lease` when folding
   rewrote published history.
4. Connects to the target, resets its checkouts to their upstream branches,
   runs `uv sync --locked`, and restarts or refreshes services.

The repositories currently describe Reccy inconsistently:

- Recs and Showco use exact Git revisions.
- Twitcho uses an older exact Git revision.
- Lyte uses an editable sibling path.

Showco also pins Recs to an exact Git revision. Consequently, publishing a new
dependency does not advance its consumers without manual `pyproject.toml` and
lockfile edits.

## Dependency Graph

Use this graph for ordering:

```text
Reccy
  -> Recs
  -> Twitcho
  -> Lyte

Reccy + Recs
  -> Showco
```

Showco controls Twitcho and Lyte operationally, but does not import them as
Python packages. Their repositories and lockfiles therefore remain independent
of Showco's Python dependency resolution.

## Repository Policy Change

Showco's repository instructions currently say never to stage or commit
`uv.lock`. That rule conflicts with this design. Before implementation, revise
the rule narrowly:

- Continue to reject a lockfile that was dirty before an update starts.
- Permit `showco update` to stage and commit only lockfile changes it generated.
- Never include other tracked or untracked changes in an automatic dependency
  commit.
- Inspect and reject unrelated lockfile changes rather than committing them.

Do not weaken the general clean-worktree requirement.

## Phase 1: Standardize Internal Sources

Make this one-time migration in the owning repositories, with dependency and
lockfile changes committed together:

```toml
[tool.uv.sources]
reccy = { git = "https://github.com/rec/reccy.git", branch = "main" }
```

Use that Reccy source in Recs, Twitcho, Lyte, and Showco. Replace Showco's Recs
source similarly:

```toml
recs = { git = "https://github.com/rec/recs.git", branch = "main" }
```

Use one Git URL form consistently. The target already rewrites SSH GitHub URLs
to public HTTPS URLs, but using HTTPS directly avoids requiring that rewrite for
these public package sources.

Keep each `uv.lock`. A branch source chooses what can be refreshed; the lockfile
still records the exact tested commit used by that consumer.

## Phase 2: Publish Folded Histories Once

Retain the existing branch checks, autosquash, clean-worktree checks, and
`push_program()` behavior for the initial publication phase.

For every selected repository:

1. Verify `main` and a clean tracked worktree, including a clean `uv.lock`.
2. Autosquash as currently configured.
3. Try a normal push.
4. If folding made the normal push non-fast-forward, fetch the current upstream
   commit and use the existing exact `--force-with-lease` push.

This is the only phase allowed to force-push. Finish it for every selected
repository before creating any dependency-update commits.

If any initial push fails, stop before changing lockfiles or contacting the
target.

## Phase 3: Refresh Dependencies Locally

After all initial pushes succeed, refresh selected consumers in dependency
order. Use command argument lists and the existing injected `RunCommand`; do not
construct shell command strings.

Commands are conceptually:

```shell
uv lock --directory /path/to/recs --upgrade-package reccy
uv lock --directory /path/to/twitcho --upgrade-package reccy
uv lock --directory /path/to/lyte --upgrade-package reccy
uv lock --directory /path/to/showco --upgrade-package reccy --upgrade-package recs
```

Only refresh a repository selected for this update. With no selection,
`selected_repositories()` already selects all managed repositories, which gives
the normal full-system update.

Do not refresh Reccy itself because it has no managed internal dependency.

After locking each consumer:

1. Confirm that only `uv.lock` changed.
2. Run `uv lock --check --directory <repository>`.
3. Run that repository's automated test suite in its locked environment.
4. Do not run applications, services, hardware checks, or target operations as
   local verification.

Process Recs, Twitcho, and Lyte before Showco. Showco must lock only after its
new Recs commit is available on GitHub.

If locking or tests fail, report the repository and failing step, restore only
the updater-generated lockfile change, and stop before deployment. Do not roll
back or rewrite the already-published source commits automatically.

## Phase 4: Commit And Normally Push Lockfiles

When a refreshed lockfile differs from `HEAD`, create a repository-local commit
containing only `uv.lock`. Use one stable message, such as:

```text
Update internal dependencies
```

Push this commit normally. Do not call `push_program()` here because its force
fallback is appropriate only for the initial folded-history publication.
Provide a separate fast-forward-only push step for generated dependency commits.

The second push must fail rather than force if the remote branch advanced after
Phase 2. Report that concurrent update and require the user to rerun the command.

If locking produces no change, do not create an empty commit or perform a second
push for that repository.

Recs must complete its normal lockfile push before Showco resolves Recs from
GitHub. Twitcho and Lyte can be processed independently after Reccy is
available.

## Phase 5: Update The Target

Run the existing remote update only after every selected dependency refresh,
test, commit, and normal push succeeds.

Preserve these target guarantees:

- Fetch and reset each checkout to its upstream `main`.
- Use `uv sync --locked`; never resolve or rewrite dependencies on the target.
- Stop, restart, or refresh services through the existing paths.
- Keep current status and health checks.
- Do not deploy a partial local dependency update after any preceding failure.

The target should consume the exact commits recorded in the newly published
lockfiles, even though each `pyproject.toml` names `main` as the available source.

## Implementation Shape

Keep the existing public update entry points. Add small operations at the local
repository-preparation boundary rather than expanding target update logic.

Suggested responsibilities:

- Existing `prepare_local_repositories()`: validation, folding, and initial
  publication.
- New dependency refresh coordinator: dependency ordering and selected-consumer
  filtering.
- New per-program refresh operation: lock, verify changed paths, and test.
- New per-program dependency commit operation: stage only `uv.lock` and commit.
- New normal-push operation: push without force fallback.

Use `Program` and `StepResult`, preserve injected command execution, and report
failures through the existing reporting functions. Do not introduce a generic
workflow engine or asynchronous execution.

Represent the dependency graph as small explicit project data close to
`REPOSITORY_NAMES`. Do not infer dependencies by parsing arbitrary TOML or
lockfiles at runtime.

## Tests

Extend `test/test_update.py` with injected command results. Tests must not use
GitHub, modify real sibling repositories, contact the target, or run real
project test suites.

Cover at least:

1. All initial folded-history pushes finish before any lock refresh starts.
2. Recs, Twitcho, and Lyte refresh after Reccy publication.
3. Showco refreshes only after Recs' dependency commit was normally pushed.
4. Partial selections refresh only selected consumers.
5. Reccy-only updates create no lockfile commit.
6. An unchanged lockfile creates no commit or second push.
7. A refresh changes only `uv.lock`; any other changed path aborts the update.
8. A lock or test failure prevents commits, second pushes, and deployment.
9. Generated commits stage only `uv.lock`.
10. Dependency commits use normal pushes and never invoke the force fallback.
11. A rejected second push aborts and reports a concurrent update.
12. Remote deployment still uses `uv sync --locked`.
13. Existing force-with-lease behavior remains available only during the
    initial folded-history publication.

Keep focused tests for helper behavior and one orchestration test that asserts
the complete command ordering.

## Acceptance Criteria

- The five repositories remain independent GitHub projects.
- Every managed internal Python dependency names GitHub `main` as its source.
- Every consumer lockfile records an exact tested dependency commit.
- One `showco update` invocation performs publication, dependency refresh,
  verification, lockfile commits, normal follow-up pushes, and deployment.
- Force-with-lease is possible only before dependency-update commits exist.
- No force-push occurs after the initial publication phase.
- The target never resolves unlocked dependency updates.
- A failure before deployment leaves the target unchanged and clearly names the
  failed repository and step.
- Existing dirty files are neither overwritten nor included in generated
  commits.

## Additional Work Beyond The Prompt

None.
