const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const status = JSON.parse(fs.readFileSync(0, 'utf8'));
const elements = new Map([
  'streamo-health', 'bitrate', 'temperature', 'readiness-state',
  'readiness-checks', 'incidents', 'input-checks', 'osc-recorders', 'connection-status', 'lyte-health',
].map(id => [id, {textContent: '', replaceChildren() {}}]));
const context = vm.createContext({
  document: {
    getElementById: id => elements.get(id) || null,
    querySelectorAll: () => [],
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
    status.streamo.output_bitrate_kbps === null ? 'unknown' : '128 kbps');
  assert.equal(elements.get('temperature').textContent, '42.0 °C');
  assert.equal(elements.get('readiness-state').textContent, 'not ready');
  assert.equal(elements.get('osc-recorders').textContent, 'No OSC recorders.');
  assert.match(elements.get('connection-status').textContent, /^Connected/);
  assert.ok(!elements.get('lyte-health').textContent.includes('undefined'));
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
