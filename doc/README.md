# Showco

Showco is the operator web UI for a small live-show system. It reports and controls Recs recording, mixer reachability, Raspberry Pi health, Lyte lighting, and Streamo streaming. Those programs retain responsibility for recording, lighting, and streaming themselves.

Showco runs on a Raspberry Pi. A provisioning machine configures and deploys it. The target has sibling checkouts of `reccy`, `recs`, `streamo`, `lyte`, and `showco`, each with its own locked `uv` environment.

## Perform a show

Open Showco on the show network at the configured address and port. The Health page is the first place to look: **Ready to perform** is ready only when Recs is connected and recording, the recording disk is usable, every configured mixer is connected, and enabled Lyte and Streamo services are connected.

| Page | Use it for |
| --- | --- |
| Channels | Watch input levels and waveforms. Edit track names and stereo groups, then save them. |
| Health | Check readiness, recording and streaming state, disk space, CPU, memory, temperature, mixer and input status, current Recs errors, and incidents from this Showco run. |
| Playback | Play and navigate available Recs recordings. |
| Attributes | View and edit mutable Recs settings. Changes are saved when an input loses focus. |
| Actions | Calibrate, mark, start a session, pause or resume, inspect Recs status, test lights or cables, operate Streamo, and review the ten most recent results. |
| Errors | See up to 25 Recs errors from the current Showco process. |

The browser refreshes status and channel data. Waveforms use a dedicated event stream and resynchronize after a delayed browser connection. An unavailable optional service does not make the other pages unavailable.

### Before the performance

1. Boot the Pi, then open Health from the tablet.
2. Confirm the intended removable recording disk, free space, and plausible remaining duration. Do not assume recordings are using a particular mount point or the Pi SD card.
3. Confirm each configured audio and MIDI device, mixer, and enabled service.
4. Check the Channels page while sound is present and make any required track name or stereo changes.
5. Start and stop a brief recording. Confirm that files grow on the removable disk, remain readable after stopping, and survive an unmount and remount.
6. When lighting or streaming is enabled, confirm their output with the real equipment. A green web status is useful evidence, but it is not proof that audio reached storage, lights emitted, or the audience received the stream.

For a full installation or major-change check, also power-cycle the Pi, test devices arriving after boot, confirm tablet and mixer network access, and run a five-hour soak with every live service. Record the date, deployed commits, hardware, recording destination, and pass/fail result outside this repository.

### Actions that affect a live show

- **Pause recording**, **resume recording**, and **start new recording session** change Recs recording. Use the action result and the Health page to confirm the new state.
- The named marker buttons create `show start`, `song start`, `song end`, `set break`, or `show end` markers. A custom marker is also available.
- **Shutdown Recs daemon** requires choosing the shutdown confirmation. It stops the recorder.
- **Test lights** runs Lyte's one-second, 30-percent test and leaves the lights off. Showco automatically runs the same test once when Lyte first connects.
- **Test X18 cables** pauses audio recording while it runs, then restores it if it was recording. It temporarily changes X18 routing and restores the affected mixer settings afterward. The default sends the same tone to AUX 1-6 at once and records inputs 9-14, so the cables may be connected in any order. It reports input channels with no signal or a distorted signal. Results require at least 98 percent tone similarity, 70-130 percent level, and no clipping.
- Streamo actions appear only when streaming is enabled. They include restart, mute, unmute, stop, stream information, chat, announcement, clip, and marker actions.

## Configure and deploy

The source configuration is [`showco/provision/config.toml`](../showco/provision/config.toml). Private values belong in the ignored `showco/provision/secrets.toml`, which overlays the public configuration. Never commit Wi-Fi passwords, stream keys, OAuth tokens, client secrets, or private keys.

The important configuration is near the top of `config.toml`:

| Setting | Meaning |
| --- | --- |
| `[network]` | Target host, SSH user and port, web port, Wi-Fi ordering, and topology. The user defaults to the provisioning machine's `USER`. |
| `[paths].root` | Absolute target directory containing the five sibling repositories. |
| `[networks.internal]` | Required IPv4 mixer subnet. Wi-Fi and mixer `ip_address` values are host offsets in this subnet. |
| `[[mixers]]` | Mixer name plus the audio/MIDI name prefixes expected from Recs. Networked mixers provide both `ip_address` and `port`. |
| `[stream]` | Enable Streamo and provide its non-secret metadata. |
| `[lyte]` | Enable Lyte and name an installation configuration relative to the Lyte checkout. |

`network.topology` may be `public`, `private`, `mixed`, or empty. Public joins the external Wi-Fi and puts an X18 on Ethernet. Private creates the internal access point and bridges it to X18 Ethernet. Mixed uses one Wi-Fi interface for the private bridged network and a second for the external network. When empty, Showco selects a topology from the configured external network, available Wi-Fi interfaces, and whether streaming is enabled.

The first Raspberry Pi must already boot, have the intended user and hostname, accept SSH keys, and allow `sudo -n`. To prepare an Imager-written card before its first boot, run this on macOS:

```bash
showco prepare-card
```

It selects an external disk no larger than 256 GiB, requires the exact response `yes`, adds passwordless sudo to its existing cloud-init configuration, and ejects the card. It does not image the card.

Run `showco` or `showco go` from the provisioning machine for ordinary deployment. It compares the resolved configuration and generated provisioning script against the target fingerprint. A missing or changed fingerprint causes provisioning; a match causes an update.

Provisioning validates local repositories, publishes and synchronizes all five projects, checks SSH and passwordless sudo, configures the target, installs user services, and verifies the web UI, Recs progress, enabled services, networking, and configured inputs. It may reboot the target when necessary.

Ordinary provisioning updates the APT package index and installs missing base packages, but does not upgrade installed packages. Pass `--upgrade` to run `apt-get upgrade` as part of provisioning.

Use these explicit modes only when their effects are intended:

```bash
# Provision and run apt-get upgrade.
showco go --upgrade

# Publish and deploy selected repositories. Recs also updates Showco.
showco go recs

# Update the target from already-published GitHub commits.
showco go --remote recs showco

# Publish local work without contacting the target.
showco --push recs

# Publish local work and refresh internal dependency lockfiles, without target work.
showco --sync recs
```

Updates clear mutable Recs web settings by default, including track names and stereo groups. Pass `--no-clear-settings` for the one update that must preserve them. Do not use `showco go` as a casual diagnostic: it can publish histories, reset disposable target checkouts, restart services, clear Recs settings, and change network or system configuration.

## Diagnose a problem

Fetch combined target service logs from the provisioning machine:

```bash
showco logs
showco logs recs showco --lines 500
```

Logs are retained on the target at `~/.local/state/SERVICE/SERVICE.log` for `showco`, `recs`, `streamo`, and `lyte`. Showco itself keeps only the current process's Recs errors and ten recent action results.

For a small target-side inspection, use:

```bash
showco python 'import sys; print(sys.version)'
```

On the target, `showco bundle` writes a timestamped diagnostic bundle below `~/.local/state/showco/bundles`. `showco cable-test` runs the X18 test from the terminal; it accepts optional inclusive channel and AUX-send ranges, for example `showco cable-test 9-14 1-6`.

The Health page samples Pi temperature, aggregate CPU use, memory use, and Recs disk state every second. It writes one aggregate record per UTC minute to `~/.local/state/showco/monitoring/YYYY-MM-DD.jsonl`, keeps seven UTC calendar days, and writes the final partial minute during a clean shutdown. Sampling or history-write failures are logged and do not take down the web UI.

## Service and protocol boundaries

`showco.service` runs the standard-library HTTP server. Recs, Lyte, and Streamo are user services and communicate with Showco through their public Reccy RPC interfaces. Showco creates a Recs control client per request, serializes actions, gives operator actions a six-second timeout, and caches short status snapshots for one second. The last valid Recs snapshot remains visible and is marked stale after a transport failure.

The mixer state combines Recs-reported audio and MIDI input names with an optional TCP or UDP probe. For X18, the UDP probe sends `/xremote` and waits for a reply. It is a reachability hint only; confirm mixer control with the tablet application or real OSC feedback. X18 `/xremote` subscriptions are renewed for feedback, but successful renewals are not recording events.

The web service limits ordinary requests to eight concurrent connections and waveform event streams to four. Its browser routes are `/` and `/channels`, `/health`, `/playback`, `/attributes`, `/actions`, `/errors`, `/status` (JSON), and `/waveforms` (server-sent events). Form posts go to `/actions`; requests that accept JSON receive the action result directly.
