const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const status = JSON.parse(fs.readFileSync(0, 'utf8'));
const elements = new Map([
  'streamo-health', 'bitrate', 'temperature', 'readiness-state',
  'readiness-checks', 'incidents', 'input-checks', 'osc-recorders',
].map(id => [id, {textContent: '', replaceChildren() {}}]));
const context = vm.createContext({
  document: {
    getElementById: id => elements.get(id) || null,
    querySelectorAll: () => [],
    createElement: () => ({append() {}}),
  },
  fetch: async () => ({ok: true, json: async () => status}),
  setTimeout() {},
});
vm.runInContext(fs.readFileSync('site/status-script.js', 'utf8'), context);
vm.runInContext('updateStatus()', context).then(() => {
  assert.equal(elements.get('streamo-health').textContent,
    `streamo: ${status.streamo.service.state}`);
  assert.equal(elements.get('bitrate').textContent,
    status.streamo.output_bitrate_kbps === null ? 'unknown' : '128 kbps');
  assert.equal(elements.get('temperature').textContent, '42.0 °C');
  assert.equal(elements.get('readiness-state').textContent, 'not ready');
  assert.equal(elements.get('osc-recorders').textContent, 'No OSC recorders.');
}).catch(error => { console.error(error); process.exitCode = 1; });
