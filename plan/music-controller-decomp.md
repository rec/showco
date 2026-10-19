# MusicController decomposition plan

Planning only. This document takes over the music portion of review item 27.
It complements [showco-decomp.md](showco-decomp.md); neither plan implements a
refactor or changes show behavior.

## Additional work beyond the prompt

None.

## Purpose

Separate normal show transitions from the closing-operation observer and its
recovery state. Make ownership and failure ordering explicit while retaining one
implementation of each transition and the existing RPC/player/routing adapters.
Do not introduce an event bus, async runtime, retry engine, generic state machine,
controller inheritance, or additional background workers.

## Existing behavior that must survive

- Setup stops broadcasting, pauses recs, starts configured setup audio, and
  establishes the appropriate return routing.
- Record stops incidental music, starts a new recs session, resumes recording,
  and then starts broadcasting. Failure to resume recording prevents broadcasting.
- Repeating Record in normal Record mode preserves the session and stream.
- End show requests closing credits exactly once and continues recording until
  streamO confirms completion. Elapsed time or socket loss is not completion.
- Room fading, recording pause, instrument isolation, and teardown playback
  follow the existing confirmed closing boundaries in
  [closing-credits.md](closing-credits.md).
- Abandoning failed closing restarts recording and broadcasting through the same
  Resume show transition. Restore room routing first; clear closing evidence only
  after success. It must not merely label paused recording as Record mode.
- Manual finish still requires explicit confirmation that the broadcast stopped.
  Reject recovery while matching credits are still running.
- Failure to persist a closing failure must still publish the in-memory fault,
  failed operation, persistence error, and recovery information, and log the error.
- Shutdown waits for the observer to exit before stopping the player or muting
  returns. A slow in-flight operation may delay shutdown; there is no timed join
  followed by competing cleanup.
- Existing saved operation IDs and room scenes remain the basis for restart
  recovery. Restart must observe an existing operation rather than resubmit it.

## Proposed boundaries

| Owner | Responsibility |
| --- | --- |
| `runtime/music.py:MusicController` | Setup/Record/Resume/teardown entry points, normal show-phase transitions, and shutdown ordering |
| `runtime/closing.py:ClosingController` | Closing request/operation identity, observer lifecycle, credits progress, finalization, and closing recovery |
| `runtime/music.py:MusicConfig` and factory | Existing music configuration and assembly from mixer specifications |
| `x18/music.py:MusicPlayer` | Playlist decoding, playback thread, level fading, player status |
| `x18/music.py:X18MusicRouting` | Confirmed mixer reads/writes, captured room scene, and room routing operations |
| Existing recs and streamO clients | Service-specific transport and acknowledgement interpretation |

`ClosingController` is a cohesive owner, not a wrapper around methods left in
`MusicController`. Move the closing record, observer, persistence, and failure
handling together. Keep normal mode configuration and factory code in music.py
unless a later concrete consumer justifies another module.

Supply existing concrete clients/player/routing objects and narrow callbacks
where closing must publish a normal show-phase result. Do not pass the whole
application or make the two controllers retain each other. MusicController owns
normal show-phase persistence; ClosingController owns closing-operation
persistence. A callback may request a phase change through its owner, rather
than both classes writing the same file or mutable field directly.

## API and state ownership

- The application calls normal show methods through MusicController. It does not
  start, join, or mutate the closing worker itself.
- ClosingController exposes a small explicit request, recovery, status, and stop
  lifecycle. Replace the ambiguous `recover_closing(finish: bool)` boundary with
  named recovery choices when moving it; keep one canonical implementation of
  Resume show and one implementation of finalization.
- Keep the distinction between streamO's remote closing result and showCo's local
  finalization state. A completed broadcast can coexist with failed local routing
  or paused recording; neither state should overwrite the other's evidence.
- Publish immutable status snapshots with explicit operation identity, phase,
  and error. Derive the combined MusicStatus in one place instead of allowing
  both controllers to assign a shared display-mode string independently.
- Preserve current file locations and JSON shapes during the mechanical
  extraction. No new schema fields, migrations, or compatibility readers are
  justified by moving ownership alone.

## Concurrency and liveness

Document which operation owns playback, mixer routing, and recording changes at
all times. The normal transition and closing finalization paths must not operate
those outputs concurrently. Keep the application's immediate recording-control
rejection and UI availability rules intact.

Use short critical sections for operation identity and published state, with
external I/O outside the status-publication lock. Never hold a lock needed by the
observer while joining it. Observer shutdown means signal, wait for exit, then
output cleanup, in that order. Handle storage failure without losing in-memory
truth or attempting an unrequested replay of side effects.

Do not add automatic restarts of failed transitions. Preserve the explicit
operator recovery choices and their uncertainty messages. Any requirement to
change those choices is a new behavior decision, not part of decomposition.

## Implementation sequence

1. Use the existing music, app, incident, and browser tests to establish the
   behavior above. Add only missing behavior coverage needed by the extraction.
2. Identify all closing-owned fields and methods, including constructor restart
   observation, normal transitions' restoration hooks, and shutdown joins.
3. Extract ClosingController with its state/persistence/observer as one coherent
   change. Remove the moved implementation from MusicController completely.
4. Connect normal transitions through narrow collaborators and a single owner for
   phase publication. Keep dependency direction acyclic.
5. Replace boolean recovery selection at the controller boundary and update its
   action-dispatch callers in the same commit; do not retain two public spellings.
6. Review lock ordering and publish-status behavior under blocked RPC, routing
   failure, unwritable storage, and shutdown during finalization.

## Acceptance

Focused tests must cover request acknowledgement loss, duplicate closing requests,
restart with an existing operation, missed black interval, finalization failure
after recording pauses, failed persistence of the fault itself, explicit Resume
show, failure to resume recs, and shutdown blocked beyond the former five-second
cutoff. Keep the current regressions instead of duplicating them for every private
method or file move.

Run the full suite and required Python checks for every implementation commit.
Fake clients and routing/player controls verify ownership and ordering; they do
not prove venue timing or audible output. Do not launch services, perform a
deployment, or run physical X18/streaming checks without explicit authorization.
