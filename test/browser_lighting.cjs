const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor() {this.children = []; this.dataset = {}; this.listeners = {}; this.textContent = '';}
  append(...children) {this.children.push(...children);}
  replaceChildren(...children) {this.children = children;}
  addEventListener(name, callback) {this.listeners[name] = callback;}
  setAttribute(name, value) {this[name] = value;}
}
const elements = new Map(['lighting-position', 'lighting-live', 'lighting-message',
  'lighting-resolution', 'lighting-pending', 'lighting-result', 'lighting-editor',
  'lighting-looks', 'confirm-lighting', 'add-lighting', 'reload-lighting'].map(id => [id, new Element()]));
const buttons = new Map(['go', 'back', 'save', 'accept', 'cancel', 'retry'].map(action => {
  const button = new Element(); button.dataset.lightingAction = `lighting-${action}`; return [action, button];
}));
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
const posts = [];
let finish;
let online = true;
let lateStatus = null;
const context = vm.createContext({
  document: {
    getElementById: id => elements.get(id),
    querySelectorAll: selector => selector === '[data-lighting-action]' ? [...buttons.values()] : [],
    createElement: () => new Element(),
  },
  AbortController, setTimeout() {}, clearTimeout() {},
  updateShowControls() {}, statusConnected() {}, statusFailed() {},
  fetch: async () => {
    if (!online) throw new Error('offline');
    if (lateStatus) return new Promise(resolve => {lateStatus.resolve = resolve;});
    return {ok: true, json: async () => payload};
  },
  showAction: fields => {posts.push(fields); return new Promise(resolve => {finish = resolve;});},
});
async function flush() {for (let i = 0; i < 20; i++) await Promise.resolve();}
async function test() {
  vm.runInContext(fs.readFileSync('site/lighting.js', 'utf8'), context);
  await flush();
  assert.match(elements.get('lighting-position').textContent, /Next: 1. Opening/);
  assert.equal(buttons.get('go').disabled, false);
  assert.equal(buttons.get('back').disabled, true);
  assert.equal(elements.get('lighting-editor').children.length, 2);
  lateStatus = {};
  const earlierPoll = vm.runInContext('refreshLighting()', context);
  const first = buttons.get('go').listeners.click();
  await buttons.get('go').listeners.click();
  assert.equal(posts.length, 1);
  const staleResponse = structuredClone(payload);
  payload.lighting.current = 0; payload.lighting.revision += 2;
  const delayed = lateStatus; lateStatus = null;
  finish('queued'); await first;
  delayed.resolve({ok: true, json: async () => staleResponse}); await earlierPoll;
  assert.match(elements.get('lighting-position').textContent, /Current cue: 1. Opening/);
  payload.lighting.pending = 1;
  await vm.runInContext('refreshLighting()', context);
  assert.equal(buttons.get('go').disabled, true);
  assert.equal(buttons.get('accept').disabled, false);
  assert.equal(elements.get('lighting-resolution').hidden, false);
  payload.lighting.pending = null; payload.lighting.current = 1;
  payload.show.performance_locked = true;
  await vm.runInContext('refreshLighting()', context);
  assert.equal(buttons.get('back').disabled, false);
  assert.equal(buttons.get('go').disabled, true);
  assert.equal(buttons.get('save').disabled, true);
  online = false;
  await vm.runInContext('refreshLighting()', context);
  assert.ok([...buttons.values()].every(button => button.disabled));
  online = true;
  payload.show.lyte.active_animation = 'idle';
  payload.show.lyte.blackout = true;
  await vm.runInContext('refreshLighting()', context);
  assert.equal(posts.length, 1);
  assert.match(elements.get('lighting-position').textContent, /Current cue: 2. Finale/);
  assert.match(elements.get('lighting-live').textContent, /Active: idle/);
  assert.match(elements.get('lighting-live').textContent, /Blackout: on/);
}
test().catch(error => {console.error(error); process.exitCode = 1;});
