# Parallel unit-test plan

## Goal

Run each project's Python unit tests concurrently where this reduces the time
to a trustworthy result. Preserve the current serial command as the reference
and as the failure-reproduction command.

This applies both to a developer running tests directly and to showCo's local
dependency refresh. The refresh must still process projects in dependency
order: reccy and uFor before recs, and recs and streamO before showCo. Only
the tests *within* one project run concurrently.

## Constraints

- Do not run hardware checks, a live showCo server, or deployment as part of
  the parallel suite.
- Each worker must use pytest's per-test temporary directory. Tests must not
  share a fixed port, a fixed file path, a process-wide environment change, or
  a module-level fake that can affect another worker.
- Keep regression fixtures read-only. A test that intentionally updates a
  fixture runs serially and requires an explicit update command.
- A failed parallel run must print pytest node IDs so the exact test can be
  rerun with `uv run --locked pytest -q <node-id>`.

## Implementation steps

1. Measure the serial baseline in showCo, recs, reccy, streamO, lyte, and
   uFor. Record wall time, slowest tests from `--durations`, and the number of
   tests. Run each suite at least three times so cache and process-start costs
   are visible.

2. Audit tests before enabling workers. Start with the known risk areas:
   browser subprocess tests, socket and HTTP-server tests, environment patches,
   process-wide patches, and fixture-writing regression tests. Give every test
   an isolated temporary path and an OS-assigned port where needed. Do not add
   locks merely to make a shared test pass: remove the shared resource instead.

3. Add `pytest-xdist` as a development dependency in each participating
   project. Commit each project's `pyproject.toml` and `uv.lock` separately.
   Use the same current dependency policy as the project: the package is a
   test-only tool and must not enter the target runtime dependencies.

4. Define one standard parallel command in each project:

   ```bash
   uv run --locked pytest -q -n auto --dist=loadfile
   ```

   `loadfile` keeps tests from one file in the same worker. It avoids splitting
   a unittest class or a file that has module-level setup, while independent
   files still run together. Do not choose a fixed worker count; `auto` adapts
   to the machine running the tests.

5. Keep the existing serial command unchanged:

   ```bash
   uv run --locked pytest -q
   ```

   Document it as the required rerun after a parallel failure and the command
   for deliberate regression-fixture updates. The parallel command becomes the
   normal verification command only after the stability checks pass.

6. Change showCo's dependency-refresh verification to use the project's
   standard parallel command. Do not refresh or test dependent projects in
   parallel: a refreshed lockfile must be committed and pushed before a
   dependant refreshes its source dependencies. Keep `uv lock --check` serial
   immediately before the test command.

7. Add focused tests for the generated verification command and its failure
   behavior. They should prove that showCo invokes `pytest -q -n auto
   --dist=loadfile`, reports a failed step, restores a generated lockfile, and
   does not commit or push after failure.

## Rollout and acceptance

For each project, compare the serial command with the parallel command from a
clean checkout. Run the parallel command at least ten consecutive times on the
development machine and once in the provisioning environment. Investigate any
flaky result before enabling it by default.

Accept a project only when all of the following hold:

- its parallel suite has the same collected tests and results as the serial
  suite;
- no test contacts real audio, lighting, mixer, network, or target services;
- repeated runs are stable; and
- the median wall time is meaningfully lower after including worker startup.

If showCo's short suite is not faster on a given machine, leave its direct
developer command serial. The dependency refresh can still use parallel tests
for larger sibling projects once those projects meet the same acceptance
criteria.

## Additional work beyond the prompt

None. This document plans the change; it does not add pytest-xdist, alter
project dependencies, or change test execution yet.
