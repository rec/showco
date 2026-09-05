# Improve Cross-Repository Updates

## Goal

Keep Reccy, Recs, Showco, Twitcho, and Lyte as independent GitHub repositories
while making their normal development workflow consistently follow each
dependency's `main` branch.

`showco go` is the integration point. A normal update publishes folded local
histories, refreshes and tests internal dependencies, commits generated
lockfiles, pushes those commits normally, and only then updates the target
machine.

`showco go --remote` remains deployment-only. It does not examine or change
local repositories, refresh dependencies, or create commits.

Tuney is not managed by `showco go` and is outside this plan. Moving the
projects to Python 3.13 is also independent work.

## Dependency Graph

Use this graph for ordering and repository selection:

```text
Reccy
  -> Recs
  -> Twitcho
  -> Lyte

Reccy + Recs
  -> Showco
```

Showco controls Twitcho and Lyte operationally, but does not import them as
Python packages. Their lockfiles remain independent of Showco's dependency
resolution.

## Selection Semantics

Expand an explicit repository selection to include every managed downstream
consumer whose lockfile and runtime would otherwise remain on the old version:

- `reccy` selects all five repositories.
- `recs` also selects Showco.
- `showco`, `twitcho`, and `lyte` add no repositories.

Combine and deduplicate closures in dependency order. With no explicit
selection, select all managed repositories as before.

Use the expanded selection for local validation, publication, dependency
refresh, target updates, environment synchronization, and service restarts. In
particular, a Reccy-only request must not merely update the Reccy checkout while
leaving every service locked to an older Reccy commit.

## Repository Policy Change

Revise Showco's repository instruction about `uv.lock` narrowly:

- Reject every tracked file, including `uv.lock`, that was dirty when an update
  started.
- Permit `showco go` to stage and commit only lockfile changes it generated.
- Never include other tracked or untracked files in an automatic dependency
  commit.
- Recheck changed paths after locking and tests, immediately before staging.
- Restore only an uncommitted updater-generated `uv.lock` after a failure.

Do not weaken the general clean-worktree requirement.

## Phase 1: Standardize Internal Sources

Make this one-time migration in the owning repositories, with each repository's
dependency declaration and lockfile committed together:

```toml
[tool.uv.sources]
reccy = { git = "https://github.com/rec/reccy.git", branch = "main" }
```

Use that Reccy source in Recs, Twitcho, Lyte, and Showco. Replace Showco's Recs
source similarly:

```toml
recs = { git = "https://github.com/rec/recs.git", branch = "main" }
```

Use public HTTPS consistently for these public package sources. Keep each
`uv.lock`: the branch source specifies what may be refreshed, while the lockfile
records the exact tested commit used by that consumer.

## Phase 2: Validate And Publish Local Histories

Before changing any lockfile:

1. Expand the requested selection to its downstream closure.
2. Verify every selected repository is on `main` with a clean tracked worktree,
   including a clean `uv.lock`.
3. Record each selected repository's upstream branch and upstream commit.
4. Autosquash as configured, recording whether each repository was rewritten.
5. Push every selected repository.

Try a normal push first. A force push is allowed only when autosquash actually
rewrote that repository. Its `--force-with-lease` must name the upstream commit
captured before autosquash. Do not fetch a newer upstream commit and then use
that newer value as the lease: that could authorize overwriting concurrent
work.

If the remote advanced after the upstream commit was captured, the lease must
fail. Report the concurrent update and require the user to reconcile it. Finish
all initial pushes before changing lockfiles. If any push fails, stop before
lockfile changes or target contact.

## Phase 3: Refresh And Publish By Dependency Wave

Process selected consumers in these waves:

1. Recs, Twitcho, and Lyte, after Reccy has been published.
2. Showco, after any selected Recs dependency commit has been published.

For each selected consumer, run the appropriate command using the existing
injected `RunCommand` and an argument list:

```shell
uv lock --directory /path/to/recs --upgrade-package reccy
uv lock --directory /path/to/twitcho --upgrade-package reccy
uv lock --directory /path/to/lyte --upgrade-package reccy
uv lock --directory /path/to/showco --upgrade-package reccy --upgrade-package recs
```

After locking each consumer:

1. Confirm that only `uv.lock` changed.
2. Run `uv lock --check --directory <repository>`.
3. Run `uv run --locked --directory <repository> pytest` so the test environment
   is synchronized to that lockfile before tests execute.
4. Confirm again that only `uv.lock` changed.
5. If the lockfile differs from `HEAD`, stage only `uv.lock`, commit it as
   `Update internal dependencies`, and push it normally.
6. If the lockfile is unchanged, create no commit and perform no second push.

The generated dependency push is always fast-forward-only in effect: use a
normal Git push and never fall back to force. A rejection means the remote
advanced after Phase 2; report it and stop.

Complete and publish Recs before locking Showco so Showco can resolve the new
Recs commit from GitHub. Twitcho and Lyte are independent once Reccy is
available.

If locking, checking, testing, committing, or pushing fails, report the
repository and step. Restore the current repository's generated, uncommitted
lockfile change and stop before deployment. Already published source or
dependency commits remain published and are completed by a later rerun; do not
rewrite or roll them back automatically.

Do not run applications, services, hardware checks, or target operations as
local verification.

## Phase 4: Update The Target

Run the existing target update with the expanded repository selection only
after every selected dependency refresh, test, commit, and normal push
succeeds.

Preserve these target guarantees:

- Fetch and reset each selected checkout to its upstream `main`.
- Use `uv sync --locked`; never resolve or rewrite dependencies on the target.
- Stop, restart, or refresh all services affected by the expanded selection.
- Keep current status and health checks.
- Leave the target unchanged when any local publication step fails.

The target consumes the exact commits in the published lockfiles even though
each `pyproject.toml` names `main` as the available source.

## Implementation Shape

Keep `showco go` and the current internal update entry points. Add small
operations at the local repository-preparation boundary; do not expand target
update logic beyond passing it the expanded selection.

Responsibilities:

- Selection closure: explicit dependency graph and deterministic expansion.
- Initial validation: branch, worktree, upstream branch, and upstream commit.
- Autosquash result: distinguish unchanged repositories from rewritten ones.
- Initial publication: normal push, with the captured pre-rewrite lease used
  only for repositories actually rewritten.
- Consumer refresh: lock, lock check, test, and final changed-path check.
- Dependency publication: stage only `uv.lock`, commit, and normal push.

Use `Program` and `StepResult`, preserve injected command execution and existing
failure reporting, and represent the graph as explicit project data near
`REPOSITORY_NAMES`. Do not parse arbitrary TOML or lockfiles, introduce a
workflow engine, or use asynchronous execution.

## Tests

Extend `test/test_update.py` and `test/test_go.py` using injected command results.
Tests must not use GitHub, modify real sibling repositories, contact the target,
or run real project test suites.

Cover at least:

1. Selection expansion includes downstream consumers in dependency order.
2. All initial pushes finish before any lock refresh starts.
3. Force-with-lease is unavailable when autosquash did not rewrite history.
4. A rewritten history uses the upstream SHA captured before autosquash.
5. A rejected lease aborts without lock changes or deployment.
6. Recs, Twitcho, and Lyte refresh after Reccy publication.
7. Showco refreshes only after Recs' dependency commit is normally pushed.
8. A Reccy selection refreshes and deploys every consumer.
9. A Recs selection also refreshes and deploys Showco.
10. An unchanged lockfile creates no commit or second push.
11. Changed paths are checked both before and after tests.
12. Any changed path other than `uv.lock` aborts the update.
13. A lock, lock-check, or test failure prevents later commits and deployment.
14. Generated commits stage only `uv.lock`.
15. Dependency commits use normal pushes with no force fallback.
16. A rejected dependency push reports a concurrent update and prevents
    deployment.
17. Target deployment receives the expanded selection and still uses
    `uv sync --locked`.
18. `--remote` retains its deployment-only behavior.

Keep focused tests for helpers and one orchestration test that asserts the
complete command ordering.

## Acceptance Criteria

- The five repositories remain independent GitHub projects.
- Every managed internal Python dependency names GitHub `main` as its source.
- Every consumer lockfile records an exact tested dependency commit.
- One normal `showco go` invocation publishes sources, refreshes dependencies,
  verifies them, publishes lockfiles, and deploys the complete affected set.
- Partial selections cannot leave downstream consumers running an older
  dependency unintentionally.
- Force-with-lease is possible only for a repository actually rewritten by
  autosquash and only against its upstream state captured before rewriting.
- No force push occurs for generated dependency commits.
- The target never resolves unlocked dependency updates.
- A failure before deployment leaves the target unchanged and clearly names the
  failed repository and step.
- Existing dirty files are neither overwritten nor included in generated
  commits.

## Additional Work Beyond The Prompt

None.
