const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor() {this.children = []; this.dataset = {}; this.listeners = {}; this.textContent = '';}
  append(...children) {this.children.push(...children);}
  replaceChildren(...children) {this.children = children;}
  addEventListener(name, callback) {this.listeners[name] = callback;}
}
const elements = new Map(['cue-position', 'cue-notes', 'cue-elapsed', 'cue-message',
  'cue-resolution', 'workflow-result', 'expected-inputs', 'soundcheck-scope',
  'soundcheck-results', 'recovery-options', 'recovery-state'].map(id => [id, new Element()]));
const buttons = ['setlist-next', 'setlist-repeat', 'setlist-accept', 'setlist-retry',
  'soundcheck-begin', 'recovery-refresh'].map(action => {
    const button = new Element(); button.dataset.workflowAction = action; return button;
  });
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
const posts = [];
let finish;
let online = true;
const context = vm.createContext({
  document: {
    getElementById: id => elements.get(id) || null,
    querySelectorAll: selector => selector === '[data-workflow-action]' ? buttons : [],
    createElement: () => new Element(), createTextNode: text => text,
  },
  AbortController, setTimeout() {}, clearTimeout() {},
  updateShowControls() {}, statusConnected() {}, statusFailed() {},
  fetch: async () => {if (!online) throw new Error('offline'); return {ok: true, json: async () => payload};},
  showAction: fields => {posts.push(fields); return new Promise(resolve => {finish = resolve;});},
});
async function flush() {for (let i = 0; i < 20; i++) await Promise.resolve();}
async function test() {
  vm.runInContext(fs.readFileSync('site/workflow.js', 'utf8'), context);
  await flush();
  assert.match(elements.get('cue-position').textContent, /Next: Opening/);
  assert.equal(buttons[0].disabled, false);
  assert.equal(buttons[1].disabled, true);
  assert.equal(elements.get('recovery-options').children.length, 0);
  const first = buttons[0].listeners.click();
  const duplicate = buttons[0].listeners.click();
  await flush();
  assert.equal(posts.length, 1);
  assert.equal(posts[0].revision, 1);
  await vm.runInContext('refreshWorkflows()', context);
  assert.ok(buttons.every(button => button.disabled));
  payload.setlist.current = 0; payload.setlist.next_index = 1; payload.setlist.revision = 3;
  finish('Cue sent'); await Promise.all([first, duplicate]);
  assert.equal(buttons[0].disabled, false);
  assert.equal(buttons[1].disabled, false);
  await buttons[0].listeners.click(); assert.equal(posts.length, 1);
  assert.match(elements.get('cue-notes').textContent, /<script>/);
  assert.equal(elements.get('cue-notes').children.length, 0);
  payload.setlist.pending = {label: 'interval'};
  await vm.runInContext('refreshWorkflows()', context);
  assert.equal(buttons[1].disabled, true);
  assert.equal(buttons[2].disabled, false);
  assert.equal(elements.get('cue-resolution').hidden, false);
  payload.soundcheck.results.lights = {state: 'skipped', evidence: 'No lights', checked_at: new Date().toISOString()};
  payload.show.recs.service.state = 'failed';
  await vm.runInContext('refreshWorkflows()', context);
  const skipped = elements.get('soundcheck-results').children[4];
  assert.match(skipped.textContent, /skipped/); assert.equal(skipped.className, 'failed');
  const options = elements.get('recovery-options').children;
  assert.equal(options.length, 1);
  assert.equal(options[0].children[2].dataset.service, 'recs');
  assert.match(options[0].children[0].textContent, /audio during the interruption is lost/);
  payload.show.performance_locked = true;
  await vm.runInContext('refreshWorkflows()', context);
  assert.equal(buttons[4].disabled, true);
  assert.equal(buttons[5].disabled, false);
  online = false;
  await assert.rejects(vm.runInContext('refreshWorkflows()', context), /offline/);
  assert.ok(buttons.every(button => button.disabled));
  await buttons[0].listeners.click(); assert.equal(posts.length, 1);
}
test().catch(error => {console.error(error); process.exitCode = 1;});
