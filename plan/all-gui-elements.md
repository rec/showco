# showCo GUI element inventory

This is an inventory of the HTML interface served by showCo. It includes static,
repeated, and conditional elements, because the latter are still part of the
operator interface. Labels below are the displayed labels where one exists.

Service endpoints such as `/status`, `/waveforms`, and `/workflow-status` are
not pages and are excluded. `/diagnostics` is a downloaded diagnostic bundle,
not a page.

## Every page

- Browser title: `showCo <page title>`.
- Header: `showCo` and navigation links: **Performance**, **Set list**,
  **Lighting cues**, **Soundcheck**, **Recovery**, **Channels**,
  **Musicians**, **Health**, **Playback**, **Attributes**, **Actions**, and
  **Errors**.
- Connection-status message.
- Performance-protection section:
  - Performance-lock state.
  - **Enable performance lock**.
  - **Confirm unlock** checkbox.
  - **Unlock protected actions**.
  - Lock-result message and explanatory text.
- Fault banner. When a fault is active it includes its detail, a link to
  **Inspect health and history**, and, while unacknowledged, **Acknowledge**.
- Monitoring-error message.

## Channels (`/` and `/channels`)

- **Recording channels** heading.
- One channel card for each channel supplied by recs. Each card has:
  - Recording-state indicator and channel name.
  - Track-name text field.
  - **Stereo** checkbox. It is unavailable when that channel cannot be paired.
  - Live waveform canvas.
  - **Calibrate** button, whose text becomes **Calibrating...**,
    **Calibrated**, or **Calibration failed** while reporting the result.
- **Save** and **Revert** track-name buttons.
- `No channel data from recs.` when recs has no channels.

## Musicians (`/musicians`)

- **Add musician** section, with a nickname field, names multiline field,
  links multiline field, and **Add musician**.
- **Edit musician** section. One form per saved musician, each with a read-only
  nickname, names multiline field, links multiline field, and **Save changes**.
- `No musicians saved in recs.` when the database is empty.
- **Recent actions** list, or `No actions yet.`.
- A failure message replaces the musician forms if recs cannot provide them.

## Performance (`/performance`)

### Set list controls

- Set-list position, performer notes, elapsed-time, cue-message, and workflow-result messages.
- **Start next song**.
- Expandable **Skip, repeat, or interval** section with **Skip next song**,
  **Repeat current song**, and **Start interval**.
- Conditional marker-resolution panel, shown only after uncertain marker
  delivery: **I checked the outcome and chose how to proceed** checkbox,
  **Keep cue without resending**, and **Retry marker with duplicate risk**.

### Performance controls

- Recording, destination/capacity, audio-write, and stream status messages.
- **Mark this moment**, **Pause recording**, **Resume recording**, and
  **Inspect health**.
- **Dim display** checkbox.
- **Keep screen awake**, which changes to **Allow screen sleep** when awake.
- Screen-awake status.
- **Pinned inputs** area. It shows each selected input and, if the input is no
  longer available, **Unpin unavailable input**. With no pins it says to choose
  inputs to watch.
- Expandable **Choose inputs to pin** section. It contains one checkbox per
  current recs channel, labelled with device and channel name.

## Set list (`/setlist`)

- **Prepare the set list** heading and workflow-result message.
- Set-list editor with one row per song. Each row has:
  - Song-title text field.
  - Expected-minutes number field.
  - Performer-notes multiline field.
  - **Move up**, **Move down**, and **Remove**.
- **Add song**, **Discard edits and reload saved list**, and
  **Save set list and reset position**.
- **Perform with this set list** link.

## Lighting cues (`/lighting`)

- Current-cue position, next-cue position, and lyte-status messages.
- **Back** and **Go**.
- Conditional pending-resolution panel: **I checked the outcome** checkbox,
  **Keep pending cue**, **Keep previous position**, and **Retry selection**.
- Expandable **Edit lighting cues** section. One row per cue has:
  - Cue-name text field.
  - Installation-look text field with a list of currently available lyte looks.
  - **Move up**, **Move down**, and **Remove**.
- **Add cue**, **Discard edits and reload**, and **Save cues and reset position**.

## Soundcheck (`/soundcheck`)

- Soundcheck scope/status message.
- Expected-input chooser with one checkbox per available recording input.
- **Begin a fresh soundcheck**.
- Step-result list for disk, inputs, recording, playback, lights, and stream.
- **I confirmed the intended disk or personally heard/saw the selected output**
  checkbox, **Confirm disk and capacity**, and **Check expected inputs now**.
- **I intend this recording or lighting change now** checkbox,
  **Resume recording and begin sample**, **Check sample writes**, and
  **Pause recording to finish sample**.
- **Open Playback** link, `Session/file heard, or reason for skipping` text
  field, and **Confirm I heard this sample**.
- **Actions** link, **Test lights now**, **Confirm I saw the lights**, and
  **Confirm I received the stream**.
- **Skip a step** selector with Disk, Inputs, Recording, Playback, Lights, and
  Stream choices, plus **Record skip with reason**.
- **Performance** link.

## Recovery (`/recovery`)

- **Guided recovery** heading.
- **Refresh and verify recovery**.
- **Download diagnostic bundle** link.
- Recovery-options area, generated only for unavailable restartable services.
  Each service has an explanation, **I intend to interrupt <service>** checkbox,
  and **Restart <service> only**.
- **Latest recovery** state and workflow-result messages.
- **Health** link.

## Health (`/health`)

- **Service readiness**: readiness state and a repeated list of named checks.
- Recording and Streaming service cards, each with state and detail.
- **Performance** rows for CPU, memory, and recording disk. Each gives the
  value, detail, and health state.
- **Health** values for recs, recs snapshot, recording progress, streamO,
  lyte, Pi temperature, stream bitrate, mixers, and OSC recorders.
- **Recording inputs** list, with one signal/check result per current input, or
  `No recording inputs.`.
- **recs errors** list, limited to the latest 25 entries, or `No recs errors.`.
- **Observed incidents** list, or `No incidents.`.

## Playback (`/playback`)

- Playback state, selected session/channel/output, position/duration, and
  action-result messages.
- **Previous session**, **-10 seconds**, **Play**, **Pause**, **Stop**,
  **+10 seconds**, and **Next session**. Controls that need a session are
  unavailable while waiting for one.

## Attributes (`/attributes`)

- **recs attributes** heading.
- One labelled editable field per mutable recs attribute. The field is text,
  number, checkbox, or JSON text according to its current value and saves when
  it loses focus.
- A recs failure message, or `No mutable recs attributes.`, when appropriate.

## Actions (`/actions`)

- **Calibrate noise floor**.
- **Set noise floor** form: source, channel, and noise_floor fields, plus
  **Set noise floor**.
- **Reload recs profiles**.
- Marker buttons: **Show start**, **Song start**, **Interval**, and **Show end**.
- **Create recs marker** form: label field and **Create recs marker**.
- **Set recs key label** form: key and label fields, plus **Set recs key label**.
- **Start new recording session**, **Pause recording**, and **Resume recording**.
- **recs status snapshot**, **recs disk status**, **List recs devices**, and
  **recs capabilities**.
- **Shutdown recs daemon** form: confirmation selector (`Cancel` or
  `Shutdown recs daemon`) and **Apply shutdown choice**.
- **Test lights**, when lyte is enabled.
- **Test X18 cables** form: channels text field, sends text field, seconds
  number field, and **Test cables**.
- Conditional **Music mode** section when music is configured: mode/track/error
  status, **Setup**, **Record**, **Tear down**, and **Stop and shut down**.
- Conditional streamO controls when streamO is enabled:
  - **Restart Stream**, **Mute Stream**, **Unmute Stream**, and **Stop Stream**.
  - **Update stream info** form: title, category, and tags fields.
  - **Send chat message** and **Send announcement** forms, each with a message field.
  - **Create clip**.
  - **Create stream marker** form with a description field.
- **Recent actions** list, or `No actions yet.`.

## Errors (`/errors`)

- Latest 25 recs errors, with time and message, or `No recs errors.`.
