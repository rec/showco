# showCo

showCo is the operator web UI for a small live-show system. It reports and controls recs recording, mixer reachability, Raspberry Pi health, lyte lighting, and streamO streaming. Those programs retain responsibility for recording, lighting, and streaming themselves.

showCo runs on a Raspberry Pi. A provisioning machine configures and deploys it. The target has sibling checkouts of `reccy`, `recs`, `streamo`, `lyte`, and `showco`, each with its own locked `uv` environment.

When streaming is enabled with an X18, provisioning enables recs audio forwarding
and configures streamO to subscribe to that recorder. recs alone opens the USB
input. The broadcast uses channels 17 and 18 by default, retaining an existing
configured stereo pair. The exact source key is resolved from recs at deployment;
an absent or ambiguous mixer fails configuration instead of opening a competing
hardware capture. Media and publishing credentials are preserved.

Before starting or restarting streamO from showCo, the X18 sends Main L/R
post-fader to USB 17–18. showCo confirms each routing setting before starting
the broadcast; an unconfirmed setting prevents startup. This applies to the
Twitch button, streamO restart and recovery actions, and entering Record mode, without
changing room faders or the other USB sends.

Run showCo from its Git source checkout, including the top-level `site/` directory. Installing showCo as a standalone wheel is not supported; provisioning maintains the required checkout layout.

## Perform a show

Open showCo on the show network at the configured address and port. The Health page shows **Service readiness**: recs must be connected and recording, the recording disk usable, every configured mixer connected, and enabled lyte and streamO services connected. This checks service state, not successful audio writes. Check recorded-audio progress separately; silence filtering may legitimately pause file growth.

| Page | Use it for |
| --- | --- |
| Show | First page: observed faults, Start/Stop Twitch, and End show. Twitch switching leaves recs unchanged. After a streamO error or disconnection, the button retries streamO. Starting is disabled after teardown; use Resume show on Actions. |
| Lighting cues | Named lighting looks, Go/Back, current/next cue position, and live lyte state. |
| Performance | Manual song cues, large marker and pause/resume controls, recording destination and capacity, stream state, pinned inputs, dimming, and optional screen-awake mode. |
| Set list | Prepare and reorder songs, notes, and expected durations. |
| Soundcheck | Save measured checks and explicit playback, lighting, and stream confirmations. |
| Recovery | Download diagnostics, explicitly restart a failed service, and verify recovery. |
| Channels | Watch input levels and waveforms. Edit track names and stereo groups, then save them. |
| Health | Check readiness, recording and streaming state, disk space, CPU, memory, temperature, mixer and input status, current recs errors, and incidents from this showCo run. |
| Playback | Play and navigate available recs recordings. |
| Attributes | View and edit mutable recs settings. Changes are saved when an input loses focus. |
| Musicians | Add and edit recs musician identities, aliases, public keys, and contacts. Enter each alias, key, or contact on its own line. |
| Actions | Calibrate, mark, start a session, pause or resume, inspect recs status, test lights or cables, operate streamO, and review the ten most recent results. |
| Errors | See up to 25 recs errors from the current showCo process. |

The **Show** tab opens by default. Switching Twitch does not start a new recs
session or pause recording. **End show** runs the existing closing credits and
fades when broadcasting, then pauses recs and starts the configured teardown
music. If Twitch is already stopped, teardown music starts without credits.
Performance lock also blocks these buttons.
**End show** always ends the show; it never becomes a restart action. It is
disabled during closing and after teardown finishes. A broadcast that is live
despite a saved teardown phase can still be ended with this button.
**Resume show** is a separate control on **Actions**. It stops the closing
observer, fades out teardown music, restores the room mix, starts a new recs
session, and restarts Twitch. This also works during credits. Starting Twitch
alone after teardown is blocked so it cannot bypass restoring the show.

The browser refreshes status and channel data. Waveforms use a dedicated event stream and resynchronize after a delayed browser connection. An unavailable optional service does not make the other pages unavailable.

Page order, sections, labels, and visible controls come from `showco/gui.toml`.
For a different show, commit a complete GUI file inside the showCo checkout,
then set `[gui] path = "showco/your-show.toml"` in
`showco/provision/config.toml` before deployment. The selected file is
validated before provisioning or target service replacement. The included
`showco/gui-minimal.toml` demonstrates a shorter Performance page with just a
marker action. For local use, `showco run --gui showco/gui-minimal.toml` selects
it without changing provisioning. The shared connection and performance-lock
controls remain on every page.

The waveform bridge uses reccy's `EventClient.wait_closed()` to detect a closed connection even if recs sends no shutdown event. It closes the old client, retains the one-second interruptible retry delay, creates a fresh client, and checks recs's subscription activation response. Explicit shutdown events and invalid waveform data also trigger reconnection. Stopping showCo interrupts its connection wait; this does not change snapshot/event ordering or replay control commands.

On the target, background sampling collects service and recording observations even with no browser open. The latest 100 incident events and up to 100 active faults are retained in `~/.local/state/showco/incidents.json` across restarts. Changes between samples can still be missed. A fault banner shows its start time, latest evidence, and a next step. Acknowledgment does not resolve a fault; recurrence after recovery requires a new acknowledgment. History write failures remain visible, and observations continue in memory.

Recording-input checks cover channels currently recording and flag silence or clipping. Recording progress needs an observed increase after startup, pause, or a session counter reset. Silence filtering can legitimately pause file growth; a write-progress observation alone is not a reason to restart recs.

### Performance controls and protection

Open **Performance** for large controls that advance the set list, mark a moment, or pause/resume recording. Moment markers use the label `performance moment`. The page shows recording state, destination, remaining capacity, stream state, and connection age. Pin important inputs and enable dimming for this browser. Screen-awake mode is opt-in and reports whether the browser and connection support it. Controls are disabled when status is unavailable; an action with an unknown outcome is never automatically retried.

Enable **Performance lock** from any page before a show. It blocks web requests for cable and lighting tests, calibration, recorder shutdown, new sessions, track names, stereo grouping, musician identities, mutable attributes, noise-floor changes, key labels, profile reloads, set-list and lighting-cue replacement, soundcheck, and recovery restarts. Manual cues, ordinary markers, pause/resume, and recovery verification remain available. An older tab receives the same server-side rejection. To unlock, select **Confirm unlock** and press **Unlock protected actions**; submit any previously rejected action again yourself.

Lock state persists in `~/.local/state/showco/performance.json`. If that file cannot be read, protected actions remain blocked until an explicit unlock can be saved successfully. The lock does not affect separate CLI or deployment commands, and it never starts or stops services itself. Rehearsal uses temporary in-memory lock and incident state and observes services when status is requested.

### Set list and cues

Prepare songs on **Set list**, with optional performer notes and expected minutes. Add, remove, and reorder songs, then save. Saving resets the set position and elapsed clock; it does not remove recs markers. The editor preserves unsaved work through status refreshes. If another tab changed the list or position, a stale save is rejected; use **Discard edits and reload saved list** to load the current version.

On **Performance**, **Start next song** sends `song start: TITLE` to recs. Skip advances the next-song cursor without a marker. Repeat sends the current song's marker again; Interval sends `interval`. Nothing advances automatically or changes lighting, streaming, or recording state. Position, notes, durations, elapsed start time, and pending cues persist in `~/.local/state/showco/setlist.json`.

Repeated requests carrying the same list revision are rejected. If recs may have accepted a marker but its reply was lost, the cue remains pending through restart. Resolve it explicitly: keep the cue without resending, with delivery marked unverified, or retry knowing that a duplicate marker is possible. The recs API cannot guarantee exactly-once delivery across a lost reply.

### Lighting cues

showCo owns the ordered lighting cue list, Go/Back, and cue position. lyte owns
lighting execution through its existing `select_animation` RPC. Open **Lighting
cues**, expand **Edit lighting cues**, give each cue a name and an installation
look, then save. Look suggestions come from lyte. Cues can repeat the same look.
Saving resets the cursor without changing the lights; it requires performance
protection to be unlocked. Unsaved edits survive polling; stale saves are rejected.

**Go** selects the first or next cue. **Back** selects the previous cue; at the
first cue it is unavailable. Neither end wraps. Both controls cut immediately
and remain available under performance lock. Song markers and lighting cues are
independent manual actions. There are no timed cues, automatic resends, or changes
to recording or streaming. This Go button is unrelated to the `showco deploy`
deployment command.

Current cue means the last selection acknowledged by lyte, or a pending cue
explicitly kept by the operator. It does not prove physical output. The page
also displays live active/queued looks, blackout, and test overrides. MIDI or
another client can change lighting without changing showCo's cue position.
Reconnecting only reads status. Production state persists in
`~/.local/state/showco/lighting.json`, including unresolved selections. A restart
restores the cursor without selecting a look.

A lost or unsuccessful reply leaves the selection pending and blocks further
Go/Back. Inspect the live look, then explicitly keep the pending cue, keep the
previous position, or retry. Keeping either position sends nothing; retrying may
restart an already-running look. Saving pending state must succeed before sending
any selection, so a disk failure prevents that new cue while existing lighting
continues.

The existing `showco run --rehearsal` mode supplies in-memory `idle`, `circle`,
and `square` looks without contacting lyte or output hardware; its cue list lasts
for that process. These names also match lyte's `examples/installation-laser.toml`.
Try Opening/circle, Finale/square, and End/idle, then Go, Go, Back and refresh the
page. This tests cue operation only, not laser output or physical readiness.

### Guided soundcheck

Open **Soundcheck**, select the expected recording inputs, and begin. This clears earlier results without starting output. Confirm the intended disk, then make sound on all selected inputs and check their measured signal/clipping state. Disk confirmation requires fresh recs status, available space without an active disk alert, and a readable Linux mount identity. On systems without that identity, disk verification remains unsuccessful.

Explicitly confirm and resume recording to begin a sample in the current session. After making sound, check that the recorded-audio counter advanced, then explicitly pause. Open **Playback**, select and listen to that sample, and enter its session/file description before confirming you heard it. Counter growth is measured evidence; playback is an operator confirmation. Recording remains paused until you resume it yourself. Starting another sample clears the previous sample's recording and playback results.

Lighting tests require an explicit output confirmation and button press; they leave the lights off. Confirm lighting by watching it and streaming by receiving the stream externally. Optional steps can be skipped with a reason and remain visibly skipped, never passed. Opening or refreshing the page starts no test.

Results and timestamps persist in `~/.local/state/showco/soundcheck.json`. Background observations invalidate them when the date, mount identity, observed input layout, service state, saved recs settings, or deployed showCo revision changes. Relevant showCo configuration actions also invalidate results. External physical changes, including mixer gain or cabling changes, require a fresh soundcheck. Checks are observations at a stated time, not continuing guarantees. Cable testing remains separate pending [hardware validation](../plan/hardware.md).

### Guided recovery

Open **Recovery** to refresh status, inspect the active-fault banner, or download diagnostics. A restart is offered only for a failed or disconnected enabled recs, lyte, or streamO service. Unlock protection and confirm the named interruption first: recs restart interrupts recording and playback, lyte restart interrupts lighting, and streamO restart interrupts streaming. Other services are not restarted.

The request and original failure are saved before execution in `~/.local/state/showco/recovery.json` and recorded in incident history. A successful restart command remains **checking** until you press **Refresh and verify recovery**. A failed or uncertain outcome is visible and never automatically retried, including after a showCo restart. Verification may be repeated while a service starts without restarting it again. Connected-recorder disk and input faults require inspection on Health; stalled file growth alone never triggers a restart. Recovery cannot restore missed audio.

The downloaded bundle uses the existing diagnostic collector and includes incident/recovery history alongside service logs and public configuration. It excludes the private configuration file and set-list notes. Review logs before sharing.

### Before the performance

1. Boot the Pi, then open Health from the tablet.
2. Confirm the intended removable recording disk, free space, and plausible remaining duration. Do not assume recordings are using a particular mount point or the Pi SD card.
3. Confirm each configured audio and MIDI device, mixer, and enabled service.
4. Check the Channels page while sound is present and make any required track name or stereo changes.
5. Start and stop a brief recording. Confirm that files grow on the removable disk, remain readable after stopping, and survive an unmount and remount.
6. When lighting or streaming is enabled, confirm their output with the real equipment. A green web status is useful evidence, but it is not proof that audio reached storage, lights emitted, or the audience received the stream.

For a full installation or major-change check, also power-cycle the Pi, test devices arriving after boot, confirm tablet and mixer network access, and run a five-hour soak with every live service. Record the date, deployed commits, hardware, recording destination, and pass/fail result outside this repository.

### Actions that affect a live show

- **Pause recording**, **resume recording**, and **start new recording session** change recs recording. Use the action result and the Health page to confirm the new state.
- The named marker buttons create `show start`, `song start`, `song end`, `set break`, or `show end` markers. A custom marker is also available.
- **Shutdown recs daemon** requires choosing the shutdown confirmation. It stops the recorder.
- **Test lights** runs lyte's one-second, 30-percent test and leaves the lights off. It runs only when you press the button; status polling and reconnects never trigger it.
- **Test X18 cables** pauses audio recording while it runs, then restores it if it was recording. It temporarily changes X18 routing and restores the affected mixer settings afterward. The default sends the same tone to AUX 1-6 at once and records inputs 9-14, so the cables may be connected in any order. Those AUX buses must be assigned to their matching physical outputs. Results distinguish absent signal, distortion, clipping, and incorrect level. The current thresholds are at least 98 percent tone similarity, 70-130 percent level, and no clipping; these need confirmation against known-good cables on the target installation.
- **Music mode** has four deliberate transitions. **Setup** stops streamO, pauses recs, restores the room's previous instrument routing after a teardown, and starts looping setup music. **Record** fades music out, mutes its X18 channels, starts a new recs session, then restarts streamO. **Tear down with closing credits** keeps recs and the room mix unchanged while streamO shows its configured pages and fades only the broadcast audio. During the final two seconds of black, showCo fades X18 main LR to zero. Only after streamO reports that the broadcast stopped does showCo pause recs, remove instrument channels from LR while preserving their settings, and fade in teardown music on the room master. A failed or uncertain closing sequence leaves recs alone and room music off. The Actions page offers explicit recovery: abandon the sequence and check recording state, or confirm externally that the broadcast ended before manually finishing teardown. **Stop and shut down** remains the immediate Pi shutdown action. All four transitions are blocked by Performance lock.
- streamO actions appear only when streaming is enabled. They include restart, mute, unmute, stop, stream information, chat, announcement, clip, and marker actions.

Music uses the target's existing ffmpeg installation, so it can decode normal audio formats. It writes stereo audio to X18 USB returns 17 and 18 and configures X18 channels 17 and 18 as USB-return channels routed to main LR. These channels must be reserved for showCo music. The X18 music fader is set to 40 percent; set the existing instrument mix to the intended 60-percent level on the mixer. Custom `source_channels` must be an adjacent odd/even X18 pair.

On the X18, inputs 17–18 share the stereo Aux return. showCo controls that return
as one stereo channel for music playback, muting, and room-scene capture and
restoration. Other configured music pairs use the ordinary channel controls.

Choose incidental audio with repeated target runtime flags. Each path may be an audio file or a directory:

```bash
uv run showco run --audio /mnt/audio/common --audio /mnt/audio/intro.flac \
  --setup /mnt/audio/open --teardown /mnt/audio/close
```

`--setup` and `--teardown` override `--audio` for their respective phases. An unset phase uses `--audio`; if neither is supplied, that phase generates no sound. There are no default audio directories, and showCo creates no music directories. Missing audio for either phase produces a startup warning in the service log and `showco-errors.txt`. The same flags are accepted by `showco run install-service` and saved in the service's runtime arguments. Paths refer to files already on the Pi.

On the development machine, the provisioning flags take **local** paths and copy media to the Pi:

```bash
uv run showco --photos ./rouen-images --photos ./more-photos \
  --audio ./music/common --setup ./music/open --teardown ./music/close \
  --movie ./background.mp4
```

The four directory/audio flags repeat. `--movie` selects one local movie file for streamO's `video` setting. `--photos` takes directories; the audio flags take directories or individual files. The command saves absolute local paths in the ignored `showco/provision/media.toml` (beside the selected `--config` file). A supplied category replaces its saved list; omitted categories retain their lists. To clear a repeatable category, set its list to `[]` in that file. Clearing managed photos disables their display; if an image feed is configured too, remove that feed first. Subsequent plain `uv run showco` invocations reuse the selections and synchronize their contents. Explicit update-only commands such as `showco --autosquash 100` preserve installed media settings without copying media; supplying a media flag selects the provisioning path even with `--autosquash`.

Each selected source has its own numbered destination under `/home/<user>/.local/share/showco/media/{photos,audio,setup,teardown,movie}/`, preserving source order and avoiding filename collisions. All five categories compare content checksums and skip transferring identical files, even when timestamps differ. Changed contents are copied even if size and timestamp match. Directory copies mirror local contents inside those managed destinations; streamO's downloaded-image inbox and approval files are preserved. Removed source slots are no longer used. Missing sources or failed transfers stop provisioning. A copied `score.toml` must use relative file paths within its directory.

Provisioning installs the copied audio paths in showCo's service arguments and updates streamO's photo directories and movie path while preserving its other settings. Existing photo timing and compatible directory weights are retained. If photos were disabled, they are enabled every 30 seconds; incompatible weights reset to the descending defaults. This does not enable the streamO service or supply its streaming credentials. Target-side `showco run` and service-installation flags continue to refer to paths already on the Pi.

Sources run in flag order, then repeat. A directory normally supplies its audio files in alphabetical filename order. If it contains `score.toml`, that file supplies the order and shuffle policy instead:

```toml
files = ["intro.flac", "ambient.flac", "interlude.wav"]
shuffle = [4, 1]
```

Relative file paths resolve against the directory containing the score. `shuffle = false` or `shuffle = []` repeats the listed order. `shuffle = true` creates a uniform random ordering each cycle. A weight list creates a weighted ordering without replacement: every listed file plays once per cycle, with larger weights making earlier positions more likely. Short weight lists repeat cyclically, so the example gives weights 4, 1, 4. Zero-weight files play after positive-weight files; all-zero weights produce a uniform ordering. Weights must be finite and nonnegative.

Optional target configuration is `~/.config/showco/music.toml`:

```toml
fade_seconds = 2
source_channels = [17, 18]
```

streamO supplies one closing card reading “Thank you” and “for watching” on separate lines, unless replaced in its `closing_credits` configuration. showCo uses these same credits. Its status reports the current page, timing, final black interval, and completion. showCo persists the operation ID, observes it without a browser open, and resumes observation after a showCo restart. A streamO restart during credits reports a failed, uncertain outcome; showCo never sends the start command again automatically. [streamO's operator guide](https://github.com/rec/streamo/blob/main/doc/streamo.md) documents the page format.

## Configure and deploy

The source configuration is [`showco/provision/config.toml`](../showco/provision/config.toml). Private values belong in the ignored `showco/provision/secrets.toml`, which overlays the public configuration. Never commit Wi-Fi passwords, stream keys, OAuth tokens, client secrets, or private keys.

Under `[stream]`, use `access_token` instead of the former `oath_token` spelling.
Provisioning and normal `showco` updates copy non-empty `stream_key`, `client_id`,
and `access_token` values into streamO's `streaming_service.ingest.stream_key`,
`streaming_service.client_id`, and `streaming_service.access_token`, respectively.
Existing encoding, media, and other streamO settings are preserved. Omitted
credentials leave existing target values unchanged. The target configuration is
written with owner-only permissions. `client_secret` stays local for obtaining
tokens; it is not copied into streamO's configuration.

The important configuration is near the top of `config.toml`:

| Setting | Meaning |
| --- | --- |
| `[network]` | Target host, SSH user and port, web port, Wi-Fi ordering, and topology. The user defaults to the provisioning machine's `USER`. |
| `[paths].root` | Absolute target directory containing the five sibling repositories. |
| `[networks.internal]` | Required IPv4 mixer subnet. Wi-Fi and mixer `ip_address` values are host offsets in this subnet. |
| `[[mixers]]` | Mixer name plus the audio/MIDI name prefixes expected from recs. Networked mixers provide both `ip_address` and `port`. |
| `[stream]` | Enable streamO and provide its non-secret metadata. |
| `[lyte]` | Enable lyte and name an installation configuration relative to the lyte checkout. |

With `[lyte] enabled = false`, provisioning stops lyte, removes its boot autostart,
and masks its user service so service commands cannot start it. Verification
checks that it is stopped and masked. Setting `enabled = true` and provisioning
again removes the mask and installs the lighting service.

External Wi-Fi networks use named tables in `showco/provision/config.toml`:

```toml
[networks.external.wifi.home]
name = "Home Wi-Fi"

[networks.external.wifi.venue]
name = "Venue Wi-Fi"
```

Put passwords under the matching names in the ignored `showco/provision/secrets.toml`:

```toml
[networks.external.wifi.home]
password = "your home password"

[networks.external.wifi.venue]
password = "your venue password"
```

The first network has the highest connection preference. NetworkManager automatically
connects to an available saved network; it does not switch away from an active
connection just because a preferred network appears. A venue network can be saved
before visiting the venue. An entry without a password describes an open network;
these settings cover personal WPA/WPA2 networks, not enterprise authentication or
captive portal logins.

Provisioning saves these profiles on the external Wi-Fi adapter without explicitly
activating or disconnecting them. Mixed topology keeps the private hotspot on its
own adapter. The existing single `[networks.external.wifi]` table must be replaced
with named tables, including its matching password table. After provisioning, verify
connection at both locations and after a reboot.

At normal showCo startup, the saved `showco-external-*` NetworkManager profiles
are checked. An existing external Wi-Fi connection is preserved. If disconnected,
showCo explicitly tries those profiles in priority order within a 30-second total
startup deadline, without replacing a hotspot. Failures are logged and appear
in the front-page fault banner and Health readiness checks; the control panel
still starts. Connection status is subsequently refreshed without repeated
activation attempts. Rehearsal mode does not manage networking, and installations
without saved external profiles skip this check. A connected Wi-Fi adapter does
not establish Internet access, clock synchronization, or Twitch delivery.
Activation uses non-interactive `sudo -n nmcli`, relying on the target's existing
passwordless-sudo provisioning requirement; permission errors are reported rather
than prompting for credentials.

`network.topology` may be `public`, `private`, `mixed`, or empty. Public uses a saved external Wi-Fi network and puts an X18 on Ethernet. Private creates the internal access point and bridges it to X18 Ethernet. Mixed uses one Wi-Fi interface for the private bridged network and a second for the external network. When empty, showCo selects a topology from the configured external network, available Wi-Fi interfaces, and whether streaming is enabled.

This installation sets `network.restrict_external_ingress = true`. The policy allows incoming connections on the Pi's private network; other interfaces accept SSH on the configured port and mDNS discovery on UDP 5353 so `t.local` remains usable. Outbound traffic and its replies continue to work. Provisioning installs a persistent nftables ingress rule and rejects `public` topology, which has no private hotspot. Put a long, random private Wi-Fi password in the ignored `showco/provision/secrets.toml`. To reach the web UI, join the private Wi-Fi; from the external Wi-Fi, use SSH only. Verify access from both networks after provisioning.

For a new card, first write Raspberry Pi OS with Raspberry Pi Imager. Configure the user, hostname, Wi-Fi, and SSH public key to match your showCo provisioning configuration. Leave the card attached to the Mac and run `showco` or `showco deploy`.

showCo saves the applied configuration fingerprint after setup and any required
reboot complete, before checking service health. A service failure still reports
an error and exits unsuccessfully, but the next run updates the applications
instead of repeating unchanged machine setup. Failed setup does not save the
fingerprint.

showCo detects an external disk no larger than 256 GiB containing Raspberry Pi boot files and Imager's cloud-init configuration. It displays the disk, requires `yes` before changing the card, adds passwordless sudo for the configured user, and ejects the card. Insert the card into the Pi and turn it on. showCo waits up to five minutes for SSH, then up to five minutes for [cloud-init first-boot setup](https://docs.cloud-init.io/en/latest/reference/cli.html#status) to complete successfully before provisioning. If the configured Pi was already online, it must reboot before provisioning can continue. Ctrl-C cancels the wait. If reflashing changes the SSH host key, showCo displays its SHA256 fingerprint and requires `yes` before replacing the saved entry in `~/.ssh/known_hosts`. Confirm the fingerprint belongs to your Pi. Declining or reaching end of input leaves saved keys unchanged and stops provisioning. Setting `accept_changed_host_key = false` disables replacement entirely.

Multiple matching cards stop the command without modifying any card; disconnect the extras and try again. No card is formatted or imaged by showCo. Detection is available on macOS. Without a matching attached card, deployment follows the ordinary path: it compares configuration and provisioning-script fingerprints, provisions a missing or changed fingerprint, and updates a matching target. Explicit update modes such as `--sync`, `--push`, `--remote`, or repository selection do not prepare cards.

To prepare and eject a card without waiting for the Pi or provisioning, use:

```bash
showco prepare-card
```

Provisioning validates local repositories, publishes and synchronizes all five projects, checks SSH and passwordless sudo, configures the target, installs user services, and verifies the web UI, recs progress, enabled services, networking, and configured inputs. It may reboot the target when necessary.

Ordinary provisioning updates the APT package index and installs missing base packages, but does not upgrade installed packages. Pass `--upgrade` to run `apt-get upgrade` as part of provisioning.

Use these explicit modes only when their effects are intended:

```bash
# Provision and run apt-get upgrade.
showco deploy --upgrade

# Publish and deploy selected repositories. recs also updates showCo.
showco deploy recs

# Update the target from already-published GitHub commits.
showco deploy --remote recs showco

# Publish local work without contacting the target.
showco --push recs

# Publish local work and refresh internal dependency lockfiles, without target work.
showco --sync recs
```

showCo removes the previous ignored `showco-errors.txt` from its checkout when
a command starts. If that command fails, it saves the terminal output there as
well as displaying it. The file may contain operational details, so keep it
private.

On the development machine, run `showco panel` to open the Pi's control panel
without switching Wi-Fi. It uses the configured SSH address and ports, forwards
the panel to this machine's loopback interface, and opens your browser. Leave
the command running while using the panel; Ctrl-C closes the tunnel. The tablet
can use the panel at the same time. If the local port is occupied, choose another
with `showco panel --port 17353`. SSH failures are displayed and saved in
`showco-errors.txt`.

Updates preserve saved recs settings by default. Pass `--clear-settings` to explicitly clear them when updating recs. Preflight checks run before settings or services are changed. A failed update restores the previous selected revisions, locked environments, and any settings explicitly cleared, then restarts affected services. If restoration fails, services remain stopped and the command reports the failure; a recovered update still exits unsuccessfully.

Repository selection includes dependent applications. All affected services stop before any selected checkout changes, and showCo starts last. Unselected checkouts are not reset. Remote updates fetch the published showCo updater into a temporary directory and run it with its locked dependencies, so an older installed CLI does not block updates. The installed revisions and environments remain unchanged until the update transaction starts. A working target Python environment, uv, and Git checkout are required; provisioning is the repair path for a broken installation. Rollback handles command failures and interruption within the update process; it is not a durable recovery mechanism for power loss or a killed process.

Do not use `showco deploy` as a casual diagnostic: it can publish histories, reset selected target checkouts, restart services, and change network or system configuration during provisioning.

## Diagnose a problem

Fetch combined target service logs from the provisioning machine:

```bash
showco logs
showco logs recs showco --lines 500
```

`showco clear-logs` truncates the current and rotated log files for showCo, recs,
streamO and lyte, plus the target checkout's `showco-errors.txt`. It does not
restart services; new entries can appear immediately. Missing files are skipped,
and failures produce a nonzero exit status. The shared systemd journal cannot be
cleared selectively by service and remains intact, as do recordings, session
journals, monitoring history and saved incidents. This removes evidence used by
`showco report`, so generate any needed report first.

`showco report` reads the target without restarting services or changing recordings.
It groups recs sessions using a gap of three hours or more and selects the latest
group with more than 60 seconds of captured audio. `-1` through `-9` select older
qualifying groups. Change the gap with `showco report --gap-hours 2`.
Gaps are measured from a session's end to the next session's start, including overlap.
Sessions are ordered by capture timestamps, not file modification times.

The short report lists recording directories, saved media size and audio-file counts,
MIDI messages, saved duration per source/track, recording-quality issues, service
warnings, and available CPU/disk history. Stereo pairs remain single tracks. Saved
track durations include recs pre-roll and tails; they do not establish exactly how
long a channel had audible sound. Overlapping track durations are not show duration.
Recording metadata comes only from `session-record.jsonl`; `recording.toml` is
neither required nor read. Durations use recorded frame counts and sample rates,
including rates declared in journal clock records. Missing media does not
contribute audio duration. Encoding variants do not add duplicate captured time.
Open sessions and unfinished files are marked provisional or incomplete; files
without a recorded frame count or sample rate have unknown duration.

Broadcast duration is unknown: retained streamO FFmpeg progress lacks reliable
attempt identities and does not prove Twitch delivery. Rotated-away
logs, unmounted recording disks, and expired monitoring/incident history cannot be
reconstructed. Media file existence and sizes are checked, but contents and audio
headers are not verified. Errors are restricted to the selected show window;
repeated observations are grouped, with the ten most frequent issue types displayed.
Counts from different
sources can describe the same incident. URLs and common credential fields are redacted.

Logs are retained on the target at `~/.local/state/SERVICE/SERVICE.log` for `showco`, `recs`, `streamo`, and `lyte`. showCo itself keeps only the current process's recs errors and ten recent action results.

For a small target-side inspection, use:

```bash
showco python 'import sys; print(sys.version)'
```

On the target, `showco bundle` writes a timestamped diagnostic bundle below `~/.local/state/showco/bundles`. `showco cable-test` runs the X18 test from the terminal; it accepts optional inclusive channel and AUX-send ranges, for example `showco cable-test 9-14 1-6`. Use `--duration-seconds` to set the simultaneous tone and capture length, for example `showco cable-test --duration-seconds 10`.

The Health page samples Pi temperature, aggregate CPU use, memory use, and recs disk state every second. It writes one aggregate record per UTC minute to `~/.local/state/showco/monitoring/YYYY-MM-DD.jsonl`, keeps seven UTC calendar days, and writes the final partial minute during a clean shutdown. Sampling or history-write failures are logged and do not take down the web UI.

## Service and protocol boundaries

The web UI is for a trusted show network and has no login. Every client that can reach it can operate it. Do not expose the web port to the public Internet or an untrusted network. Browser action requests from a different origin are rejected; this is protection against cross-site submissions, not authentication of network clients. Command-line clients without an Origin header remain supported.

`showco.service` runs the standard-library HTTP server. recs, lyte, and streamO are user services and communicate with showCo through their public reccy RPC interfaces. showCo creates a recs control client per request, serializes actions, gives operator actions a six-second timeout, and caches short status snapshots for one second. The last valid recs snapshot remains visible and is marked stale after a transport failure.

The mixer state combines recs-reported audio and MIDI input names with an optional TCP or UDP probe. For X18, the UDP probe sends `/xremote` and waits for a reply. It is a reachability hint only; confirm mixer control with the tablet application or real OSC feedback. X18 `/xremote` subscriptions are renewed for feedback, but successful renewals are not recording events.

The web service limits ordinary requests to eight concurrent connections and waveform event streams to four. Its browser routes are `/` and `/channels`, `/performance`, `/setlist`, `/lighting`, `/soundcheck`, `/recovery`, `/health`, `/playback`, `/attributes`, `/actions`, `/errors`, `/status` and `/workflow-status` (JSON), `/diagnostics` (download), and `/waveforms` (server-sent events). Form posts go to `/actions`; requests that accept JSON receive the action result directly.
