# showCo issue audit

This is a static audit of the current showCo checkout on 2026-09-29. It covers
the runtime, browser code, X18 audio and OSC paths, provisioning, deployment,
configuration, documentation, and tests. **Confirmed** means the code path has
the stated behavior. **Risk** means the failure is plausible but needs a
targeted test or physical observation. Priorities reflect possible impact
during a show, not implementation effort. No service, deployment, or hardware
flow was run for this audit. The existing physical checks remain in
[hardware.md](hardware.md).

## P0: protect the running show and its data

1. **The web controls are reachable on every Pi network interface
   (confirmed).** Provisioning binds showCo to `0.0.0.0`
   (`showco/provision/templates/services.sh:3`). `ShowcoHandler._do_post` checks
   browser origin hints but has no authentication or operator authorization
   (`showco/runtime/server.py:474-499`); `/diagnostics` likewise has no access
   check (`server.py:359-380`). The performance lock is an accident guard and
   can itself be unlocked over that same endpoint (`server.py:183-201`). The
   intended trust boundary is the Pi's password-protected, self-generated
   private network: the Pi should accept inbound connections only from that
   network, even when it joins external Wi-Fi for outbound traffic. The
   `public` topology has no private hotspot and cannot satisfy this policy;
   streaming and a private hotspot require `mixed` topology with two Wi-Fi
   interfaces. This would address exposure to clients on external Wi-Fi
   without adding web
   authentication. Provisioning now has a `restrict_external_ingress` policy
   that allows external SSH and mDNS plus private-network access, but the Pi has not been
   updated or tested with it. Verify that the UI works from the private
   network but is unreachable from the external network, while SSH works from
   both. Confirm `t.local` still resolves over the external network through
   the narrow mDNS exception.

2. **The diagnostics endpoint can consume recording disk space and disclose
   show data (confirmed).** Each `GET /diagnostics` copies logs, monitoring
   history, configuration, and the current recs recording journal into a new
   temporary directory, then creates a second compressed archive
   (`showco/runtime/workflows.py:176-192`,
   `showco/deployment/bundle.py:31-94`). There is no size limit, admission
   control beyond the general request slots, or redaction of log contents.
   Two requests can double this I/O while recs is recording. Bound the bundle,
   avoid unnecessary copies, and make the operator aware of its contents.

3. **The background music player can deadlock on ffmpeg output (confirmed
   pipe arrangement, untested failure).** `MusicPlayer._play_track` opens both
   stdout and stderr as pipes, reads only stdout, and waits for the process
   (`showco/x18/music.py:173-216`). If ffmpeg writes enough errors to fill
   stderr, it blocks; the reader then blocks on stdout. `stop()` joins only
   five seconds before closing the stream underneath the worker
   (`music.py:123-140`). Drain stderr concurrently or route it to a bounded
   file, and test a decoder that writes sustained errors.

4. **A failed or interrupted music transition can leave audio and routing in
   different modes (confirmed).** `setup()` starts playback before enabling
   routing; `record()` stops music and disables routing before it requests a
   new recs session; `teardown()` stops the stream and pauses recording before
   starting music (`showco/runtime/music.py:85-110`). Each later call can
   raise, while `mode` changes only at the end. Report the completed steps and
   define recovery for each boundary; cover partial failures with tests.

5. **A cable test can keep running after the browser reports an unknown
   outcome (confirmed).** Browser actions abort after ten seconds
   (`site/show-controls.js:13-35`), but the server holds `action_lock` across
   the whole synchronous test (`showco/runtime/server.py:173-178,298-312`).
   The test may spend time on many OSC queries and can run for an arbitrary
   requested duration (`showco/x18/cable_test.py:256-317,388-392`). A browser
   timeout does not cancel it, and later operator actions queue behind it.
   Give tests a bounded duration, explicit running/progress state, and a safe
   way to determine completion before another action is submitted.

6. **The web cable-test duration has no practical maximum (confirmed).** The
   CLI validates only that it is finite and above half a second, and the web
   route converts arbitrary text to `float` (`showco/x18/cable_test.py:44-50,
   388-392`; `showco/runtime/server.py:303-308`). The audio path allocates an
   18-channel array and writes a raw playback file, then an equally wide
   recording (`cable_test.py:435-509`). An hour at 48 kHz is over 12 GB per
   18-channel 32-bit array before additional copies. Set a tested upper bound
   for both entry points, and reject the request before pausing recs.

## P1: failures, concurrency, and recovery

7. **Music playback shutdown can outlive its owner (confirmed).** The player
   thread is non-daemon; `stop()` sets the stop event, terminates ffmpeg, waits
   at most five seconds, and then closes the audio stream and sets
   `self.thread = None` regardless of whether the worker exited
   (`showco/x18/music.py:117-140`). A blocked worker can keep the process
   alive or write to a closed stream. Make worker termination observable and
   avoid declaring the player stopped while it is still running.

8. **The music playlist treats every regular file as playable and stops on
   the first bad one (confirmed).** `music_files()` includes every file in the
   directory (`showco/x18/music.py:219-223`); one README, image, or damaged
   audio file makes `_play` mark the entire player failed and return
   (`music.py:152-170`). Filter supported audio types and decide whether a bad
   track should be skipped with an error instead of ending all setup or
   teardown music.

9. **X18 music routing has no readback or rollback (confirmed).** `enable()`
   and `disable()` send several UDP OSC writes without querying their applied
   values or restoring a prior scene (`showco/x18/music.py:46-64`,
   `showco/x18/cable_test.py:134-156`). Lost packets or a failure halfway
   through can leave USB returns in the room mix despite a reported mode.
   Confirm the final routing state and define what to do on a partial change.

10. **A cable-test cleanup error can conceal the original failure (confirmed).**
    `CableTester.run()` resumes recs in `finally`, and
    `X18TestRouting.__exit__()` raises if restoration fails
    (`showco/x18/cable_test.py:268-290,214-235`). If capture or analysis also
    failed, only the later resume or restoration error reaches the operator.
    Preserve and report both failures, especially whether recording resumed
    and mixer routing was restored.

11. **A paused or stale recs snapshot can be presented as recording
    (confirmed).** `RecsClient.status()` sets `recording` from
    `snapshot.has_snapshot` (`showco/runtime/recs.py:33-56`); transport failure
    retains the old snapshot data (`showco/runtime/recs_snapshot.py:72-83`).
    The readiness and browser text then reason from that flag
    (`showco/runtime/readiness.py:7-26`, `site/status-script.js:7-22`). The
    separate service state does show staleness, but the name and recording
    message invite a false inference. Distinguish daemon snapshot availability,
    recording request, and observed audio progress in the model and UI.

12. **The background observer can die without restarting (risk).**
    `PerformanceMonitor._run` calls `sample()` in a loop without a top-level
    failure boundary (`showco/runtime/monitoring.py:132-137`). Only the
    `observe()` call and history writes catch selected exceptions
    (`monitoring.py:115-160`); an unexpected failure in sampling, model
    creation, or an adapter ends the thread and therefore incident collection.
    Add a focused test for an unexpected sample failure and expose a stopped
    monitor as a visible fault.

13. **Slow clients can occupy all web request slots (confirmed).** The server
    has eight ordinary request slots and twelve total connection slots
    (`showco/runtime/server.py:571-585`). `_form()` blocks while reading the
    declared body length, up to the ten-second socket timeout
    (`server.py:510-532,592-612`). A few stalled clients can make the operator
    receive 503 responses. Test slow and truncated bodies, and consider a
    shorter body deadline or reserving status capacity.

14. **Long operations serialize unrelated actions (confirmed).**
    `ShowcoApp.run_action()` holds one lock while a cable test, music fade,
    service restart, or recs RPC completes (`showco/runtime/server.py:173-186,
    286-329`). This prevents conflicting changes, but an unrelated urgent
    pause/resume can wait behind a slow operation. Separate resource conflict
    rules from a single global action queue and test prioritization of urgent
    controls.

15. **System shutdown can be triggered by one ordinary action once unlocked
    (confirmed).** The Actions page has a `Stop and shut down` button
    (`showco/gui.toml:1382-1387`); `MusicController.stop()` powers off the Pi
    after stopping playback (`showco/runtime/music.py:114-120`). The
    performance lock blocks it while enabled, but there is no second explicit
    shutdown confirmation. Require confirmation and show which recording and
    streaming states will be interrupted.

16. **A write failure can make a completed cue look unconfirmed (confirmed
    failure window).** Set-list and lighting controllers persist a pending
    operation, send recs/lyte the command, then persist the success
    (`showco/runtime/setlist.py:115-149`, `showco/runtime/lighting.py:102-134`).
    If the final state write fails, the command may have succeeded while the
    pending state remains. This is safer than automatic retry, but the UI should
    explicitly distinguish storage failure from remote delivery uncertainty
    and retain both pieces of evidence.

17. **Partial target service stops are not covered by the transaction's
    interruption handler (confirmed).** `update_target()` stops services
    before entering its `try` block (`showco/deployment/target_update.py:90-99,
    122-169`). `KeyboardInterrupt` or an unexpected error during the stop
    sequence can leave some services stopped without executing rollback.
    Include service stops in the recovery boundary and test interruption at
    each stop.

18. **A successful target update verifies only recs and showCo (confirmed).**
    `check_updated_services()` checks showCo's web revision and recs status
    advancement; streamO and lyte are only started/refreshed
    (`showco/deployment/target_update.py:226-241`). A successful update can
    therefore leave an enabled stream or lighting service unhealthy. Add
    enabled-service health checks that match their actual startup contracts.

19. **Rollback settings restoration is not atomic (risk).** When
    `--clear-settings` is used, rollback writes the saved recs settings
    directly to the final path (`showco/deployment/target_update.py:77-89,
    140-153`). An interruption or full disk can leave a partial settings file.
    Restore through a temporary file and rename only after the write succeeds.

20. **A failed dependency refresh can leave earlier repositories published
    (confirmed).** `refresh_local_dependencies()` processes projects in order,
    and each changed lockfile is committed and pushed immediately
    (`showco/deployment/local_update.py:123-169,203-303`). If a later project
    fails, the earlier GitHub states remain advanced although the overall
    command fails. Report the published subset and give a precise resume path;
    do not imply the multi-repository operation is atomic.

21. **SSH availability and provisioning state are conflated (confirmed).**
    `applied_provisioning_fingerprint()` returns `None` on timeout and on any
    nonzero SSH exit (`showco/provision/remote.py:111-132`); `showco go` treats
    `None` as absent state and starts provisioning
    (`showco/deployment/go.py:96-108`). A temporary DNS or SSH failure thus
    becomes a misleading provision attempt. Distinguish unavailable target,
    missing fingerprint, and malformed fingerprint before choosing an action.

22. **SSH reboot detection can mistake a transient network loss for a reboot
    (risk).** `wait_for_ssh_disconnect()` accepts one failed reachability
    probe as evidence of shutdown (`showco/provision/ssh.py:40-46`), then
    `wait_for_ssh()` accepts a single successful probe as return
    (`ssh.py:49-64`). On intermittent Wi-Fi, both can occur without a reboot.
    Verify boot identity or sustained state transition before declaring the
    reboot complete.

## P2: operator clarity, edge cases, and maintainability

23. **The generic musician form cannot edit every entity field (confirmed).**
    The current add form exposes `name`, `other_names`, and `links`; the edit
    form excludes `copyright_name` and `public_keys`
    (`showco/gui.toml:96-185`, `showco/runtime/server.py:334-352`). Hiding
    those fields was an explicit UI choice, so do not simply reveal them.
    Document which values are preserved on edit and provide an intentional
    route for maintaining them when that becomes necessary.

24. **A malformed saved workflow file can prevent showCo startup (confirmed).**
    `SetListController`, `LightingController`, `Soundcheck`, and `Recovery`
    catch a missing file but let malformed JSON, validation errors, and read
    errors escape their constructors (`showco/runtime/setlist.py:34-42`,
    `lighting.py:24-32`, `soundcheck.py:28-40`, `recovery.py:19-27`). The
    performance lock and incident history already have explicit failure
    states (`performance.py:10-26`, `incidents.py:17-36`). Define how each
    persisted workflow fails safely and remains inspectable after corruption.

25. **A stale browser edit can overwrite newer recs track names (risk).**
    `set_track_name()` fetches the current name map and replaces it, but has
    no revision or compare-and-swap with recs
    (`showco/runtime/recs.py:174-218`). Its local lock protects only this
    showCo process; another client can change names between the read and
    write. The set-list and lighting editors already reject stale revisions.
    Seek a recs-side conditional update or make the potential overwrite clear.

26. **Some malformed numeric values are accepted as valid status (confirmed).**
    `_number` in `recs_snapshot.py` accepts positive infinity for disk time
    and playback position (`showco/runtime/recs_snapshot.py:285-288`);
    `_float` in `recs.py` accepts booleans and nonfinite floats for levels
    (`showco/runtime/recs.py:651-654`). This can produce nonsensical browser
    labels and nonstandard JSON numeric values. Validate finite, typed values
    at the adapter boundary.

27. **Browser status display logic is duplicated in Python and JavaScript
    (confirmed).** `showco/runtime/views.py:219-281,450-623` and
    `site/status-script.js:1-116,179-263` separately format service, mixer,
    playback, temperature, memory, and disk values. Small behavior changes
    must be implemented twice, and the initial page can disagree with its
    first refresh. Define one presentation contract or test paired output
    against the same fixtures.

28. **The GUI is not yet entirely driven by the data file (confirmed).**
    `performance_page()` and `workflow_page()` still build special page bodies
    (`showco/runtime/views.py:343-387`); `Gui` admits renderer-only pages
    (`showco/runtime/gui_schema.py:339-346`), and browser scripts use fixed
    element IDs (`site/workflow.js`, `site/lighting.js`, `site/performance.js`).
    A new `gui.toml` cannot freely rearrange those controls. Finish the
    remaining migration contract in [data-driven-gui.md](data-driven-gui.md),
    or explicitly narrow the promise of interchangeable GUI files.

29. **The large GUI configuration and schema are costly to change together
    (confirmed structural risk).** `showco/gui.toml` has 1,420 lines;
    `gui_schema.py` 632, `views.py` 636, and the shared template 153. Element
    kinds, allowed fields, source paths, formatting rules, and browser IDs
    are spread across all four. A simple new control can require coordinated
    edits in each. Group the schema by element behavior and remove obsolete
    special cases as the current GUI migration completes. Avoid splitting
    files merely to lower a line count.

30. **Three large orchestration modules mix unrelated responsibilities
    (confirmed structural risk).** `server.py` has 798 lines and combines
    status gathering, action dispatch, HTTP parsing, rendering, and server
    lifecycle; `recs.py` has 736 lines of status parsing, commands, and
    presentation conversion; `deployment/local_update.py` has 708 lines of
    dependency inspection, Git publication, lockfile refresh, and autosquash.
    These are plausible review and change-collision risks. Split only along
    existing boundaries when the next related change is made.

31. **Small files are mostly intentional boundaries, but CLI/service routing
    has avoidable indirection (observation).** `showco/runtime/recs_control.py`
    is 37 lines and correctly isolates serialized RPC access; tiny
    `__init__.py` files are package markers. In contrast, `showco/cli.py`
    routes through several single-command modules while
    `showco/runtime/services.py:123-139` wraps `reccy.services` methods.
    Evaluate inlining only wrappers that add no policy. Do not merge the
    adapter boundaries just to reduce file count.

32. **The service integration still carries a protocol compatibility shim
    (confirmed).** `refresh_service_definition()` rewrites old recs metadata
    keys and module arguments before passing them to reccy's installer
    (`showco/runtime/services.py:98-112`). This is the only apparent overlap
    with reccy service handling; showCo otherwise uses reccy's controller and
    registry. Confirm whether current deployed metadata can still have the
    old shape, then remove the shim if it cannot.

33. **Music mode names compress several distinct effects (confirmed UX trap).**
    `Setup`, `Record`, and `Tear down` alter streaming, recs, local playback,
    and X18 routing, while `Stop and shut down` powers off the Pi
    (`showco/gui.toml:1369-1387`, `showco/runtime/music.py:85-120`). The
    buttons and result strings do not enumerate the effects or partial state
    after a failure. Put the concrete effect and current mode next to each
    control; use a confirmation for poweroff.

34. **`showco go` and lighting `Go` share a name for unrelated operations
    (confirmed UX ambiguity).** The CLI command provisions or deploys while
    the lighting button advances a cue (`showco/deployment/go.py:11-20`,
    `showco/gui.toml:319-430`). Documentation explains the distinction, but
    spoken instructions such as “press Go” remain ambiguous. Give the CLI
    deployment action a clearer primary name while retaining any necessary
    compatibility only if users require it.

35. **The browser allows a channel edit to freeze all channel refreshes
    (confirmed).** `updateChannels()` returns for the entire page whenever
    focus is inside any `.level` (`site/channel-controls.js:215-236`). This
    preserves an active text edit, but also freezes level, recording, and
    stereo indicators on every channel until focus moves. Preserve only the
    edited field while refreshing the rest.

36. **Unknown outcomes are handled inconsistently between controls
    (confirmed).** `showAction()` warns on aborted or lost requests
    (`site/show-controls.js:13-35`), but track names, stereo changes,
    calibration, and mutable attributes use direct `fetch()` calls without
    that uncertainty message (`site/channel-controls.js:12-185`). A lost
    response can therefore be shown as a definite failure, prompting a
    duplicate or conflicting retry. Route these controls through a shared
    outcome contract and refresh observed state before resubmission.

37. **The closing-credits feature remains a plan (confirmed).** Current
    `MusicController.teardown()` stops streamO and pauses recs immediately
    (`showco/runtime/music.py:103-112`). It does not implement the timed
    credits, delayed Twitch fade, room-master fade, or later teardown music
    described in [closing-credits.md](closing-credits.md). Keep this marked as
    planned behavior so an operator does not expect it during a live show.

## Verification gaps and boundaries

38. **MusicPlayer's process, stream, and thread lifetime lacks direct tests
    (confirmed gap).** `test/test_music.py` uses mocked `player` and `routing`
    for mode sequencing and checks OSC command lists, but does not drive
    `_play_track`, ffmpeg stderr, blocked writes, worker stop timeout, or
    `MusicPlayer.start()` cleanup. Add a few focused fake-process/stream tests
    for the failure windows in issues 3, 7, and 8.

39. **HTTP resource-limit tests cover connection counts but not slow bodies
    or large diagnostics (confirmed gap).** `test/test_http_limits.py` tests
    semaphore release and cross-origin rejection. It does not test truncated
    request bodies, an occupied action slot, concurrent diagnostics downloads,
    or disk exhaustion. Cover the bounded failure behavior for issues 2, 5,
    and 13 without starting hardware services.

40. **The physical installation remains unverified.**
    [hardware.md](hardware.md) tracks capture timing, routing isolation,
    signal thresholds, and full Pi/X18 acceptance. These should remain open
    until measured on the actual setup. Automated browser, WAV, and fake OSC
    tests cannot close them.

## Additional work beyond the prompt

None.
