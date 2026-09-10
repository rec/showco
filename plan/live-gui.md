# Live GUI Reduction Plan

## Goal

Make the Showco web UI a small live-performance control surface. Remove controls
whose normal use is configuration, diagnosis, service administration, or
pre-show testing. Leave the Channels page unchanged.

The resulting navigation will contain only `Channels`, `Health`, `Actions`, and
`Errors`. `Channels` remains the existing channel-control page, including its
track naming, mono/stereo, and per-channel calibration controls.

## Remove the Attributes Page

1. Remove the `/attributes` GET route and its navigation link.
2. Remove `attributes_page()` and the mutable-attribute rendering helpers and
   client calls that exist only for that page.
3. Remove the `recs-set-attr` action dispatch path. Mutable Recs settings will
   be managed through Recs configuration and its own tools, not Showco during a
   show.
4. Delete the corresponding server and Recs-client tests, and change the
   navigation test from five pages to four.

## Reduce the Actions Page

Keep the actions that have a direct, plausible live-performance purpose:

- `Start new recording session`, with its existing confirmation.
- `Pause recording` and `Resume recording`, for an intentional break or rapid
  recovery.
- The fixed show-marker buttons: `Show start`, `Song start`, `Interval`, and
  `Show end`.
- `Mute Twitch`, `Unmute Twitch`, `Create clip`, and `Create stream marker`.

Remove these setup, diagnostic, or lifecycle controls:

- global `Calibrate noise floor`; calibration stays on the unchanged Channels
  page;
- `Set noise floor`, `Reload Recs profiles`, `Set Recs key label`, and the
  free-text `Create Recs marker` form;
- `Recs status snapshot`, `Recs disk status`, `List Recs devices`, and `Recs
  capabilities`;
- `Shutdown Recs`;
- `Test lights`;
- `Restart Twitch`, `Stop Twitch`, `Update stream info`, `Send chat message`,
  and `Send announcement`.

The retained actions must stay in the current single compact action area with
the existing recent-action result list. Do not add a replacement setup page,
menu, feature flag, or hidden URL for removed controls.

## Remove Now-Unreachable Server Code

1. Delete the removed Recs and Streamo action entries from `RECS_ACTIONS` and
   `STREAMO_ACTIONS`, plus their UI-specific form helpers if they become unused.
2. Delete dispatch branches that no remaining HTML form can invoke, including
   shutdown and global calibration handling. Retain channel-specific dispatch
   branches because the Channels page uses them.
3. Remove direct adapter methods only if no remaining Showco runtime code or
   test calls them. Do not change the public Recs or Streamo protocols.
4. Simplify `actions_page()` arguments and helper functions once the removed
   optional controls no longer need them. Keep the disabled-Streamo behavior for
   the retained Streamo actions.

## Verification

1. Update `test/test_server.py` to assert the four-page navigation, retained
   action values, and absence of every removed action and `/attributes` route.
2. Preserve and update dispatch tests for retained recording and Streamo
   actions. Delete tests that only cover removed GUI behavior.
3. Run `uv run pytest`, Ruff, formatting, `ty check showco`, pyupgrade, and
   `git diff --check`.
4. Do not perform hardware verification. The change only removes Showco UI
   controls and leaves the Channels page untouched.

## Additional Work Beyond The Prompt

None.
