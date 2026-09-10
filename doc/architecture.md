# Showco Architecture

Showco is the operator-facing web service for a small live-show system. It
coordinates other programs while leaving recording, lighting, and streaming in
their dedicated services.

## Deployment

There are two machine roles:

- The provisioning machine holds configuration and secrets, publishes local
  repositories, and reaches the target over SSH.
- The target Raspberry Pi runs the show services and exposes Showco to the show
  network.

The target keeps `reccy`, `recs`, `streamo`, `lyte`, and `showco` as sibling Git
checkouts under `[paths].root`. Each project has its own locked `uv` environment.
They share one uv-managed Python 3.13 installation and package cache.

The target uses user-level systemd services with user lingering enabled:

| Service | Responsibility | Showco integration |
| --- | --- | --- |
| `recs.service` | Record audio, MIDI, and configured OSC nodes. | Status, control, configuration, and waveforms over public Reccy RPC endpoints. |
| `showco.service` | Serve the web UI and monitoring sampler. | Browser-facing service. |
| `lyte.service` | Render and transmit lighting output. | Status and light tests over Reccy RPC when enabled. |
| `streamo.service` | Stream to Twitch and expose stream controls. | Status and operator actions over Reccy RPC when enabled. |

Unavailable optional services are represented as disabled or offline. Recs,
mixer, MIDI, OSC, and monitoring failures are reported without preventing the
HTTP service from starting.

## HTTP service

`showco run` creates a standard-library `ThreadingHTTPServer`. HTML is rendered
in `showco/server.py`; CSS and JavaScript are read once from `site/` using
`functools.cache`.

The routes are:

| Method and path | Purpose |
| --- | --- |
| `GET /`, `GET /channels` | Channels, track names, stereo controls, and waveforms. |
| `GET /health` | Recording, streaming, service, mixer, OSC, MIDI, and machine health. |
| `GET /attributes` | Mutable Recs settings. |
| `GET /actions` | Operator actions and the ten most recent action results. |
| `GET /errors` | Up to 25 Recs errors from the current Showco run. |
| `GET /status` | Current status as JSON for browser polling and deployment checks. |
| `GET /waveforms` | Server-sent Recs waveform events. |
| `POST /actions` | Serialized Recs, Lyte, or Streamo action dispatch. |

Ordinary form posts redirect to the Actions page. Requests with
`Accept: application/json` receive the action result directly. Ordinary request
handling is limited to eight concurrent requests; waveform streams have a
separate limit of four. Actions are serialized so concurrent browser requests
cannot issue overlapping service mutations.

## Recs

`RecsControlClient` creates a public `reccy.protocol.rpc.Client` for each
request and serializes access because Recs permits one outstanding Showco
control request. Operator actions have a six-second timeout. Status snapshots
have a 250 ms timeout and are cached for one second.

`status_snapshot` is the web UI's source for recording and pause state, channel
rows, errors, recording-disk state, MIDI inputs, and OSC recorders. After a
transport failure, the last valid snapshot remains visible and is marked stale.
An invalid response is marked as an error. Errors older than the current Showco
process are filtered from the web pages.

Waveform support uses Recs' public event endpoint. Showco connects the event
client, requests `subscribe_waveforms`, retains bounded layout and sample
history, and serves it to browsers through `/waveforms`. It reconnects after an
event failure and sends a full resynchronization when a browser falls behind.
On shutdown it requests `unsubscribe_waveforms` and closes the event client.

Showco does not use a private Recs GUI protocol. Provisioning does inspect
Recs' atomically written `~/.local/state/recs/status.json`, but only to verify
that the deployed daemon continues publishing status.

## Mixers and OSC

Mixer configuration combines three independent signals:

- Recs-reported audio device names.
- Recs-reported MIDI input names.
- An optional cached TCP or UDP network probe.

A mixer can therefore be waiting, partially ready, connected, or in error. A
network probe is attempted at most once every five seconds with a 500 ms
timeout. The current UDP probe sends `/xremote` and requires a UDP response;
this is only a reachability heuristic and must be checked against real mixer
control during hardware acceptance.

Recs owns generic OSC recording. Showco converts each mixer's OSC subscription
configuration into Recs node configuration and displays the node status. For an
X18, `/xremote` is periodically renewed to retain feedback; successful renewals
are not written as recording events.

USB audio and Ethernet control are separate paths. In private and mixed network
topologies, NetworkManager bridges the private Wi-Fi access point and Ethernet
so the tablet and mixer share the internal subnet. In public topology, Ethernet
is configured directly on that subnet.

## Lyte and Streamo

When Lyte is enabled, Showco polls its Reccy RPC status and exposes a one-second,
30-percent light test. The first transition to connected during a Showco run
automatically queues the same test. A later disconnect permits another test
when Lyte reconnects.

When Streamo is enabled, Showco polls its Reccy RPC status and exposes restart,
mute, unmute, stop, stream-information, chat, announcement, clip, and marker
actions. Disabled Lyte and Streamo services do not produce their action controls.

## Performance monitoring

The web service starts one sampler thread in normal target mode. Once per
second it reads Raspberry Pi temperature, aggregate CPU use, memory use, and
Recs' cached recording-disk state. The Health page displays the latest values.

At each UTC minute boundary, Showco appends an aggregate JSON object to
`~/.local/state/showco/monitoring/YYYY-MM-DD.jsonl`. Records contain CPU average
and peak, memory average and peak, minimum recording-disk free space, the latest
space estimate, disk alert and pause occurrence, and metric errors. Seven UTC
calendar days are retained. Clean shutdown writes the partial final minute.

Sampling and history-write failures are logged only when their state changes;
they do not make the web service unavailable.

## Provisioning and updates

`showco` and `showco go` choose between provisioning and updating by comparing
the resolved configuration and generated provisioning script with the
fingerprint stored on the target after the last successful provision.

Provisioning validates local inputs, publishes and synchronizes the five local
repositories, verifies passwordless SSH and sudo, uploads a generated Bash
script, and configures packages, storage, Python, repositories, networking, and
services. It reboots only when the target records that one is required, then
performs bounded service and hardware checks before storing the fingerprint.

An update publishes selected local repositories and downstream consumers,
refreshes their internal dependency lockfiles, updates disposable target
checkouts, synchronizes changed environments, restarts affected services, and
verifies applicable services. `--remote` skips local repository work and
updates the target from GitHub. `--push` and `--sync` stop before contacting the
target.

See `doc/provisioning.md` for command and configuration details.

## Persistent state and logs

Showco's persistent state is under `~/.local/state/showco/`. It includes its
combined service log, monitoring history, provisioning fingerprint, and service
configuration hashes. The network configuration hash is stored under
`~/.config/showco/`. Recs owns web-edited mutable settings in
`~/.config/recs/settings.json`; updates clear that file by default.

Each managed service writes combined stdout, stderr, and application logging to
`~/.local/state/<service>/<service>.log`. `showco logs` reads those files from
the provisioning machine over SSH. The web UI retains only current Recs errors
and ten in-memory action results, so it is not a replacement for service logs.
