# MusicController decomposition

Implementation complete. This work covers the music portion of review item 27
and complements [showco-decomp.md](showco-decomp.md).

## Additional work beyond the prompt

None.

## Ownership

| Owner | Responsibility |
| --- | --- |
| `runtime/music.py:MusicController` | Setup/Record/Resume/teardown entry points, normal show-phase persistence, combined MusicStatus, and shutdown ordering |
| `runtime/closing.py:ClosingController` | Closing request identity, room-scene evidence, observation, local finalization, recovery checks, closing persistence, and fault publication |
| `runtime/closing.py:ClosingSnapshot` | Immutable local record, separately observed remote result, error, and observer ownership |
| `runtime/music.py:MusicConfig` and factory | Music configuration and assembly from mixer specifications |
| `x18/music.py:MusicPlayer` | Playlist decoding, playback thread, level fading, and player status |
| `x18/music.py:X18MusicRouting` | Confirmed mixer reads/writes, captured room scene, and room routing operations |
| Existing recs and streamO clients | Service-specific transport and acknowledgement interpretation |

ClosingController receives concrete clients, player, routing, fade duration,
teardown paths, its storage path, and a narrow phase-save callback. The callback
uses a weak reference to the normal phase owner; the controllers do not retain
each other. closing.py does not import music.py.

MusicController alone writes `music.json`. ClosingController alone writes
`closing.json`. Existing locations and saved JSON shapes are unchanged. There
are no new persisted fields, migration readers, dependencies, workers, or retries.
The closing methods and record model were removed from music.py completely.

## Transitions and recovery

- Setup stops broadcasting, pauses recs, restores a retained room scene, starts
  configured setup audio, and establishes the return routing.
- Record stops incidental music, mutes its returns, restores the room scene,
  starts a new recs session, resumes recording, then starts broadcasting. Failure
  to resume recs prevents broadcasting. Repeating ordinary Record preserves its
  session and stream.
- End show persists one operation ID before requesting credits. Duplicate
  requests cannot resubmit it. Recording continues until the matching remote
  operation reports completion; elapsed time and connection loss cannot imply it.
- The final black interval owns the room fade. Confirmed broadcast completion
  leads to recording pause, playback stop, instrument isolation, and teardown
  playback. Missing the interval still silences the room before finalization.
- Resume show, `record(restart=True)`, may interrupt running credits. This is the
  explicit user decision made during implementation. It stops and waits for the
  observer before using the same canonical Record transition.
- `abandon_closing()` and `finish_closing()` replace boolean recovery selection.
  Both reject matching credits that are still running. Abandon uses the canonical
  Resume show transition. Manual finish still requires the application's explicit
  broadcast-stopped confirmation.
- Closing evidence clears only after a successful normal transition. Failure to
  restore routing or resume recs retains that evidence. Successful manual finish
  clears its local saved error so restart does not resurrect a resolved fault.

## Status and persistence failures

MusicController derives the combined MusicStatus in one place. Normal transition
errors, local closing faults, player errors, and streamO's remote closing result
retain separate owners. A local routing failure after remote completion leaves
that remote result completed while MusicStatus reports the local fault.
Normal-transition and closing errors are both included when they coexist, so a
restart warning cannot hide a later routing or fault-persistence failure.

A saved local phase does not fabricate a remote result. Before matching remote
status is observed after restart, MusicStatus carries the saved operation identity
and local phase with remote state `unknown`. The response model's fields remain
unchanged. Existing operation IDs and captured room scenes remain authoritative
for observation and recovery; restart never resubmits credits.

ClosingSnapshot is immutable. Publication locks protect short in-memory updates,
not RPCs, routing, playback, filesystem writes, or joins. Storage failure while
saving a closing fault still publishes the failed local record, operation ID,
persistence error, and recovery information, and logs the error. This also holds
when restart discovers an interrupted finalization and cannot save its fault.

An active observer retains closing output ownership even if restart also loaded
an interrupted normal-transition error. The error stays visible, while closing
mode continues to block conflicting manual recording controls.

## Concurrency and shutdown

Application actions remain serialized by ShowcoApp. Normal transitions cannot
change outputs while closing observation/finalization owns them. Ordinary mode
changes reject an unresolved operation. Resume and explicit recovery signal and
join the observer first; their bounded wait rejects the action without output
changes if the worker is still active.

Shutdown uses an unbounded observer join: signal, wait for exit, then stop the
player and mute returns. No publication lock is held during that join. Player
shutdown failure still mutes returns. The application does not manage the worker
or bypass its lifecycle. No additional background worker or event framework was
introduced.

## Verification

Existing regressions were retained and updated to inject collaborators at
construction and observe public status rather than moved worker fields. Coverage
includes lost request acknowledgement, duplicate requests, saved-operation
observation, missed black intervals, finalization failure after recording pause,
unwritable fault storage, explicit recovery, recs-resume failure, blocked RPCs,
and shutdown beyond the former five-second cutoff.

Focused regressions also cover distinct Resume/recovery choices during running
credits, status availability during blocked finalization, successful manual-finish
persistence, and observer ownership after an interrupted Resume show. Recording
controls remain blocked through the final phase save and failure cleanup. Failed
deletion of old evidence after Resume show preserves the current Record mode and
does not replay the session or stream. Combined errors remain visible in status
and incident reporting even if closing fault persistence fails.

The full suite, Ruff, ty, pyupgrade, and diff checks pass. No application,
deployment, service restart, or physical X18/streaming flow was launched. The
user's successful show already supplied hardware acceptance.
