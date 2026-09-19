# showCo

showCo is the browser-facing control and status service for a self-contained
live-show system. It coordinates recording through recs, optional lighting
through lyte, optional streaming through streamO, mixer reachability,
and Raspberry Pi health without taking ownership of those services' core work.

showCo requires Python 3.13. The deployed system uses five sibling repositories:
`reccy`, `recs`, `streamo`, `lyte`, and `showco`.

## Web interface

The target runs a standard-library `ThreadingHTTPServer`. Its pages are:

- **Performance**: large runtime controls, pinned inputs, dimming, and fault visibility.
- **Set list**: songs, notes, durations, and manual next-song markers on Performance.
- **Soundcheck**: saved disk, input, sample-recording, playback, and optional output checks.
- **Recovery**: diagnostics, confirmed service restarts, and separate recovery verification.
- **Channels**: recording state, track names, stereo grouping, and live
  waveforms reported by recs.
- **Health**: service state, CPU, memory, recording-disk use, temperature,
  mixers, MIDI, OSC recorders, and recent recs errors.
- **Attributes**: mutable recs configuration.
- **Musicians**: add and edit recs musician identities, aliases, public keys, and contacts.
- **Actions**: recs, lyte, and optional streamO commands with recent results.
- **Errors**: recs errors from the current showCo run.

The root URL is the Channels page. `GET /status` provides the current JSON
status snapshot, and `GET /waveforms` provides the waveform event stream.

Every page shows the persistent performance lock and active-fault banner. The target
monitors services without an open browser and retains its latest 100 incident events.

## Commands

Run these on the provisioning machine unless stated otherwise:

```bash
showco                         # provision if configuration changed, else update
showco go                      # same as showco
showco go recs showco          # update selected repositories and consumers
showco go --remote             # update the target directly from GitHub
showco go --upgrade            # provision and run apt-get upgrade
showco --push [repository ...] # publish local histories only
showco --sync [repository ...] # publish and synchronize internal lockfiles
showco logs [service ...]      # read target service log files
showco python 'CODE'           # run one Python expression on the target
showco prepare-card            # prepare and eject an Imager-written SD card
```

Ordinary provisioning installs missing packages without running `apt-get upgrade`; add `--upgrade` when a package upgrade is intended.

Internal target commands live under `showco run`. For local UI development,
`showco run --rehearsal` starts the web server with simulated recs, streamO,
system, and mixer data. It does not simulate waveform events or lyte.

## Documentation

- [Operations and deployment](doc/README.md)

## Development

Install the locked environment and run the checks with `uv`:

Use a Git source checkout, including the top-level `site/` assets, on both the
development machine and target. Standalone wheel installations are not supported.
The browser regression tests also require Node.js on the development machine.

```bash
uv sync --locked
uv run pytest
uv run ruff check showco test
uv run ty check showco
```

`uv run pytest` uses four workers and keeps each test file in one worker. Run
`uv run pytest -n 0` to reproduce a failure serially.

Automated tests do not establish that audio was recorded, a mixer is reachable,
lighting frames were displayed, or the streaming service accepted an operation. Use the
hardware acceptance tests before relying on a deployed system.
