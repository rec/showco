# showCo issue audit

This is a static audit of the current showCo checkout on 2026-09-29. It covers
the runtime, browser code, X18 audio and OSC paths, provisioning, deployment,
configuration, documentation, and tests. **Confirmed** means the code path has
the stated behavior. **Risk** means the failure is plausible but needs a
targeted test or physical observation. Priorities reflect possible impact
during a show, not implementation effort. No service, deployment, or hardware
flow was run for this audit. The existing physical checks remain in
[hardware.md](hardware.md).

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
    (confirmed structural risk).** `showco/gui.toml` has 1,455 lines;
    `gui_schema.py` 634 and `views.py` 659. Element
    kinds, allowed fields, source paths, formatting rules, and browser IDs
    are spread across all four. A simple new control can require coordinated
    edits in each. Group the schema by element behavior and remove obsolete
    special cases as the current GUI migration completes. Avoid splitting
    files merely to lower a line count.

30. **Three large orchestration modules mix unrelated responsibilities
    (confirmed structural risk).** `server.py` has 915 lines and combines
    status gathering, action dispatch, HTTP parsing, rendering, and server
    lifecycle; `recs.py` has 737 lines of status parsing, commands, and
    presentation conversion; `deployment/local_update.py` has 721 lines of
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
    (`showco/gui.toml`, `showco/runtime/music.py`). The current mode and
    completed transition steps are now reported, and poweroff has an explicit
    confirmation. The mode buttons still do not explain their effects before
    an operator presses them. Put the concrete effects next to each control.

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

## Additional work beyond the prompt

None.

## Hardware verification required

1. **P0: The private-network ingress policy is implemented but not verified on the Pi.**
   Provisioning installs a persistent nftables policy when
   `network.restrict_external_ingress = true`. It admits traffic from the Pi's
   private hotspot, while the external interface admits only SSH and UDP 5353
   for `t.local` mDNS discovery. The `public` topology is rejected because it
   has no private hotspot. The Pi was unavailable for a controlled deployment,
   so this boundary has not been proven in use. Set a long, random password in
   the ignored `showco/provision/secrets.toml`, deploy when no show is running,
   and verify: the UI works on the private network; UI and diagnostics are
   unreachable on external Wi-Fi; SSH and `t.local` work externally; and the
   policy survives a reboot. Keep this finding open until those checks pass.

40. **The physical installation remains unverified.**
    [hardware.md](hardware.md) tracks capture timing, routing isolation,
    signal thresholds, and full Pi/X18 acceptance. These should remain open
    until measured on the actual setup. Automated browser, WAV, and fake OSC
    tests cannot close them.
