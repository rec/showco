# Showco plan

`showco` is the local stage-control program for the Raspberry Pi stage box. It is
the operator-facing control surface that coordinates `recs` and `twitcho` during
setup and performance.

The design goal is simple field operation: from a phone, tablet, or small local
screen, the operator should be able to confirm that recording and streaming are
healthy, trigger the few actions that matter during a show, and recover cleanly
from partial failures.

## Scope

Showco controls local programs. It does not record audio itself, stream to
Twitch itself, or control the mixer. It speaks to:

- `recs`, for recording state, levels, calibration, and session metadata.
- `twitcho`, for stream state, muting, stopping, Twitch actions, and stream-time
  title-card behavior.
- Future programs, such as lighting control, through the same adapter pattern.

Mixer control is intentionally out of scope for now. Showco may start a
read-only X18 OSC recorder subprocess for audit and replay, but it should not
send mixer control changes.

## User interface

The first UI should be a small web application served by the Raspberry Pi.

The Pi can run its own Wi-Fi access point, so a phone or tablet can connect
directly even when the venue network is unavailable. The web UI should be usable
on a 480x320 touchscreen, but not depend on one.

The UI should prioritize:

1. recording status
2. stream status
3. per-channel level state
4. storage and runtime health
5. a small set of large action buttons

The default screen should be useful at a glance from stage distance. Avoid dense
configuration screens during the show.

## Core screens

### Home

Show:

- `recs` connection state
- `twitcho` connection state
- recording active or stopped
- stream active, muted, or stopped
- elapsed recording time
- audio frames or update freshness
- storage path and available space if `recs` exposes it
- last error from each connected program

Home contains one compact indicator per recording channel.

Use four states:

- silent or disconnected
- signal present
- healthy level
- clipping or too hot

This is not a precision meter. Its purpose is setup confidence.

### Actions

Provide large buttons for:

- start or stop recording, once `recs` exposes that safely
- calibrate noise floor
- mute or unmute the Twitch stream
- stop the Twitch stream
- update Twitch stream title/category/tags
- send a Twitch chat message
- send a Twitch announcement
- create a Twitch clip
- create a Twitch stream marker

Destructive or show-ending actions should require confirmation.

## Communication model

Showco should not import `recs` or `twitcho` internals. It should communicate
over local protocols so each program can restart independently. Showco may
start and supervise Twitcho as a child process, but all stream control still
goes through Twitcho's local control protocol.

Use one adapter per service:

```text
showco UI
  |
  +-- RecsClient
  |
  +-- TwitchoClient
  |
  +-- future clients
```

Each adapter owns:

- connection setup
- reconnection
- command formatting
- response parsing
- stale-status detection
- service-specific errors

The rest of Showco should see stable Python objects, not raw JSON messages.

## Recs integration

Use the existing daemon/control direction from `recs`: clients connect to a
running daemon and receive status events. Showco should treat `recs` as the
source of truth for recording.

Initial Showco needs from `recs`:

- connection status
- recording state
- session name or manifest path
- active devices and tracks
- channel level states
- clipping state
- calibration command
- latest error

If some of these are not exposed yet, Showco should display them as unavailable
rather than guessing.

## Twitcho integration

Use Twitcho's JSON-lines control server.

Existing commands:

- `hello`
- `status`
- `mute`
- `unmute`
- `stop`
- `ping`

New Twitch-facing commands should be exposed through Twitcho, not directly from
Showco, so that Twitch auth and stream-specific state stay in one process.

Commands Showco should call through Twitcho:

- update stream information
- send chat message
- send announcement
- create clip
- create stream marker

Twitcho should return structured success or failure responses. Showco should
display the result and keep a short recent-action log.

## Twitch feature surface

Showco should expose only the Twitch actions useful during a small live show:

1. update stream title/category/tags
2. send chat messages
3. send highlighted announcements
4. create clips
5. create stream markers

Do not add polls or predictions yet. They add interaction complexity and are not
needed for the first field version.

## Reliability

Showco is not safety-critical, but it must be calm under failure.

Required behavior:

- if `recs` disconnects, keep Twitcho controls available
- if `twitcho` disconnects, keep Recs controls available
- reconnect automatically with backoff
- show stale data distinctly from fresh data
- never hide the most recent error
- avoid blocking the UI on service calls
- time out control commands and report unknown state

The UI should make it obvious whether a button succeeded, failed, or timed out.

## Authentication and permissions

Showco itself should not store Twitch credentials. Twitcho should own Twitch
credentials and OAuth token handling.

Showco may store local preferences such as:

- default stream title template
- common chat messages
- common marker descriptions
- which status cards are expanded

These preferences should be local files, not a database.

## Implementation phases

### Phase 1: skeleton

- create the Python project
- define service status models
- build a small web server
- serve a static or minimal dynamic status page
- add fake Recs and Twitcho adapters for UI development

### Phase 2: Twitcho control

- connect to Twitcho JSON-lines control
- display stream status
- implement mute, unmute, stop, and ping
- show connection and stale-status state

### Phase 3: Twitch actions through Twitcho

- add UI forms/buttons for title/category/tags, chat, announcements, clips, and
  markers
- call the corresponding Twitcho control commands
- display structured results and recent action history

### Phase 4: Recs control

- connect to the Recs daemon/control protocol
- display recording state, tracks, and level indicators
- add calibration action
- add recording actions only when the Recs protocol exposes them safely

### Phase 5: field UI polish

- optimize for phone/tablet use
- add large buttons and high-contrast states
- test at 480x320
- add a startup health page
- add a simple read-only fallback page if actions fail

## Non-goals for the first version

- full Twitch chat client
- Twitch polls or predictions
- mixer control, beyond read-only OSC recording
- direct audio processing
- database-backed history
- cloud service dependency
- remote internet access requirement

## Open questions

- What exact Recs control messages will be stable enough for Showco?
- What should the final Showco service command pass as the Twitcho config path?
- Should the Raspberry Pi access point setup live in Showco docs or in separate
  deployment scripts?
- How much local configuration should be editable from the UI during a show?
