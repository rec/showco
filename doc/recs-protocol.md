# Recs protocol used by Showco

This document describes the exact current messages and files that Showco uses to
communicate with `recs`.

Showco reads the Recs daemon status file for low-rate status and connects to the
Recs GUI IPC endpoint for control commands.

The current control command exposed by Recs is live noise-floor calibration.
Recs does not expose GUI IPC commands for starting recording, stopping
recording, or changing arbitrary recording configuration.

## Status file

Showco reads the Recs daemon status file.

Platform paths:

- macOS and Linux: `~/.local/state/recs/status.json`
- Windows: `%LOCALAPPDATA%\recs\status.json`

The file contains one JSON object. Current fields are:

```json
{
  "client_count": 0,
  "gui_ipc_error": null,
  "rows": [],
  "recording": false,
  "updated_at": 0.0
}
```

Field meanings:

- `client_count`: number of connected GUI IPC clients.
- `gui_ipc_error`: latest GUI IPC startup error, or `null`.
- `rows`: current display rows, described below.
- `recording`: whether the daemon reports that recording is active.
- `updated_at`: Unix timestamp of the last daemon status update.

Showco treats the Recs status as stale when `updated_at` is more than three
seconds old.

## Row objects

`rows` is the same table-oriented data that Recs sends to its GUI. Every row is a
JSON object. Fields are sparse, so a row only includes values that apply to that
row.

The total row may contain:

```json
{
  "time": 12.34,
  "recorded": 11.2,
  "file_size": 123456,
  "file_count": 2
}
```

A device row may contain:

```json
{
  "device": "MacBook Pro Microphone",
  "on": "active"
}
```

A channel row may contain:

```json
{
  "channel": "1",
  "on": "active",
  "recorded": 11.2,
  "file_size": 123456,
  "file_count": 1,
  "signal": 0.42,
  "volume": 0.42
}
```

Current `on` values are produced by Recs and are treated by Showco as display
data. Showco does not send them back to Recs.

Showco maps channel `signal` to four display states:

- missing, `null`, or less than `0.001`: `silent`
- at least `0.001` and less than `0.3333333333`: `present`
- at least `0.3333333333` and less than `0.9`: `healthy`
- at least `0.9`: `clipping`

## GUI IPC endpoint

Recs stores the GUI endpoint in daemon metadata.

Metadata paths:

- macOS and Linux: `~/.config/recs/daemon.json`
- Windows: `%APPDATA%\recs\daemon.json`

The current metadata object is:

```json
{
  "version": 1,
  "argv": [],
  "executable": "/path/to/recs",
  "platform": "linux",
  "gui_endpoint": "/home/user/.local/state/recs/gui.sock"
}
```

Endpoint values:

- macOS and Linux: Unix-domain socket path.
- Windows: named pipe string `\\.\pipe\recs`.

## Messages sent from Showco to Recs

The first live IPC message sent by Showco is the GUI hello:

```json
{"type":"hello","role":"gui","version":1}
```

Recs requires this hello before any other live IPC message.

After the hello succeeds, Showco can ask Recs to calibrate per-device noise
floors from the audio observed so far:

```json
{"type":"command","id":"c1","command":"calibrate"}
```

The `id` field is an arbitrary Showco-chosen string. Recs echoes the same `id`
in the reply.

Recs also accepts key event messages after the hello:

```json
{"type":"key_pressed","key":"g"}
{"type":"key_released","key":"g"}
```

Showco does not currently send key events.

## Messages received by Showco from Recs

After a valid hello, Recs replies:

```json
{"type":"hello","role":"daemon","version":1}
```

Recs then sends live row updates:

```json
{"type":"rows","rows":[{"device":"MacBook Pro Microphone","on":"active"}]}
```

Successful calibration reply:

```json
{
  "type": "reply",
  "id": "c1",
  "ok": true,
  "result": {
    "measurements": {
      "MacBook Pro Microphone - 1": 6.020599913279624,
      "(all)": 6.020599913279624
    },
    "profiles": {
      "MacBook Pro Microphone": {
        "noise_floor": 12.0
      }
    },
    "profiles_path": "/home/user/recs-profiles.json"
  }
}
```

Failure reply when no profiles file is configured:

```json
{
  "type": "reply",
  "id": "c1",
  "ok": false,
  "message": "Cannot calibrate noise floor without --profiles"
}
```

If Recs rejects a message, it sends:

```json
{"type":"error","message":"GUI hello required before other messages"}
```

The known current error for a version mismatch is:

```json
{"type":"error","message":"GUI protocol version 2 is not supported; daemon requires 1"}
```

## Commands not currently available

The current Recs daemon does not expose JSON messages for:

- starting recording
- stopping recording
- changing arbitrary recording configuration
