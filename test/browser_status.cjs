const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function checkTeardownConfirmation() {
  const listeners = [];
  const attributes = new Set();
  const button = {
    disabled: false,
    setAttribute(name) {attributes.add(name);},
    removeAttribute(name) {attributes.delete(name);},
  };
  const form = {
    dataset: {confirm: 'true'},
    addEventListener(_name, callback) {listeners.push(callback);},
    closest() {return {};},
    querySelector() {return button;},
  };
  let approved = false;
  let requests = 0;
  const result = {};
  let deadline;
  let stalled = false;
  const browser = vm.createContext({
    document: {
      getElementById: () => null,
      querySelectorAll: selector => selector === '[data-transport] form' || selector === 'form[method=post]' ? [form] : [],
      querySelector: () => result,
    },
    confirm(message) {assert.equal(message, 'Are you sure?'); return approved;},
    FormData: class { *[Symbol.iterator]() {yield ['action', 'music-teardown'];} },
    URLSearchParams, AbortController,
    setTimeout(callback) {deadline = callback;}, clearTimeout() {},
    async fetch(_url, options) {
      requests++;
      if (stalled) return new Promise((_resolve, reject) => {
        options.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), {name: 'AbortError'})));
      });
      assert.equal(new URLSearchParams(options.body).get('action'), 'music-teardown');
      return {ok: true, json: async () => ({ok: true, message: 'Closing credits started'})};
    },
    async refreshShowStatus() {button.disabled = false;},
    statusFailed(error) {throw error;},
  });
  for (const script of ['show-controls.js', 'shutdown-action.js', 'playback-script.js']) {
    vm.runInContext(fs.readFileSync(`site/${script}`, 'utf8'), browser);
  }
  async function submit() {
    const event = {
      prevented: false, stopped: false,
      preventDefault() {this.prevented = true;},
      stopImmediatePropagation() {this.stopped = true;},
    };
    for (const listener of listeners) {
      await listener(event);
      if (event.stopped) break;
    }
    assert.equal(event.prevented, true);
  }
  await submit();
  assert.equal(requests, 0);
  assert.equal(button.disabled, false);
  assert.equal(attributes.has('aria-busy'), false);
  approved = true;
  await submit();
  assert.equal(requests, 1);
  assert.equal(result.textContent, 'Closing credits started');
  assert.equal(button.disabled, false);
  stalled = true;
  const pending = submit();
  for (let i = 0; i < 10; i++) await Promise.resolve();
  deadline();
  await pending;
  assert.match(result.textContent, /outcome unknown/);
  assert.equal(button.disabled, false);
  assert.equal(attributes.has('aria-busy'), false);
}

const status = JSON.parse(fs.readFileSync(0, 'utf8'));
const toggle = {
  disabled: true,
  busy: false,
  hasAttribute() {return this.busy;},
  closest() {return {dataset: {disabledWhen: 'streamo_unavailable'}};},
};
const recordingButtons = ['recs-pause-recording', 'recs-resume-recording'].map(action => ({
  disabled: false,
  hasAttribute() {return false;},
  closest() {return {dataset: {action}};},
}));
const elements = new Map([
  'streamo-health', 'bitrate', 'temperature', 'readiness-state',
  'readiness-checks', 'incidents', 'input-checks', 'osc-recorders', 'connection-status', 'lyte-health', 'playback-state',
].map(id => [id, {textContent: '', replaceChildren() {}}]));
for (const [id, value, format, label] of [
  ['streamo-health', 'show.streamo.service', 'service', 'streamo'],
  ['bitrate', 'show.streamo', 'bitrate', 'Stream bitrate'],
  ['temperature', 'show.system', 'temperature', 'Pi temperature'],
  ['readiness-state', 'show.readiness.ready', 'readiness', ''],
  ['lyte-health', 'show.lyte', 'lyte', 'lyte'],
  ['playback-state', 'show.recs.playback', 'playback_state', ''],
]) {
  elements.get(id).dataset = {value, format, label};
}
const osc = elements.get('osc-recorders');
osc.dataset = {source: 'show.recs.osc', layout: 'list', limit: '0', emptyText: 'No OSC recorders.'};
osc.replaceChildren = (...children) => {osc.textContent = children.map(item => item.textContent).join('');};
const context = vm.createContext({
  document: {
    getElementById: id => elements.get(id) || null,
    querySelectorAll: selector => selector === 'p[data-value]'
      ? [...elements.values()].filter(element => element.dataset?.value)
      : selector === '[data-layout="list"]' ? [osc]
      : selector === 'form[data-disabled-when] button' ? [toggle, ...recordingButtons]
      : selector.includes('form[data-action="recs-pause-recording"]') ? recordingButtons : [],
    createElement: () => ({append() {}}),
  },
  fetch: async () => ({ok: true, json: async () => status}),
  setTimeout() {},
  clearTimeout() {},
  AbortController,
});
vm.runInContext(fs.readFileSync('site/status-connection.js', 'utf8'), context);
vm.runInContext(fs.readFileSync('site/show-controls.js', 'utf8'), context);
vm.runInContext(fs.readFileSync('site/channel-controls.js', 'utf8'), context);
vm.runInContext(fs.readFileSync('site/status-script.js', 'utf8'), context);
vm.runInContext('updateStatus()', context).then(async () => {
  await checkTeardownConfirmation();
  const recordingState = {...status, recs: {...status.recs, service: {state: 'connected'}}};
  context.recordingState = recordingState;
  recordingState.recording_controls_available = false;
  vm.runInContext('updateConfiguredActions(recordingState)', context);
  assert.ok(recordingButtons.every(button => button.disabled));
  recordingState.recording_controls_available = true;
  vm.runInContext('updateConfiguredActions(recordingState)', context);
  assert.ok(recordingButtons.every(button => !button.disabled));
  vm.runInContext('showControlsUnavailable()', context);
  assert.ok(recordingButtons.every(button => button.disabled));
  assert.equal(elements.get('streamo-health').textContent,
    `streamo: ${status.streamo.service.state}`);
  assert.equal(elements.get('bitrate').textContent,
    status.streamo.output_bitrate_kbps === null ? 'Stream bitrate: unknown' : 'Stream bitrate: 128 kbps');
  assert.equal(elements.get('temperature').textContent, 'Pi temperature: 42.0 °C');
  assert.equal(elements.get('readiness-state').textContent, 'not ready');
  assert.equal(elements.get('playback-state').textContent, status.recs.playback.state);
  assert.equal(elements.get('osc-recorders').textContent, 'No OSC recorders.');
  assert.match(elements.get('connection-status').textContent, /^Connected/);
  assert.ok(!elements.get('lyte-health').textContent.includes('undefined'));
  assert.equal(toggle.disabled, !status.streamo.toggle_available);
  assert.equal(toggle.textContent, status.streamo.toggle_label);
  context.toggleStatus = {...status, streamo: {...status.streamo, toggle_available: true}};
  vm.runInContext('updateConfiguredActions(toggleStatus)', context);
  assert.equal(toggle.disabled, false);
  toggle.busy = true;
  toggle.textContent = 'Starting...';
  vm.runInContext('updateConfiguredActions(toggleStatus)', context);
  assert.equal(toggle.disabled, true);
  assert.equal(toggle.textContent, 'Starting...');
  toggle.busy = false;

  context.toggleStatus.music = {...status.music, mode: 'teardown'};
  context.toggleStatus.streamo.toggle_starts_stream = true;
  vm.runInContext('updateConfiguredActions(toggleStatus)', context);
  assert.equal(toggle.disabled, true);
  context.toggleStatus.streamo.toggle_starts_stream = false;
  vm.runInContext('updateConfiguredActions(toggleStatus)', context);
  assert.equal(toggle.disabled, false);

  const endForm = {
    dataset: {action: 'music-teardown', disabledWhen: '', confirm: 'true'},
    action: {value: 'music-teardown'},
  };
  const resumeForm = {dataset: {action: 'music-restart', disabledWhen: ''}};
  const end = {
    textContent: 'End show', hasAttribute() {return false;}, closest() {return endForm;},
  };
  const resume = {
    textContent: 'Resume show', hasAttribute() {return false;}, closest() {return resumeForm;},
  };
  context.document.querySelectorAll = selector =>
    selector === 'form[data-disabled-when] button' ? [end, resume] : [];
  context.restartStatus = {...status, end_show_available: false,
    music: {...status.music, restart_available: true}};
  vm.runInContext('updateConfiguredActions(restartStatus)', context);
  assert.equal(end.disabled, true);
  assert.equal(resume.disabled, false);
  assert.equal(endForm.action.value, 'music-teardown');
  assert.equal(endForm.dataset.confirm, 'true');
  assert.equal(end.textContent, 'End show');
  assert.equal(resume.textContent, 'Resume show');
  context.restartStatus.end_show_available = true;
  context.restartStatus.music.restart_available = false;
  vm.runInContext('updateConfiguredActions(restartStatus)', context);
  assert.equal(end.disabled, false);
  assert.equal(resume.disabled, true);

  const fields = [
    {dataset: {value: 'item.name', format: 'bold'}, textContent: ''},
    {dataset: {value: 'item.message', format: ''}, textContent: ''},
  ];
  const row = {className: '', querySelectorAll: () => fields};
  const checks = {
    dataset: {source: 'show.input_checks', limit: '0', template: 'checks-template'},
    replaceChildren(child) {this.child = child;},
  };
  context.document.querySelectorAll = selector => selector === '[data-layout="list"]' ? [checks] : [];
  context.document.getElementById = id => id === 'checks-template'
    ? {content: {firstElementChild: {cloneNode: () => row}}}
    : elements.get(id) || null;
  context.document.createElement = () => ({append(child) {this.child = child;}});
  context.checkStatus = {input_checks: [{name: 'X18 input 1', message: 'clipping', ok: false}]};
  vm.runInContext('updateConfiguredLists(checkStatus)', context);
  assert.equal(checks.child.child, row);
  assert.equal(row.className, 'failed');
  assert.deepEqual(fields.map(field => field.textContent), ['X18 input 1', 'clipping']);

  toggle.disabled = false;
  context.document.querySelectorAll = selector =>
    selector === '[data-disabled-when="streamo_unavailable"] button' ? [toggle] : [];
  context.fetch = async () => {throw new Error('offline');};
  await vm.runInContext('updateStatus()', context);
  assert.match(elements.get('connection-status').textContent, /offline/);
  assert.equal(elements.get('readiness-state').textContent, 'unknown (status unavailable)');
  assert.equal(toggle.disabled, true);

  let respond;
  let submitted;
  context.fetch = (_url, options) => {
    submitted = new URLSearchParams(options.body);
    return new Promise(resolve => {respond = resolve;});
  };
  const input = {value: 'A', dataset: {action: 'recs-track-name'}, setCustomValidity() {}};
  const form = {dataset: {savedTrackName: 'old', channels: '1', device: 'X18'}, querySelector: () => input};
  context.form = form;
  context.URLSearchParams = URLSearchParams;
  const saved = vm.runInContext('saveTrackName(form)', context);
  assert.equal(submitted.get('expected_name'), 'old');
  assert.equal(submitted.get('channel'), '1');
  input.value = 'B';
  respond({ok: true, json: async () => ({ok: true})});
  await saved;
  assert.equal(form.dataset.savedTrackName, 'A');
  assert.equal(input.value, 'B');

  const title = {textContent: ''};
  const trackInput = {value: ''};
  const channelState = {dataset: {label: 'Recording'}, setAttribute(name, value) {this[name] = value;}, getAttribute(name) {return this[name];}};
  const stereo = {checked: false, disabled: false, addEventListener() {}};
  const clonedForm = {
    dataset: {},
    querySelector(selector) {
      if (selector === '[data-action="recs-track-name"]') return trackInput;
      if (selector === '.channel-caption b') return title;
      if (selector === '.channel-state') return channelState;
      if (selector === '.stereo input') return stereo;
      return null;
    },
  };
  const container = {
    dataset: {template: 'tracks-template', emptyText: 'No tracks available.'},
    querySelectorAll() {return this.children || [];},
    contains: form => container.children.includes(form),
    replaceChildren(...children) {this.children = children;},
    append(...children) {this.children.push(...children);},
  };
  context.document.querySelectorAll = selector =>
    selector === '[data-source="show.recs.channels"]' ? [container] : [];
  context.document.activeElement = {closest: () => null};
  context.document.getElementById = id => id === 'tracks-template'
    ? {content: {firstElementChild: {cloneNode: () => clonedForm}}}
    : elements.get(id) || null;
  vm.runInContext('updateChannels([{name:"1", state:"healthy", device:"X18", channels:[1], on:true}])', context);
  assert.equal(container.children[0], clonedForm);
  assert.equal(title.textContent, '1');
  assert.equal(trackInput.value, '1');
  assert.equal(clonedForm.dataset.device, 'X18');
  trackInput.value = 'Editing';
  context.document.activeElement = {closest: () => clonedForm};
  container.replaceChildren = () => {throw new Error('focused channel was replaced');};
  vm.runInContext('updateChannels([{name:"1", state:"clipping", device:"X18", channels:[1], on:false}])', context);
  assert.equal(trackInput.value, 'Editing');
  assert.equal(clonedForm.className, 'level clipping');
  assert.equal(channelState['aria-label'], 'not recording');
  assert.equal(container.children[0], clonedForm);

  const edited = {
    value: 'New name', dataset: {action: 'recs-track-name'},
    setCustomValidity(message) {this.error = message;}, reportValidity() {},
  };
  const editedForm = {
    dataset: {device: 'X18', channel: '1', channels: '1', savedTrackName: 'Old name'},
    querySelector: () => edited,
  };
  context.editedForm = editedForm;
  const realRequestStatus = context.requestStatus;
  context.requestStatus = () => Promise.resolve({recs: {channels: [
    {name: 'Old name', state: 'healthy', device: 'X18', channels: [1], on: true},
  ]}});
  const lost = vm.runInContext('new TypeError("connection lost")', context);
  context.fetch = () => Promise.reject(lost);
  await vm.runInContext('saveTrackName(editedForm)', context);
  assert.match(edited.error, /outcome unknown/);
  assert.equal(editedForm.dataset.savedTrackName, 'Old name');

  context.requestStatus = () => Promise.resolve({recs: {channels: [
    {name: 'Newer name', state: 'healthy', device: 'X18', channels: [1], on: true},
  ]}});
  context.fetch = () => Promise.resolve({ok: true, json: () => Promise.resolve({
    ok: false, message: 'Track name changed in recs; current name is Newer name',
  })});
  await vm.runInContext('saveTrackName(editedForm)', context);
  assert.equal(editedForm.dataset.savedTrackName, 'Newer name');
  assert.match(edited.error, /current name is Newer name/);
  context.requestStatus = realRequestStatus;

  let deadline;
  context.setTimeout = callback => {deadline = callback; return 1;};
  context.fetch = (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error('aborted')));
  });
  const pending = vm.runInContext('requestStatus()', context);
  deadline();
  await assert.rejects(pending, /aborted/);
}).catch(error => { console.error(error); process.exitCode = 1; });
