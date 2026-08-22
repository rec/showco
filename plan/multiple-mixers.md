# Multiple Mixers (fix #5)

## Scope

Allow one Showco target to use multiple mixers with different capabilities. The
initial deployment has:

- an X18, recorded over USB, monitored over UDP, and recorded as an OSC node;
- a Behringer Flow 8, recorded over USB and with its USB MIDI input recorded by
  Recs, but without a network reachability probe.

The target must start successfully when neither mixer is connected. The X18
may appear minutes later and the Flow 8 hours later; each device must become
available without reinstalling or restarting either service. Their initial
absence is normal operational state, not an error.

Showco owns the target's mixer inventory, service installation, and health
presentation. Recs continues to own audio, MIDI, and OSC capture. A mixer is
not a Showco-controlled device: this change adds no mixer control actions,
device profiles, or audio routing.

## Current State

The current configuration and provisioning path have one X18-shaped path:

- `[usb].x18_device_name` becomes repeated Recs `--include` arguments.
- `networks.internal.wired.x18` supplies `SHOWCO_X18_HOST`.
- provisioning generates an X18-only Recs OSC configuration and passes one
  UDP mixer probe to Showco.
- `ShowStatus` and the Health page carry and display one `MixerStatus`.

Recs already supports repeated audio include selectors and MIDI input selectors
through `--include` and `--midi-include`. Its MIDI recorder writes one file per
selected input and its OSC recorder already accepts several named nodes. The
audio device lifecycle discovers eligible USB inputs after Recs starts and
already stops and restarts an audio source when it disappears and reappears. It
currently records `No input devices detected` as an error before any device has
arrived. The MIDI recorder does not discover late ports at all: it enumerates
only at startup. The Flow 8 therefore requires generic Recs MIDI delayed-device
work, not a Flow-8-specific recorder.

## Configuration Model

Add a frozen Pydantic `DeviceSpec` to `recs.cfg.device`. It represents declared
recording hardware, including hardware that is not connected yet:

- `name`: stable unique name.
- `audio_device_names`: sounddevice name prefixes.
- `midi_input_names`: mido input name prefixes.

Do not reuse Recs' existing `InputDevice`: it is a discovered sounddevice
object with live channel and sample-rate data, so it cannot represent a Flow 8
that will arrive later or a MIDI-only device. `DeviceSpec` is the shared
configuration contract instead. Recs uses it for selector validation and
matching; Showco imports it rather than duplicating its device fields.

Define `MixerSpec(DeviceSpec)` as a frozen Pydantic model in
`showco/provision/config.py`, adding mixer-specific health and OSC fields.
Replace the X18-only USB configuration with a list of `MixerSpec`s:

```toml
[[mixers]]
name = "X18"
audio_device_names = ["X18", "XR18"]

[mixers.probe]
host = "10.43.0.18"
port = 10024
protocol = "udp"

[mixers.osc]
host = "10.43.0.18"
port = 10024
subscription_path = "/xremote"
resubscribe_period = 10

[[mixers]]
name = "Flow 8"
audio_device_names = ["FLOW 8"]
midi_input_names = ["FLOW 8"]
```

`MixerSpec` adds these fields to `DeviceSpec`:

- `probe`: optional `MixerProbeSpec` with `host`, `port`, and `tcp` or `udp`
  protocol.
- `osc`: optional `MixerOscSpec` with a destination and the one subscription
  required for the target. It is absent for mixers without OSC.

Validate `DeviceSpec` names and selectors in Recs. Validate per-file unique
mixer names, valid ports, complete probe endpoints, and a positive OSC
resubscribe period in Showco. Require an OSC endpoint to be complete
independently of a probe, because an OSC node does not imply a useful
reachability probe.

The X18 wired-network entry remains responsible only for NetworkManager bridge
configuration. Remove `[usb].x18_device_name` and do not retain a compatibility
fallback. Provisioning obtains the X18 bridge address from the existing network
entry and validates it agrees with the X18 mixer specification's OSC/probe host.

## Recs Installation

Change `provision_locally.tmpl.sh` to derive Recs arguments from all configured
mixers:

1. Flatten `audio_device_names` in mixer order and emit one `--include` for
   each distinct value.
2. Flatten `midi_input_names` in mixer order and emit one `--midi-include` for
   each distinct value.
3. Build one Recs OSC TOML file containing a node for every `MixerSpec.osc`.
   Use the mixer name as the node name and emit its subscription path and
   resubscribe period. Pass that file once with `--osc-nodes` when it has nodes.
4. Do not create an OSC file when no mixer has OSC configuration.

Generate the argument lists and OSC TOML from the validated provisioned config,
not shell parsing of TOML. Pass the rendered values to the remote script as
quoted environment values or an uploaded generated file. This avoids trying to
reimplement TOML parsing in Bash and keeps the existing target script limited
to service installation.

Retain the existing audio lifecycle's reconnect behavior. When at least one
`DeviceSpec` exists, make missing matching audio inputs `waiting` and prevent
them from emitting `No input devices detected`; retain that warning for the
unconstrained recorder with no discovered inputs.

Extend `MidiRecorder` to apply `DeviceSpec` MIDI selectors during each bounded
device-discovery interval, rather than only in `start()`:

1. Start with every declared MIDI input in `waiting` state when it is absent.
2. Open and create a writer when a matching port appears, without restarting
   Recs or resetting the recording session.
3. When an open port disappears or fails, close it, retain its completed MIDI
   file record, change it back to `waiting`, and discover it again later.
4. Treat an initially absent declared audio or MIDI input as status information,
   not a warning or error. Retain warnings for an actual open, read, or write
   failure.

The resulting status must expose `waiting`, `recording`, and failure states so
Showco can distinguish a mixer that has not arrived from one whose active port
failed.

## Showco Runtime And Status

Replace the singular `MixerMonitor` dependency with a `MixersMonitor` that owns
a dictionary of `MixerMonitor`s keyed by `MixerSpec.name`.

- `MixerMonitor` remains the small cached TCP/UDP probe implementation.
- A mixer without `probe` derives its state from the matching Recs audio and
  MIDI status. It is `waiting` until declared hardware appears, then
  `connected` or `partial` according to its declared inputs. It must not be
  reported offline merely because it has no IP endpoint.
- A mixer with a probe reports a probe result alongside its Recs source state.
  Before the mixer has booted, an absent response is `waiting`, not an error
  record. A later failed probe is visible in Health without making the Recs
  recording service unhealthy.
- `MixersMonitor.status()` returns a name-sorted list of `MixerStatus` values.
- `MixerStatus` gains `name`, `state`, and declared-input progress while
  retaining probe latency and errors; `ShowStatus.mixer` becomes `mixers`.

Pass the rendered mixer configuration to `showco run` through one
`--mixers-config PATH` option. `WebUiOptions`, `services.install_showco_service`,
`showco_args`, and the service-install command all use that file, replacing the
three singular `--mixer-*` arguments. Rehearsal mode supplies two named fake
statuses so the normal browser polling path exercises a list.

Update the Health page and `site/status-script.js` to render a stable list of
named mixer rows, for example `X18: waiting for mixer` and `Flow 8: waiting for
USB audio and MIDI`. Refresh rows by mixer name from `/status`; do not collapse
the list to one aggregate light. Channel rendering remains based on Recs'
device names and is unchanged.

## Provisioning And Verification

Replace the X18-specific USB verification with one verification result per
configured `audio_device_names` selector. Add one MIDI verification result per
configured MIDI selector using the same Recs matcher, run in the target Recs
environment. A missing mixer is a named `waiting` note, not a provisioning
failure. Do not require every mixer during provisioning: it is normally run
before the Flow 8 is connected.

Keep the X18 bridge verification because it tests network configuration, not
the generic mixer model. Add no Flow 8 network setup or probe: USB audio and
MIDI are its declared capabilities.

## Tests

1. Validate shared Recs `DeviceSpec` fields and Showco mixer fields separately,
   including duplicate names, incomplete probes, invalid ports, and
   non-positive resubscribe periods.
2. Verify Recs starts with selected audio and MIDI hardware absent, reports
   `waiting` without warnings, and discovers each input when it appears later.
3. Verify an unplugged MIDI input closes its file, returns to `waiting`, and
   resumes into a later file when it reappears.
4. Verify provisioning renders ordered, de-duplicated Recs audio and MIDI
   selectors and a two-node OSC TOML file when appropriate.
5. Verify no OSC configuration or `--osc-nodes` argument is produced when no
   mixer declares OSC.
6. Unit-test `MixersMonitor` with a waiting network mixer and an unprobed
   mixer whose Recs audio and MIDI inputs appear at different times.
7. Update server and status-JSON tests for the `mixers` list and confirm browser
   polling updates named rows across `waiting`, `partial`, and `connected`.
8. Update service-argument and provisioning tests to ensure no singular
   `--mixer-host`, `--mixer-port`, or `[usb].x18_device_name` path remains.
9. On the target, boot the Pi before connecting either mixer; connect the X18
   after several minutes and the Flow 8 later; verify each becomes connected,
   the Flow 8 MIDI file grows, and no initial absence entered the error list.

## Implementation Order

1. Add the Recs-owned `DeviceSpec` and matching helpers, then migrate Recs
   audio and MIDI selector configuration to use them.
2. Implement Recs MIDI discovery, waiting state, disconnect handling, and
   reopen behavior.
3. Add and validate provisioned `MixerSpec`, replacing the X18 USB field.
4. Render Recs audio/MIDI arguments and the OSC TOML from mixer specifications.
5. Replace singular Showco probe options and models with the named mixer list.
6. Render and poll the Health-page mixer list.
7. Generalize provisioning hardware verification and update its tests.
8. Run the full Showco and focused Recs test suites, then perform the staged
   target hardware test with both mixers connected at different times.

## Additional Work Beyond The Prompt

None.
