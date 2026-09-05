# Showco Architecture

Showco is the browser-facing control surface for a small, self-contained live
show system. It runs on the target Raspberry Pi and coordinates the recorder,
the X18 mixer, optional lighting, and optional Twitch streaming without taking
audio or mixer-control ownership from their dedicated programs.

## System Roles

There are two machines.

- The provisioning machine holds the operational configuration and uses SSH to
  provision and update the target.
- The target machine runs the show services and exposes the Showco web UI to
  the private show network.

The projects live as sibling checkouts below one configurable root directory:
`reccy`, `recs`, `showco`, `twitcho`, and `lyte`. Reccy supplies the shared
daemon, service, IPC, process, logging, and persistence facilities. The other
projects remain independently deployable services.

## Runtime Services

The target uses user-level systemd services with user lingering enabled:

| Service | Responsibility | Showco relationship |
| --- | --- | --- |
| `recs.service` | Capture and record mixer USB audio and record configured OSC nodes. | Showco reads its status and sends Recs control requests. |
| `showco.service` | Serve the local web UI. | The central browser-facing service. |
| `lyte.service` | Control lighting when enabled. | Installed and checked by provisioning and updates; it is not part of the Showco HTTP request path. |
| `twitcho.service` | Run Twitch streaming and its external controls when enabled. | Showco queries it and forwards explicit operator actions. |

Showco must remain usable when Recs, Lyte, Twitcho, a mixer, or an OSC recorder
is unavailable. Their failures are represented in status or action
results rather than preventing the HTTP service from starting.

## Web UI

`showco run` starts a standard-library `ThreadingHTTPServer`. It exposes:

- `GET /` and `GET /home`: the current status and Recs mutable-attribute form.
- `GET /actions`: explicit Recs and optional Twitcho controls.
- `POST /actions`: action dispatch, returning JSON when requested or redirecting
  to the actions page for ordinary form submissions.
- `GET /status`: a JSON snapshot for browser polling and deployment checks.

The server composes status from independent adapters:

- `RecsClient` reads the Recs daemon status and uses its two control protocols.
- `TwitchoClient` uses Reccy RPC when Twitcho is enabled.
- `MixerMonitor` probes the configured X18 endpoint, caching results briefly to
  avoid probing per browser request.
- `SystemMonitor` reports local Raspberry Pi information.
- `X18RecorderSupervisor` reports the state of the optional OSC subprocess.

HTTP request concurrency is bounded. Stateful control actions are serialized,
and the short recent-action log is protected by its own lock. Browser status
polling updates the display without requiring a page reload.

## Recs Integration

Showco intentionally uses two distinct Recs interfaces.

- Recs GUI IPC is its internal typed protocol. Showco uses it for calibration,
  track names, recording actions, and shutdown.
- Recs external Reccy RPC is used for mutable configuration. Showco requests the
  available addresses, fetches each value, and sends `set_cfg` when an operator
  changes a field.

Recs status is read from its atomically-written status file. A missing, invalid,
or stale file is reported as an unhealthy Recs service rather than interpreted
as a live recording state.

## X18 Integration

The X18 has two independent paths to the target:

- USB audio is owned by Recs.
- Ethernet carries mixer control. In the private topology, NetworkManager
  bridges the private Wi-Fi access point and the Pi Ethernet interface so the
  tablet can reach the mixer.

Recs owns generic OSC recording. Its configured nodes may subscribe, poll, or
listen for continuous telemetry and write timestamped JSONL recordings. Showco
shows each named recorder independently from mixer reachability. An X18 node
uses `/xremote` only to maintain its subscription and never changes mixer
state.

## Network Topologies

Network configuration is based on the provisioning TOML rather than hard-coded
interface names. The selected topology is one of:

- `private`: the selected Wi-Fi interface provides the private show network.
- `public`: the primary Wi-Fi joins an external network.
- `mixed`: the private access point uses one Wi-Fi interface and the external
  network uses a second interface.

The configured X18 wired network is either a direct Ethernet configuration for
the public topology or a bridge member in the private and mixed topologies.
Network reconfiguration creates a temporary rollback profile before changing
the private network so an error does not leave the target inaccessible.

## Provisioning And Updates

`showco go` runs on the provisioning machine. With no repository or remote
update option, it compares the resolved local provisioning configuration and
generated script with the fingerprint recorded by the target after its last
successful provision. It runs full provisioning when they differ, otherwise it
updates all repositories. Provisioning reads the non-secret configuration and
local secret overlay, validates them before making remote changes, waits for
SSH, uploads a generated Bash script, and runs that script on the target. The
remote script performs locale, package, storage, checkout, dependency, network,
and service setup. It reboots only when required, then verifies enabled
services, the Showco HTTP revision, and configured hardware conditions.

Repository arguments, `--autosquash`, or `--remote` select update mode. Normal
update mode expands selected libraries to their downstream consumers, checks
and publishes all affected sibling checkouts, refreshes and tests their locked
internal dependencies in dependency order, and normally pushes generated
lockfile commits before calling the target through SSH. Selecting Reccy includes
all repositories; selecting Recs also includes Showco. Autosquashed history uses
force-with-lease only against the upstream commit recorded before rewriting.
Generated dependency commits are never force-pushed. `--remote` updates the
target directly from GitHub without examining local checkouts or refreshing
dependencies. On the target, the update stops affected services, records their
commits, updates each checkout, synchronizes changed dependencies, restarts
services, and verifies Showco and Recs when applicable. The target checkout is
disposable: a failed target update may reset it to a known commit or upstream
state. The local development checkout is never reset by this process.

`showco python` is a developer-machine diagnostic shortcut that executes a
one-line Python expression in the target Showco checkout and environment.

## Configuration And Secrets

The configuration model mirrors the TOML structure.

- `showco/provision/config.toml` contains topology, paths, project URLs,
  enabled-service settings, and non-secret defaults.
- `showco/provision/secrets.toml` overlays passwords and streaming credentials.

The root directory can be supplied with `--root` and is persisted in the
configuration. Repository URLs are public HTTPS URLs, so target updates do not
need GitHub credentials.

## Operational Diagnostics

Each service writes combined application output to its own persistent file at
`~/.local/state/<service>/<service>.log`. Use `showco logs` to tail the known
service logs, or pass service names and `--lines` to narrow the output. The web
UI reports current dependency health and recent operator actions; it is not
intended to retain complete diagnostic history.

The acceptance and smoke-test documents define the software and hardware
evidence required before treating the system as ready for a show.
