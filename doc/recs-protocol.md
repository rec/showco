# Recs protocol used by Showco

This document describes the exact current messages and files that Showco uses to
communicate with `recs`.

The current Showco implementation reads the Recs daemon status file. It does not
currently open the Recs GUI IPC endpoint, so it sends no live IPC messages to
Recs.

The current `recs` daemon also exposes live GUI updates through a local GUI IPC
endpoint. That protocol is documented below because it is the protocol Showco
should use when it needs push updates or key-event support. Recs does not yet
expose daemon commands for calibration, starting, or stopping recording.

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

Current Showco sends no messages to Recs. It only reads
`~/.local/state/recs/status.json` or `%LOCALAPPDATA%\recs\status.json`.

If Showco later opens the GUI IPC endpoint, the first live IPC message must be
the GUI hello:


```json
{"type":"hello","role":"gui","version":1}
```

Recs requires this hello before any other live IPC message.

Recs also accepts key event messages after the hello:

```json
{"type":"key_pressed","key":"g"}
{"type":"key_released","key":"g"}
```

Showco does not currently send key events.

## Messages received by Showco from Recs

Current Showco receives no live IPC messages from Recs. It reads the status file
instead.

If Showco later opens the GUI IPC endpoint, Recs sends the following messages.

After a valid hello, Recs replies:

```json
{"type":"hello","role":"daemon","version":1}
```

Recs then sends live row updates:

```json
{"type":"rows","rows":[{"device":"MacBook Pro Microphone","on":"active"}]}
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

- calibrating noise floors
- starting recording
- stopping recording
- changing recording configuration

Showco therefore displays the Recs calibration action as unavailable until Recs
adds a command protocol for it.
