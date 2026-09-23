const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const status = JSON.parse(fs.readFileSync(0, 'utf8'));
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
      : selector === '[data-layout="list"]' ? [osc] : [],
    createElement: () => ({append() {}}),
  },
  fetch: async () => ({ok: true, json: async () => status}),
  setTimeout() {},
  clearTimeout() {},
  AbortController,
});
vm.runInContext(fs.readFileSync('site/status-connection.js', 'utf8'), context);
vm.runInContext(fs.readFileSync('site/channel-controls.js', 'utf8'), context);
vm.runInContext(fs.readFileSync('site/status-script.js', 'utf8'), context);
vm.runInContext('updateStatus()', context).then(async () => {
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

  context.fetch = async () => {throw new Error('offline');};
  await vm.runInContext('updateStatus()', context);
  assert.match(elements.get('connection-status').textContent, /offline/);
  assert.equal(elements.get('readiness-state').textContent, 'unknown (status unavailable)');

  let respond;
  context.fetch = () => new Promise(resolve => {respond = resolve;});
  const input = {value: 'A', dataset: {action: 'recs-track-name'}, setCustomValidity() {}};
  const form = {dataset: {savedTrackName: 'old'}, querySelector: () => input};
  context.form = form;
  context.URLSearchParams = URLSearchParams;
  const saved = vm.runInContext('saveTrackName(form)', context);
  input.value = 'B';
  respond({ok: true, json: async () => ({ok: true})});
  await saved;
  assert.equal(form.dataset.savedTrackName, 'A');
  assert.equal(input.value, 'B');

  const title = {textContent: ''};
  const trackInput = {value: ''};
  const clonedForm = {
    dataset: {},
    querySelector(selector) {
      if (selector === '[data-action="recs-track-name"]') return trackInput;
      if (selector === '.channel-caption b') return title;
      return null;
    },
  };
  const container = {
    dataset: {template: 'tracks-template', emptyText: 'No tracks available.'},
    querySelectorAll: () => [],
    replaceChildren(...children) {this.children = children;},
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

  let deadline;
  context.setTimeout = callback => {deadline = callback; return 1;};
  context.fetch = (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error('aborted')));
  });
  const pending = vm.runInContext('requestStatus()', context);
  deadline();
  await assert.rejects(pending, /aborted/);
}).catch(error => { console.error(error); process.exitCode = 1; });
