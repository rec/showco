# Showco

Showco is the browser-facing control and status service for a self-contained
live-show system. It coordinates recording through Recs, optional lighting
through Lyte, optional streaming through Streamo, mixer reachability,
and Raspberry Pi health without taking ownership of those services' core work.

Showco requires Python 3.13. The deployed system uses five sibling repositories:
`reccy`, `recs`, `streamo`, `lyte`, and `showco`.

## Web interface

The target runs a standard-library `ThreadingHTTPServer`. Its pages are:

- **Channels**: recording state, track names, stereo grouping, and live
  waveforms reported by Recs.
- **Health**: service state, CPU, memory, recording-disk use, temperature,
  mixers, MIDI, OSC recorders, and recent Recs errors.
- **Attributes**: mutable Recs configuration.
- **Actions**: Recs, Lyte, and optional Streamo commands with recent results.
- **Errors**: Recs errors from the current Showco run.

The root URL is the Channels page. `GET /status` provides the current JSON
status snapshot, and `GET /waveforms` provides the waveform event stream.

## Commands

Run these on the provisioning machine unless stated otherwise:

```bash
showco                         # provision if configuration changed, else update
showco go                      # same as showco
showco go recs showco          # update selected repositories and consumers
showco go --remote             # update the target directly from GitHub
showco go --system             # provision and refresh OS packages
showco --push [repository ...] # publish local histories only
showco --sync [repository ...] # publish and synchronize internal lockfiles
showco logs [service ...]      # read target service log files
showco python 'CODE'           # run one Python expression on the target
showco prepare-card            # prepare and eject an Imager-written SD card
```

Internal target commands live under `showco run`. For local UI development,
`showco run --rehearsal` starts the web server with simulated Recs, Streamo,
system, and mixer data. It does not simulate waveform events or Lyte.

## Documentation

- [Architecture](doc/architecture.md)
- [Provisioning and updating](doc/provisioning.md)
- [Hardware acceptance tests](doc/acceptance-tests.md)

## Development

Install the locked environment and run the checks with `uv`:

```bash
uv sync --locked
uv run pytest
uv run ruff check showco test
uv run ty check showco
```

Automated tests do not establish that audio was recorded, a mixer is reachable,
lighting frames were displayed, or the streaming service accepted an operation. Use the
hardware acceptance tests before relying on a deployed system.
