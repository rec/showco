# Live Channel Waveforms

## Scope

Add a near-real-time waveform display to the existing Channels tab. Showco
will consume Recs' forthcoming public waveform subscription API; Recs remains
responsible for calculating waveform envelopes from audio frames.

The display is a monitoring surface, not an audio editor or persisted
waveform. It shows recent signal history only and does not add recording
controls, change Recs' recording configuration, or alter the existing channel
name and stereo controls.

Smooth movement is the priority. The display may receive audio summaries at a
modest rate, but it must scroll continuously on the browser animation clock
rather than visibly jumping when each summary arrives.

## Recs Contract

Depend on Recs' public waveform models and subscription client once that API is
published. Showco needs these existing concepts, without duplicating their
calculation or validation:

- a layout, keyed by `source` and `generation`, containing the sample rate,
  bucket size, and ordered tracks;
- tracks identified by one mono channel or two consecutive stereo channels;
- ordered batches with `sequence`, `start_frame`, `start_timestamp`,
  `present`, and per-channel minimum and maximum envelopes;
- `dropped_batches` to identify data discarded before it reached Showco.

Showco must only accept a batch whose source and generation match its current
layout. A new layout invalidates old batches for that source. Channel identity
is the existing `(device, channels)` pair, so a Recs waveform track maps
directly to an existing Channels card.

Keep Recs' initial bucket and batch defaults. At 20 ms buckets and 100 ms
batches, Recs supplies enough time resolution for a useful canvas envelope;
Showco does not need a waveform-rate option in its own configuration.

## Runtime Data Path

1. Add a small Showco waveform bridge beside `RecsClient`. It owns one
   long-lived connection to the public Recs waveform API, requests the
   subscription, receives layouts and batches, and reconnects after a normal
   Recs restart or connection loss.
2. The bridge keeps the newest bounded recent history per source and publishes
   layouts and new batches to interested browser clients. Its queues are
   coalescing queues: when a browser or the server falls behind, discard old
   batches and retain the most recent one, recording a discontinuity rather
   than allowing latency to grow.
3. Add a standard-library Server-Sent Events endpoint, `/waveforms`. It sends
   the current layout followed by waveform batches. It is separate from
   `/status`: status remains the one-second control/status poll and is not made
   larger or faster for waveform data.
4. Begin the Recs subscription when Showco starts, so the first Channels page
   can immediately receive the retained recent history. The bridge continues
   independently of individual browser connections. Stop it during server
   shutdown.
5. Keep the bridge and SSE handler outside the action lock. A slow or closed
   browser must not delay recording actions, `/status`, or the Recs waveform
   reader. The SSE handler sends a small periodic comment heartbeat and exits
   cleanly on a broken client connection.

The EventSource browser connection is established only on the Channels page.
It reconnects with normal EventSource behaviour after a page reload or a
temporary Showco connection failure. Reconnection starts from current retained
history; it does not attempt an unbounded replay.

## Channels Page Rendering

Add `site/waveform-script.js` and load it only from `channels_page`. Add a
canvas to each existing `.level` card, identified by its existing device and
channel data attributes. Add the minimal waveform CSS to `site/server.css`:
each canvas has a stable height, fills the card width, and uses device pixels
correctly on high-density displays.

The script maintains an in-memory ring buffer for each `(source, channels)`
track, with a fixed recent window such as eight seconds. Each bucket is stored
as its measured minimum, maximum, presence bit, and time position. It draws
vertical min-to-max strokes, preserving both sides of a stereo track in the
matching stereo card.

Use `requestAnimationFrame` as the renderer's clock:

1. Position the newest envelope buckets on a time axis derived from the Recs
   batch timestamps and bucket duration.
2. Advance that axis every animation frame, even when no new batch has
   arrived. The waveform therefore moves smoothly at the browser's display
   cadence while retaining Recs' actual 100 ms data cadence.
3. Render only the canvas pixel columns that are visible. When several buckets
   fall in one column, combine them by the lowest minimum and highest maximum.
4. Do not interpolate amplitude values. An absent bucket, a sequence gap, a
   positive `dropped_batches`, a layout generation change, or a timestamp gap
   is rendered as a visible gap or reset in the time line.

Do not let the existing one-second `updateChannels()` implementation replace
all Channel card DOM nodes. Reconcile cards by their `(device, channels)` key
instead, preserving each canvas and its renderer state when status updates
arrive. New, removed, or changed channel layouts create or remove their
matching canvas deliberately.

When Recs has no active waveform subscription, no matching source, or no
present buckets, show the normal channel card with an empty baseline. This is
not an error state. Recs connection errors remain represented through the
existing Health and Errors surfaces.

## Server Models And Errors

Introduce small Showco-owned runtime data types for a current layout and a
bounded batch buffer only if the public Recs models cannot be used directly at
the bridge boundary. Do not mirror envelope fields into `ShowStatus`, write
them to disk, or put them into the existing status JSON.

Log waveform bridge connection, protocol, and write failures through Showco's
existing `reccy.logging` logger. Rate-limit repeated reconnect failures so an
offline Recs daemon cannot fill `showco.log`. Do not log ordinary batches,
empty signal, browser disconnects, or successful reconnects at error level.

## Tests And Verification

1. Unit-test the bridge with fake public Recs waveform messages: subscription,
   layout replacement, valid-batch acceptance, generation mismatch rejection,
   bounded coalescing, and Recs disconnect/reconnect.
2. Test the SSE endpoint with a fake bridge. Verify its content type, initial
   layout, batch event shape, current-history replay, and a slow-client backlog
   becoming a discontinuity rather than stale output.
3. Extend server-page tests to verify that every rendered channel card has the
   stable waveform canvas identity and that the Channels page alone loads the
   waveform script.
4. Test the browser-side reconciliation and rendering helpers with deterministic
   layouts and batches, including mono, stereo, missing `present` buckets,
   dropped batches, and generation changes. Assert time positions and combined
   min/max columns rather than browser frame rate.
5. Run the focused Showco tests, then the repository verification commands
   required for Python changes.
6. Manually run Recs in waveform mode with real audio. Confirm that a quiet
   input has a baseline, sound produces the expected envelope, the trace moves
   smoothly between 100 ms batches, status polling does not erase it, and a
   Recs restart yields a temporary blank trace followed by the new layout.

## Implementation Order

1. Confirm the final public Recs waveform import and subscription entry point;
   use it directly from Showco.
2. Implement and test the bounded Recs waveform bridge.
3. Add the `/waveforms` SSE endpoint and its tests.
4. Add canvas markup, CSS, and the Channels-only waveform script.
5. Change channel status reconciliation so it preserves waveform canvases.
6. Add rendering tests and perform the manual live-audio acceptance test.

## Additional Work Beyond The Prompt

None.
