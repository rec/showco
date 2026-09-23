# Data-driven GUI plan

## Goal and current state

One TOML file must determine what each showCo page displays: the order and
grouping of sections, text, status values, repeated rows, fields, buttons,
links, visibility, and enabled state. Selecting another complete file must
produce a different useful display for a show or application without editing
Python or JavaScript.

The Channels, Track names, Musicians, Errors, Health, Attributes, and Playback pages read their sections,
content, order, and labels from `showco/gui.toml` and render through one shared
Jinja template. A third edit page can be added to a selected TOML file without
adding a route, Python renderer, template, or JavaScript page file. Errors and
Health also refresh their declared values and repeated rows from live status.
The remaining page bodies still come from `showco/runtime/views.py` and
`site/*.html`. Supported actions are currently limited to the registered recs
channel operations. **The current implementation does not yet meet the overall
goal.**

The finished file must account for every item in
[all-gui-elements.md](all-gui-elements.md). Python owns service connections,
data formatting that depends on domain rules, action validation, performance
protection, and the actual behaviour of recs, streamO, and lyte. It must not
silently add a control that the selected GUI file omits.

## Prove the design with a second edit page

Channels and Track names use the same element template. The browser clones
the declared repeated-item markup for channels discovered after page load.
The Track names page demonstrates an edit-focused layout in TOML alone. An
HTTP test adds a third page in a temporary TOML file and checks its rendered
route, fields, labels, and controls.

The core of the current Channels declaration is:

```toml
version = 1
name = "showCo"
default_page = "channels"

[[pages]]
name = "channels" # route is /channels; page order is navigation order

[[pages.sections]]
name = "recording"
title = "Recording channels"

[[pages.sections.elements]]
name = "channels"
kind = "repeat"
source = "show.recs.channels"
empty_text = "No channel data from recs."

[[pages.sections.elements.children]]
name = "recording_state"
kind = "indicator"
value = "item.on"
label = "Recording state"

[[pages.sections.elements.children]]
name = "track_name"
kind = "text_field"
label = "Track name"
value = "item.name"
action = "recs-track-name"

[[pages.sections.elements.children]]
name = "stereo"
kind = "checkbox"
label = "Stereo"
value = "item.channels"
enabled_when = "stereo_pair_available"
action = "recs-set-stereo"

[[pages.sections.elements.children]]
name = "waveform"
kind = "waveform"
label = "Live waveform"
source = "waveforms"

[[pages.sections.elements.children]]
name = "calibrate"
kind = "button"
label = "Calibrate"
action = "recs-calibrate"

[[pages.sections.elements]]
name = "save_names"
kind = "button"
label = "Save"
operation = "save_track_names"

[[pages.sections.elements]]
name = "revert_names"
kind = "button"
label = "Revert"
operation = "revert_track_names"
```

The complete current Channels declaration is in `showco/gui.toml`. The browser
supplies each repeated channel's device and channel numbers to the existing
server actions. Save submits only changed track names, Revert restores the
saved values, and stereo availability follows the channel pairing rule.

The decisive test edits only a temporary GUI file and renders Channels from
that file. Removing **Calibrate** from the file must remove that button from
the rendered Channels page. Moving **Save** to another section must move it in
the rendered page. Changing its label must change the visible label. The
server must still reject an invalid or protected calibration request, whether
or not a page shows the button. A test that only inspects parsed TOML does not
prove the migration.

## Schema and rendering contract

- Keep `[[pages]]` in navigation order. Each page has a unique `name`; its
  route is `/<name>`. `title` defaults to `name.capitalize()`. `/` resolves to
  `default_page`. Remove the current `renderer` selector when the last named
  page renderer has been replaced.
- Each page contains ordered sections; each section contains ordered elements.
  Nested elements are allowed only for container kinds such as `repeat`,
  `form`, and `details`. Order in the file is order on screen. Stable element
  names identify fields and status targets, not a second navigation system.
- The initial element vocabulary is `text`, `link`, `status`, `indicator`,
  `meter`, `button`, `text_field`, `number_field`, `textarea`, `checkbox`,
  `select`, `form`, `repeat`, `details`, and `waveform`. Add a kind only when
  an inventory item cannot be expressed through these and an existing
  behaviour adapter. No kind may secretly render an entire legacy page.
- Every element kind has explicit allowed fields and required fields. For
  example, a button needs an `action` or a local `operation`; a status needs a
  registered `value`; a repeat needs a registered list `source` and an
  `empty_text`. Reject unknown fields, kinds, sources, actions, conditions,
  and duplicate names at load time, with the GUI file path and element name.
- Values refer to approved status or page-data paths, including `item.*`
  within a repeat. Conditions refer to named predicates such as
  `feature_enabled`, `performance_unlocked`, `pending_resolution`, and
  `stereo_pair_available`. Do not evaluate expressions from TOML. A predicate
  may hide or disable a control but never authorizes the action.
- An action registry states which existing server action each control invokes,
  which fields it accepts, and how values from a form or repeated item supply
  them. The server validates the submitted values and enforces its existing
  performance lock. A GUI file cannot define a new endpoint, command, URL, or
  action handler.
- Jinja templates render the shared shell, sections, labels, and initial
  content from the selected document. Browser code updates values and repeated
  items from the declared sources with DOM operations, preserving focus and
  unsaved edits. Behaviour adapters implement waveform drawing, ordered-row
  editing, screen awake, and similar browser capabilities; they do not choose
  visible controls, labels, placement, or page membership.
- Keep connection status, fault banner, and performance protection in the
  shared shell as the user requested. They are safety and connection controls
  on every page, so their text and mechanics do not need GUI configuration.
  Navigation follows the page array. All other items in the inventory must
  come from the GUI file.
- Escape every text value, never interpolate configured HTML or JavaScript,
  and restrict links to registered showCo routes or approved downloads.
- Keep user-visible strings in TOML or translation catalogs, not in Python
  HTML fragments. Use Jinja's i18n extension and message catalogs when a
  second language is introduced; the first Channels conversion establishes
  templates and escaping, not translations.

## Migration sequence

1. **Shared edit-page proof.** Render Channels and Track names through one
   Jinja element template, with no page-specific renderer. Add a third edit
   page by changing only TOML and verify it through HTTP. Keep existing recs
   action validation and the browser's unsaved-edit behaviour.
2. **Convert read-only pages (done):** Errors, then Health. Define status formatters
   and repeated rows for readiness checks, service cards, meters, inputs,
   errors, mixers, OSC recorders, and incidents. Verify that changing labels,
   order, visibility, and empty text in TOML changes the page.
3. **Convert forms and transport:** Attributes, Playback, and Musicians are done. Convert
   Actions. Add only the field, form, conditional visibility, result, and
   transport behaviour these pages need. Preserve hidden musician data on
   edits, explicit shutdown confirmation, cable-test duration, and feature
   gates for lyte, streamO, and music.
4. **Convert stateful workflows:** Set list, Performance, Lighting cues,
   Soundcheck, and Recovery. Express their controls and sections in TOML.
   Browser adapters may implement ordered editing, pin preferences, and
   confirmation flow, while the existing server controls revisions, pending
   outcomes, skip evidence, and protected actions. Remove each `site/*.html`
   fragment and page-specific script only when its page uses the document for
   every displayed element.
5. **Finish selection and provisioning.** Keep `showco run --gui` for local
   use. Pass an explicitly selected GUI file through service installation and
   provisioning; validate it before replacing a running service. Make changes
   to the selected file part of the target's update decision. Document an
   alternate show file with a genuinely different Performance page.
6. **Remove the legacy path.** Remove named page renderers, obsolete
   page-specific scripts, and the `renderer` field. The route handler looks up
   a page by `name` and passes it to the generic renderer. Keep only the
   shared shell, registries, formatters, and behaviour adapters in code.

Each stage must leave every already converted page running entirely from the
document. An old renderer may remain temporarily for pages not yet converted,
but a converted page must have one rendering path. Do not add unused TOML
entries in advance and count them as progress.

## Completion checks

- Trace every entry in [all-gui-elements.md](all-gui-elements.md) to a
  specific `showco/gui.toml` section or element, except the agreed shared
  shell. Record this mapping during implementation and fail a review if an
  item still comes from a page-specific HTML template or view function.
- For each page, a test changes a label or section order **in the file only**
  and observes the corresponding rendered change. At least Channels and an
  alternate Performance display must also prove that removing a control from
  the file removes it from the interface.
- The default file preserves current labels and behaviour across all pages.
  Browser tests cover status refresh, unsaved edits, disconnection, repeated
  input, pending workflow outcomes, and partial service failure where relevant.
- A second valid file produces a materially different but functioning display
  without editing code. An invalid file fails before serving HTTP and names
  the offending entry.
- Hidden controls do not weaken authorization. Requests for unknown actions,
  invalid fields, and protected actions are rejected by the server regardless
  of what the selected GUI file contains.
- The final `showco/gui.toml` has no inert `components`, `sources`, or action
  declarations: every configurable field it contains is read by the running
  renderer or its registered behaviour adapter.

## Additional work beyond the prompt

None.
