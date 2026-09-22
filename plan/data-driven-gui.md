# Data-driven GUI plan

## Goal

Replace showCo's hand-authored page layouts with one validated GUI data file.
That file will define every display decision recorded in
[all-gui-elements.md](all-gui-elements.md): pages, navigation, headings, copy,
layout, fields, controls, repeated rows, visibility and enabled conditions,
status displays, and their action or status bindings.

At runtime, showCo reads that file to provision its web interface. A different
show or application can use a different GUI file without changing the renderer.
The file controls the interface; Python continues to own service calls,
validation, state collection, action authorization, and audio, lighting, and
streaming behaviour.

## Design constraints

- Use one TOML GUI document. TOML matches the project's existing configuration,
  is easy to edit during preparation, and can be parsed with the standard
  library.
- Do not permit HTML, JavaScript, Python expressions, URLs outside showCo, or
  arbitrary action names in the document. The document selects only registered
  widget kinds, data sources, fields, actions, and condition names.
- Treat the document as configuration, not code. Validate it completely before
  starting the HTTP server. A bad document must make startup fail with the path
  and precise invalid entry, rather than serve a partly broken control surface.
- Keep the document executable only through a small, declarative vocabulary.
  An action binding names a server-side action already registered by showCo;
  it cannot run a command or invent a request payload.
- Keep accessibility in the renderer: labels, headings, focusable native
  controls, live regions, disabled state, and text alternatives for waveforms
  are renderer responsibilities, not optional configuration.
- Preserve the current server-side performance lock. A GUI condition can hide
  or disable a control, but cannot grant permission to an otherwise protected
  action.

## Resulting files and ownership

1. Add `showco/gui.toml` as the complete shipped GUI specification. It is the
   only source of page and control decisions for the default showCo interface.
2. Add `showco/runtime/gui_schema.py` with frozen Pydantic models for the
   document and a `load_gui(path)` function using `tomllib`.
3. Add `showco/runtime/gui.py` with the generic HTML renderer and its small
   registry of supported widget kinds, binding names, and conditions.
4. Replace page-specific HTML layout in `showco/runtime/views.py` with a small
   wrapper that asks the GUI renderer for the requested page.
5. Replace page-specific browser scripts with one `site/gui.js`. It reads a
   generated page manifest, polls declared sources, applies declared bindings,
   and submits declared actions. Keep a narrowly scoped renderer adapter for
   waveform canvases, because their drawing is a browser capability rather than
   a layout decision.
6. Add `[gui] path = "showco/gui.toml"` to the provisioned showCo configuration
   and pass that path into the service. Provisioning therefore installs one
   selected GUI document with the application, and a show can select a different
   document deliberately.

The application must never silently fall back to a compiled-in page definition.
A documented `--gui` CLI override may select a different complete document for
local rehearsal or another application.

## GUI document model

The top-level document has these sections:

```toml
version = 1
name = "showCo"
default_page = "performance"

[[navigation]]
page = "performance"
label = "Performance"

[[pages]]
id = "performance"
path = "/performance"
title = "Performance"
sources = ["show", "workflow"]
layout = ["global", "setlist", "performance"]
```

The actual document uses tables rather than embedded markup. Each `layout`
entry refers to a named component declared in the same file. A component has a
`kind`, an optional label, a stable identifier, a binding, and an optional
condition. Components may contain child components.

Supported component kinds should cover the complete existing inventory:

| Kind | Existing examples |
| --- | --- |
| `section`, `heading`, `text`, `notice`, `details` | page grouping, guidance, result and fault messages |
| `navigation`, `link` | shared header and page links |
| `status`, `meter`, `list`, `card` | health, incidents, errors, service cards, readiness, mixer and input values |
| `form`, `field`, `checkbox`, `select`, `button` | musician, action, streamO, cable-test, lock, and shutdown controls |
| `repeater` | channels, musicians, songs, lighting cues, inputs, recovery choices, attributes, and pinned inputs |
| `transport`, `waveform` | playback buttons and channel waveform canvas |
| `editor` | ordered song and lighting-cue rows with add, remove, and move controls |

A field declares its native type, label, value binding, validation constraints,
and whether it is read-only. A select declares its static options or an approved
option binding. A repeater declares the list binding, empty-state message,
per-item component, and item key. An editor additionally declares its permitted
row fields and the registered add, remove, and reorder operations.

`condition` and `enabled_when` use named predicates with explicit arguments,
not a general expression language. Examples:

```toml
condition = { predicate = "feature_enabled", feature = "streamo" }
enabled_when = { predicate = "performance_unlocked" }
condition = { predicate = "fault_unacknowledged" }
enabled_when = { predicate = "has_selected_playback" }
```

The schema rejects unknown predicates, bindings, actions, component kinds, and
fields. This makes a typo a startup error instead of an inactive button.

## Data and action contract

### Status sources

Define a source registry in Python:

- `show`: existing `/status` data, including service, lock, fault, health,
  channels, attributes, playback, and monitoring state.
- `workflow`: existing `/workflow-status` data for set list, soundcheck,
  recovery, and lighting.
- `waveforms`: existing server-sent waveform stream, used only by `waveform`.
- `page`: static values assembled when the page is rendered, such as musician
  records and recent actions.

Bindings use a limited dotted path through a named source, for example
`show.recs.channels`, `workflow.lighting.cues`, and `page.musicians`. The server
serializes only data that a page has declared. This avoids making configuration
or private state available merely because it exists in the app object.

### Actions

Create an action registry that maps stable GUI action names to the existing
server operations and declares:

- required and optional fields;
- accepted value types and limits;
- confirmation requirement, if any;
- performance-lock classification;
- result target and refresh sources.

For example, `recording.pause`, `recs.marker`, `cable_test.run`,
`musician.add`, `setlist.save`, and `lighting.go` are registry names. The GUI
file binds a button or form to one of those names; it never contains the current
internal POST token such as `recs-pause-recording`.

Retain the existing action endpoints initially behind one generic JSON form
submission endpoint. The server resolves the registry entry, validates supplied
fields, checks the performance lock, invokes the current adapter, and returns
the current `ActionResult`. Remove legacy per-page form dispatch only after all
specified actions use the registry.

## Rendering and browser behaviour

1. The server loads and validates the document once, then resolves the requested
   page by path. It renders a standard shell from document components and embeds
   a JSON manifest containing only that page's component tree, sources,
   bindings, and actions.
2. `gui.js` renders dynamic component content using DOM methods, never by
   interpolating HTML. It subscribes only to the page's declared sources and
   refreshes only the components whose bindings changed.
3. The renderer has component adapters for the few interactive patterns that
   need stateful browser behaviour:
   - ordered editors for songs and lighting cues;
   - persistent browser preferences for dimming, screen awake, and pinned inputs;
   - waveform drawing;
   - form submission, disabled/busy state, and result messages.
4. The document declares those adapters and their fields. It does not include
   custom script. If a future display needs behaviour the registry lacks, add a
   deliberate reusable component kind with tests instead of adding a page script.
5. Render the global shell from a `global` component in the document. This
   includes navigation, connection status, performance lock, fault banner, and
   monitoring error, so those elements stop being a hard-coded exception.

## Migration phases

### 1. Establish the specification and validation

- Write the complete default `showco/gui.toml` from
  [all-gui-elements.md](all-gui-elements.md), without changing visible behaviour.
- Define Pydantic schema models for document version, pages, navigation,
  components, bindings, predicates, and actions.
- Validate uniqueness of page IDs, paths, component IDs, action names, and
  repeater keys; validate that links use declared paths.
- Add fixture-based schema tests for valid default data and concise errors for
  invalid paths, components, bindings, predicates, actions, and field types.

### 2. Introduce the shared renderer

- Build the shell, static components, navigation, links, sections, text,
  notices, forms, fields, buttons, and details from the document.
- Port the global shell first and compare its output against the present HTML
  contract, including page title, navigation, lock controls, fault banner, and
  monitoring message.
- Switch route dispatch from named view functions to a page lookup. Keep the
  existing views temporarily only as a test oracle while their pages migrate.

### 3. Port data displays and repeated elements

- Port Channels, Musicians, Health, Errors, Attributes, Actions, and Playback.
- Add list, card, meter, repeater, waveform, and transport adapters as needed.
- Move all labels, empty-state wording, headings, action placement, and feature
  visibility into the TOML document.
- Remove each corresponding hard-coded layout function only after its page is
  rendered solely from the document.

### 4. Port workflow pages

- Express Set list, Soundcheck, Lighting cues, Recovery, and the Performance
  set-list controls using sections, repeaters, editors, conditions, and actions.
- Move the current workflow page fragments and their layout decisions into the
  document.
- Replace `workflow.js` and `lighting.js` with generic editor and action
  adapters. Preserve revision checks, pending-action resolution, explicit
  confirmation, and service-state restrictions in Python.

### 5. Consolidate action and status delivery

- Register every control named in `all-gui-elements.md` with its action and
  validate that every document action resolves.
- Serve page-specific data manifests from the schema and remove renderer-only
  status fields that no page declares.
- Retain existing status and workflow APIs while browser tests migrate. Remove
  obsolete per-page HTTP paths and JavaScript only after no document component
  depends on them.

### 6. Provision and document alternate displays

- Teach the service setup and local CLI to pass the selected GUI TOML path.
- Include the selected document's hash in provisioning state so changing a show
  layout updates the target without confusing it with application code changes.
- Document a small alternate GUI example, such as a performance-only tablet
  display, created by selecting pages and components from the same schema.
- Verify that provisioning rejects an invalid GUI document before replacing a
  running service.

## Acceptance criteria

- `showco/gui.toml` is the complete source for every page and element in
  [all-gui-elements.md](all-gui-elements.md); `views.py`, `site/*.html`, and
  page-specific scripts contain no page, label, layout, or visibility decision.
- Starting showCo with a valid alternate GUI file changes the available pages
  and controls without a Python change.
- Starting with an invalid document fails before serving HTTP and names the
  erroneous table and field.
- A GUI file cannot invoke an undeclared server action, access undeclared data,
  bypass the performance lock, or inject executable content.
- Existing behaviour remains intact: channel editing, musician editing,
  playback, set-list revisions, soundcheck confirmations, recovery
  confirmation, lighting pending resolution, action history, cable testing,
  streamO controls, and music modes all work through the registry.
- Browser tests cover the default complete document, a small alternate document,
  a broken document, stale status, disabled conditions, and a rejected
  protected action. Unit tests cover schema validation, page lookup, binding
  resolution, and action registration.
- The rendered default pages preserve their current accessible labels and
  operator-visible messages unless a deliberate GUI-data change changes them.

## Risks and decisions to make during implementation

- A generic renderer should not become a second programming language. Add a
  component kind only when two pages need the same behaviour; otherwise retain
  a narrowly defined existing kind.
- Some existing display values are currently formatted in Python. Keep their
  trusted formatting functions, but make their placement and label declarative.
- Browser-only preferences such as dimming and screen awake require component
  adapters. Their presence and placement are data-driven; browser APIs remain
  code.
- The default document must be reviewed like production configuration. Its
  validation tests and manifest snapshot are the protection against accidental
  loss of controls during a show-specific edit.

## Additional work beyond the prompt

None. This plan only describes the requested conversion to a data-driven GUI.
