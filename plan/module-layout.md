# Module Layout Plan

## Goal

Reduce `showco/` to package entry points and clear top-level ownership
packages. Move code without compatibility forwarding modules: all internal
imports, tests, command strings, and documentation will use the new locations.

The intended root layout is:

```
showco/
  __init__.py
  __main__.py
  cli.py
  provision/
  streamo/
  runtime/
  deployment/
```

`cli.py` remains at the root because it is the application entry point. The
existing `provision/` and `streamo/` packages stay in place.

## Target Packages

### `runtime/`

Move the target-machine web service and all of its direct adapters here:

- `server.py`
- `models.py`
- `recs.py`, `recs_control.py`, `recs_snapshot.py`
- `mixer.py`, `lyte.py`, `system.py`, `monitoring.py`
- `services.py`
- `rehearsal.py`
- `readiness.py`, `incidents.py`, `input_check.py`, `recording_progress.py`
- `revision.py`

This package owns current show state, the HTTP UI, service adapters, health,
waveforms, and local rehearsal fakes. Its modules may import from
`showco.streamo`, but deployment and provisioning code must not import runtime
server modules.

### `deployment/`

Move provisioning-machine and target-maintenance operations here:

- `go.py`
- `update.py`, `local_update.py`, `repositories.py`
- `logs.py`
- `bundle.py`
- `card.py`
- `machine_role.py`
- `python.py`

Move `network_config.py` into `provision/network.py`, because it resolves and
writes provisioning network configuration rather than controlling a running
show. Update its CLI routing accordingly.

`deployment/` may import `provision`, but never `runtime`. `bundle.py` may use
only standard-library paths and `machine_role`; it must not acquire a dependency
on the web service.

## Migration Steps

1. Create `runtime/` and move the small, dependency-leaf modules first:
   `models`, `revision`, `readiness`, `incidents`, `input_check`, and
   `recording_progress`. Update their direct imports and tests. Verify model
   serialization and the health-page tests.

2. Move target adapters and monitors to `runtime/`: Recs modules, mixer, Lyte,
   system, monitoring, and rehearsal. Update relative imports as part of each
   move. Keep the Recs public RPC surface and cache behavior unchanged.

3. Move `server.py` into `runtime/`, then update `cli.py`, tests, and service
   installation references. Confirm that `showco run` still resolves to the
   same command and that static site paths use the project root rather than the
   new package directory.

4. Create `deployment/` and move `go`, update, local update, repositories,
   logs, card, bundle, machine-role, and Python-version commands. Update
   `cli.py` routing in the same commit so all user-facing commands retain their
   names.

5. Move `network_config.py` into `provision/network.py`. Update imports from
   provisioning, deployment, and tests. Keep `showco run network-config` as an
   internal command if it is still needed, but route it to the new module.

6. Remove every old top-level module after its callers have moved. Do not add
   forwarding imports or compatibility wrappers. Update `doc/architecture.md`,
   `doc/provisioning.md`, README examples, and test subprocess strings to use
   the new paths.

## Verification

For each move commit:

- run the affected focused tests before and after the move;
- run the complete available test suite;
- run Ruff, formatting, `ty check showco`, pyupgrade, and `git diff --check`;
- inspect service installation arguments and the generated provisioning script
  for stale Python module paths;
- verify `showco --help`, `showco go --help`, and `showco run --help` without
  starting services or contacting hardware.

The final acceptance check is a source search confirming no imports or command
strings reference removed top-level module paths.

## Additional Work Beyond The Prompt

None.
