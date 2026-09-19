# Closing credits

## Intended result

Selecting **Tear down** must end a broadcast as a deliberate closing sequence,
not by stopping streamO immediately.

1. showCo asks streamO to begin closing credits. recs continues recording
   unchanged.
2. streamO displays the configured credit pages in order. Each page fades in,
   remains visible, then fades out.
3. Only the Twitch program's main-mix audio fades continuously from the start
   of the credits and reaches silence exactly two seconds after the final credit
   page ends. The room mix remains unchanged.
4. At that same point, the video reaches black. It remains black and silent for
   two seconds. During this final black interval, showCo fades the X18 room main
   LR fader to zero.
5. streamO stops the broadcast after those two black seconds and reports that
   completion to showCo.
6. Only after that completion, showCo stops recs recording, routes teardown
   music to X18 main LR, and immediately fades it in on the room master faders.

The room must remain unchanged until the final black interval. Teardown music
begins at the broadcast-stop boundary, not when the operator presses Tear down.

## Ownership and interface

streamO owns the encoded program, credits compositor, Twitch-program audio
fade, video fade, black interval, and final broadcast stop. showCo coordinates
the request, the last-two-seconds X18 room-master fade, and the later in-room
transition. recs continues to own recording state.

Add one streamO public control command that starts a closing sequence and
returns a durable operation identifier. Its status must report at least:

- `running`, `completed`, or `failed`
- the current credit page and timing progress while running
- the start of the final black interval
- the failure message when failed

The command must reject a second active closing sequence. A status refresh or
browser reconnect must only observe the existing sequence, never start one.

showCo stores the operation identifier and its local phase before sending the
request. Its background observation continues after the browser disconnects or
showCo restarts. When streamO reports `completed`, showCo starts its existing
teardown-music path. It must not infer completion from elapsed time. A failed
or uncertain streamO result leaves in-room music off and presents the operator
with the reported state and an explicit recovery choice.

## Credit configuration

Keep the credit content in streamO configuration because streamO renders it.
Use an ordered list of pages, each with text or an image reference, visible
duration, fade-in duration, and fade-out duration. Validate that every asset
exists and every duration is positive before a broadcast can start.

The closing command derives the schedule from that list:

- `last_page_end` is the end of the final page's fade-out.
- Twitch-program main-mix audio reaches silence at `last_page_end + 2 seconds`.
- Video reaches black at `last_page_end + 2 seconds`.
- The encoded program remains black and silent until
  `last_page_end + 4 seconds`, then streamO stops it.

Make the Twitch-program audio-fade curve explicit in streamO configuration. Its duration is
the derived interval from the credit start through `last_page_end + 2 seconds`;
do not use a separate timer in showCo.

## showCo transition changes

Replace the current Tear down order with this transaction:

1. Reject the action when Performance lock is active, as today.
2. Keep recs recording and the room X18 mix unchanged. Persist
   `credits-requesting`, then request the streamO closing operation and
   persist its identifier and `credits-running` state.
3. Keep X18 teardown-music return channels muted while credits run. On the
   durable streamO black-interval transition, preserve the current master value
   and fade X18 main LR to zero across those final two seconds.
4. On the durable streamO `completed` result, stop recs recording. Do not infer
   completion from an elapsed timer.
5. With main LR at zero, remove the instrument mix from LR while preserving its
   prior routing and faders, start the teardown playlist, route its USB returns
   to main LR, then fade main LR up immediately for the room music.
6. Persist `teardown-music-playing` only after recs has stopped and that player,
   routing, and room fade have started.

Setup and Record retain their existing streamO behavior. Do not make Stop wait
for or replace a closing-credits operation; it remains the explicit immediate
Pi shutdown action.

## Failure behavior

- If credits cannot be validated or started, leave streamO and in-room music in
  their prior state and display the error.
- If streamO refuses the request, leave recs and the room mix unchanged and
  leave music muted.
- If streamO fails during credits, keep room music off until the operator
  explicitly resolves the broadcast outcome. recs continues recording unless
  the operator explicitly stops it.
- If showCo restarts, reload the persisted operation and resume observation.
  It must never resend the start command.
- If streamO is disabled, Tear down uses the existing local recs and music
  transition, with no credits sequence.

## Verification

Automated tests should cover:

- page scheduling, including first and final fades
- the exact audio/video/black boundary times
- recs recording continuing until streamO reports completion
- the X18 room-master fade only during the final black interval
- rejection of duplicate start requests
- streamO process restart and status recovery during credits
- showCo persistence, restart, and an uncertain request response
- no in-room music before streamO completion, followed immediately by its room
  fade-in
- recs or streamO failures that block later steps without retrying commands

Run a target rehearsal with a short two-page configuration. Capture the stream
output and verify frame luminance and audio level at each boundary. In the room,
confirm that the room stays unchanged through credits, X18 main LR reaches zero
only during the final black interval, recs stops only after the broadcast, and
teardown music then begins immediately. Confirm that X18 routing is restored
for the next Setup mode.
