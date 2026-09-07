# Recs protocol used by Showco

Showco uses Recs' public Reccy RPC endpoints. The complete public contract is
documented in Recs at `doc/recs_protocol.md`; this document records the subset
and integration rules used by Showco.

## Control

`RecsControlClient` opens one `reccy.protocol.rpc.Client` connection per
request, using Recs' external control endpoint and the role `showco`. It
serializes all calls because Recs accepts only one outstanding control request.
Status requests use a 250 ms timeout; operator actions use the protocol's
six-second default.

Showco uses these data-returning commands and validates the response `type`:

| Command | Response type |
| --- | --- |
| `capabilities` | `capabilities_result` |
| `status_snapshot` | `status_snapshot_result` |
| `disk_status` | `disk_status_result` |
| `list_devices` | `devices` |
| `mutable_attributes` | `mutable_attributes_result` |
| `get_cfg` | `cfg_value` |
| `get_track_names` | `track_names` |
| `calibrate` | `calibrated` |
| `card_replace` | `card_replace_started` |
| `subscribe_waveforms` | `waveform_subscription` |
| `unsubscribe_waveforms` | `waveform_subscription` |

The mutation-only commands `set_cfg`, `set_track_names`, `set_tracks`,
`set_noise_floor`, `set_key_label`, `mark`, `pause_recording`,
`resume_recording`, `reload_profiles`, and `shutdown` must return the JSON
string `"ok"`.

The cached `status_snapshot` is the sole runtime source for the web UI's Recs
status. It supplies recording pause state, display rows, errors, recording-disk
status, MIDI inputs, and OSC recorders. If a request fails after a valid
snapshot, Showco retains the data but marks the service stale and displays the
new diagnostic. Invalid responses are errors rather than healthy empty data.

Provisioning still reads `~/.local/state/recs/status.json` remotely to verify
that the daemon's status publisher advances. That deployment check is separate
from the web adapter.

## Waveforms

Showco starts a public `rpc.EventClient` on Recs' external event endpoint before
calling `subscribe_waveforms`. It consumes `waveform_layout` and `waveform`
events, retaining bounded history for browser SSE clients. On shutdown it calls
`unsubscribe_waveforms` and closes the event client.

Showco does not use Recs' private GUI socket, daemon metadata, GUI handshake, or
private request models. It also does not execute the `recs control` command as a
subprocess; that CLI and Showco call the same public RPC protocol directly.
