# Recs protocol used by Showco

Showco uses Recs protocol version 2 over the daemon GUI endpoint. Version 2 is
not compatible with the former version-1 command envelope.

Each connection begins with:

```json
{"type":"hello","role":"gui","version":2}
```

Recs responds with daemon hello at version 2. Showco then sends one typed
request and reads its direct typed response before making another request on
that connection. The protocol has no request IDs and no generic reply message.

Showco uses these requests:

```json
{"type":"calibrate"}
{"type":"get_track_names"}
{"type":"set_track_names","track_names":{"Mic":{"Lead Vocal":1}}}
{"type":"set_noise_floor","source":"Mic","noise_floor":42.5}
{"type":"mark","label":"guitar solo"}
{"type":"set_key_label","key":"g","label":"guitar solo"}
{"type":"pause_recording"}
{"type":"resume_recording"}
{"type":"start_recording"}
{"type":"stop_recording"}
{"type":"capabilities"}
{"type":"disk_status"}
{"type":"list_devices"}
{"type":"reload_profiles"}
{"type":"status_snapshot"}
```

Examples of corresponding response types are `calibrated`, `track_names`,
`noise_floor_set`, `marked`, `key_label_set`, `recording_state`,
`capabilities_result`, `disk_status_result`, `devices`, `profiles_reloaded`,
and `status_snapshot_result`. Failures use `{"type":"error","message":"..."}`.

`shutdown` remains its own message. Recs broadcasts it to listeners and closes
their connections:

```json
{"type":"shutdown"}
```

The full protocol definition is in Recs at `doc/recs_protocol.md`.
