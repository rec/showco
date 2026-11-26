const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor() {
    this.textContent = ''; this.children = []; this.listeners = {};
    this.disabled = false; this.checked = false; this.dataset = {};
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; this.textContent = ''; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  setAttribute(name, value) { this[name] = value; }
  querySelector() { return confirmation; }
}

const elements = new Map();
for (const id of ['connection-status', 'performance-lock-state', 'fault-banner',
  'monitoring-error', 'performance-lock-form', 'lock-result', 'performance-result',
  'performance-recording', 'performance-disk', 'performance-progress', 'performance-stream',
  'input-pins', 'performance-inputs', 'dim-display', 'keep-awake', 'awake-status']) {
  elements.set(id, new Element());
}
const confirmation = new Element();
const lockButtons = ['performance-lock', 'performance-unlock'].map(value => Object.assign(new Element(), {value}));
const actionButtons = ['recs-marker', 'recs-pause-recording', 'recs-resume-recording'].map(value => {
  const button = new Element(); button.dataset.performanceAction = value; return button;
});
const stopButton = new Element();
stopButton.dataset.performanceAction = 'streamo-stop';
const performanceButtons = [...actionButtons, stopButton];
const settings = new Map();
const classes = new Set();
const status = JSON.parse(fs.readFileSync(0, 'utf8'));
const posts = [];
let respond;
let pendingAction = false;
const context = vm.createContext({
  document: {
    body: {classList: {toggle(name, enabled) {enabled ? classes.add(name) : classes.delete(name);}}},
    getElementById: id => elements.get(id) || null,
    querySelectorAll: selector => selector === '[data-performance-action], #performance-lock-form button'
      ? [...performanceButtons, ...lockButtons]
      : selector === '[data-performance-action="streamo-stop"]' ? [stopButton]
      : selector === '[data-performance-action]' ? performanceButtons
      : selector === '#performance-lock-form button' ? lockButtons : [],
    createElement: () => new Element(), createTextNode: text => text,
    addEventListener() {}, visibilityState: 'visible',
  },
  localStorage: {getItem: key => settings.get(key), setItem: (key, value) => settings.set(key, value)},
  navigator: {}, AbortController, URLSearchParams,
  setTimeout() {}, clearTimeout() {},
  fetch: async (url, options) => {
    if (url === '/status') return {ok: true, json: async () => status};
    posts.push(Object.fromEntries(options.body));
    if (pendingAction) return new Promise(resolve => {respond = resolve;});
    return {ok: true, json: async () => ({ok: true, message: 'done'})};
  },
});

async function flush() { for (let i = 0; i < 20; i++) await Promise.resolve(); }

async function test() {
  for (const file of ['status-connection.js', 'show-controls.js', 'performance.js']) {
    vm.runInContext(fs.readFileSync(`site/${file}`, 'utf8'), context);
  }
  await flush();
  assert.match(elements.get('performance-recording').textContent, /recording enabled/);
  assert.match(elements.get('performance-disk').textContent, /recordings/);
  status.recs.snapshot_error = 'recs unavailable';
  context.currentStatus = status;
  vm.runInContext('renderPerformance(currentStatus)', context);
  assert.match(elements.get('performance-disk').textContent, /stale: recs unavailable/);
  assert.equal(elements.get('performance-disk').className, 'failed');
  status.recs.snapshot_error = null;
  vm.runInContext('renderPerformance(currentStatus)', context);
  assert.doesNotMatch(elements.get('performance-disk').textContent, /stale/);
  assert.ok(actionButtons.every(button => !button.disabled));
  status.recording_controls_available = false;
  context.currentStatus = status;
  vm.runInContext('renderPerformance(currentStatus)', context);
  assert.equal(actionButtons[0].disabled, false);
  assert.ok(actionButtons.slice(1).every(button => button.disabled));
  await actionButtons[1].listeners.click();
  await actionButtons[2].listeners.click();
  assert.equal(posts.length, 0);
  status.recording_controls_available = true;
  vm.runInContext('renderPerformance(currentStatus)', context);
  assert.ok(actionButtons.every(button => !button.disabled));
  assert.match(elements.get('performance-lock-state').textContent, /protected actions locked/);
  assert.equal(lockButtons[0].disabled, true);
  assert.equal(lockButtons[1].disabled, false);

  const pin = elements.get('input-pins').children[0].children[0];
  pin.checked = true; pin.listeners.change();
  await vm.runInContext('pollPerformance()', context); await flush();
  assert.match(elements.get('performance-inputs').children[0].textContent, /Vocal/);
  assert.equal(elements.get('input-pins').children[0].children[0], pin);
  const dim = elements.get('dim-display'); dim.checked = true;
  dim.listeners.change({target: dim});
  assert.ok(classes.has('dim-display'));
  assert.equal(JSON.parse(settings.get('showco-performance')).pins.length, 1);
  await elements.get('keep-awake').listeners.click({target: elements.get('keep-awake')});
  assert.match(elements.get('awake-status').textContent, /unavailable/);

  pendingAction = true;
  const first = actionButtons[0].listeners.click();
  const duplicate = actionButtons[0].listeners.click();
  await flush();
  assert.equal(posts.length, 1);
  await vm.runInContext('pollPerformance()', context); await flush();
  assert.ok(actionButtons.every(button => button.disabled));
  respond({ok: true, json: async () => ({ok: true, message: 'marked'})});
  await Promise.all([first, duplicate]);
  assert.deepEqual(posts[0], {action: 'recs-marker', label: 'performance moment'});
  assert.equal(elements.get('performance-result').textContent, 'marked');

  pendingAction = false;
  assert.equal(stopButton.disabled, true);
  status.twitch_stop_available = true;
  status.recs.service.state = 'offline';
  await vm.runInContext('pollPerformance()', context); await flush();
  assert.ok(actionButtons.every(button => button.disabled));
  assert.equal(stopButton.disabled, false);
  await stopButton.listeners.click();
  assert.deepEqual(posts[1], {action: 'streamo-stop'});

  status.active_faults = [
    {name: 'recs', message: 'offline', next_action: 'Inspect Health',
      started_at: '2026-09-16T10:00:00Z', acknowledged: true},
    {name: 'recs recording errors', message: 'Instrument input failed',
      next_action: 'Check the input', started_at: '2026-09-16T10:00:01Z', acknowledged: false},
  ];
  status.monitoring_error = 'Cannot save incident history: full disk';
  await vm.runInContext('pollPerformance()', context); await flush();
  const banner = elements.get('fault-banner');
  assert.equal(banner.className, 'failed');
  const [recorder, input] = banner.children[0].children;
  assert.match(recorder.children[1].textContent, /Acknowledged; still unresolved/);
  assert.match(input.children[0].textContent, /Instrument input failed/);
  assert.equal(input.children[1].textContent, 'Acknowledge');
  assert.match(elements.get('monitoring-error').textContent, /full disk/);

  context.fetch = async () => {throw new Error('offline');};
  await vm.runInContext('pollPerformance()', context); await flush();
  assert.ok(actionButtons.every(button => button.disabled));
  assert.equal(stopButton.disabled, false);
  assert.ok(lockButtons.every(button => button.disabled));
  assert.match(elements.get('performance-recording').textContent, /unknown/);
  assert.match(elements.get('connection-status').textContent, /may be stale/);

  for (const id of ['performance-disk', 'performance-progress', 'performance-stream',
    'input-pins', 'performance-inputs', 'dim-display', 'keep-awake', 'awake-status']) {
    elements.delete(id);
  }
  context.currentStatus = status;
  vm.runInContext('renderPerformance(currentStatus)', context);
}
test().catch(error => { console.error(error); process.exitCode = 1; });
