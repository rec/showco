# Lyte Provisioning And Updates

Lyte will be deployed as a fifth sibling project alongside `reccy`, `recs`,
`showco`, and `twitcho`. Its daemon is the existing `lyte-midi.service`,
installed through `lyte daemon install`. The initial repair path for an
already-provisioned Pi will be:

```bash
showco provision --host bertrand.local
```

Provisioning is already intended to be idempotent, and unlike `showco update`
it can clone a project that is absent from the target machine.

1. Establish the Lyte deployment contract in Lyte: confirm the target Python
   requirement, the checked-in daemon configuration to use, and the exact
   `lyte daemon install --config ...` invocation. The current Lyte project
   requires Python 3.13, so provisioning must install a Python version that
   satisfies both Lyte and the other projects before attempting `uv sync`.

2. Add a `[lyte]` section to Showco provisioning configuration with an
   explicit `enabled` flag and daemon configuration path. Add `[git.lyte]`
   with the SSH repository URL and optional refname. Keep credentials in the
   Lyte configuration or Showco secrets configuration according to their
   sensitivity; do not copy secrets into the generated shell command.

3. Extend the frozen Showco configuration models and command-line overrides
   to carry Lyte's Git repository, enabled state, and daemon configuration.
   Validate that the daemon configuration exists before the remote script
   begins changing the target.

4. Add `lyte` to provisioning preflight repository checks and remote
   environment values. Extend the remote template to create Lyte's required
   configuration and state directories, clone or synchronize `$ROOT/lyte`,
   and run its locked `uv sync` through the existing repository synchronization
   flow.

5. When Lyte is enabled, install or refresh `lyte-midi.service` after the
   checkout is synchronized, using the established Lyte daemon command. Do
   not install or start the service when Lyte is disabled. Add Lyte's service
   status to the post-reboot verification and report unavailable lighting
   hardware as a note rather than a provisioning failure where Lyte itself is
   otherwise healthy.

6. Add `lyte` to `showco update`: local updates must check, push, and
   force-with-lease Lyte exactly like the other sibling repositories; target
   updates must stop `lyte-midi.service`, record its commit, pull or reset to
   its upstream commit, synchronize dependencies after a change, and restart
   the service. Include Lyte in the no-argument update set.

7. Preserve the existing ordering guarantees: update Reccy before every
   project that depends on it, update Showco before its own final restart, and
   restart Lyte only after its own checkout and environment are ready. Lyte
   must not be made a dependency of the Showco web service unless the web UI
   later gains lighting controls.

8. Add focused tests for configuration parsing, Lyte repository preflight,
   remote script generation, disabled-versus-enabled service installation,
   and local and target update ordering. Exercise the existing-Pi repair path
   by verifying a missing `$ROOT/lyte` checkout is created by provisioning.

9. Update the provisioning report and setup documentation to list the Lyte
   checkout and `lyte-midi.service`, the selected daemon configuration, and
   the command to verify its service status.

## Additional work beyond the prompt

None.
