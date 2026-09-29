const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const {status, expected} = JSON.parse(fs.readFileSync(0, 'utf8'));
const context = vm.createContext({
  document: {querySelectorAll: () => []},
  requestStatus: () => new Promise(() => {}),
  updateChannels() {},
  statusConnected() {},
  statusFailed() {},
  setTimeout() {},
});
vm.runInContext(fs.readFileSync('site/status-script.js', 'utf8'), context);
context.status = status;

function result(expression) {
  return vm.runInContext(expression, context);
}

const checks = {
  'readiness-state': 'statusText("readiness", status.readiness.ready)',
  'recs-health': '"recs: " + statusText("service", status.recs.service)',
  'recs-snapshot': '"recs snapshot: " + statusText("snapshot", status.recs.snapshot_error)',
  'recording-progress': '"recording progress: " + statusText("progress", status.recording_progress.message)',
  'streamo-health': '"streamo: " + statusText("service", status.streamo.service)',
  'lyte-health': '"lyte: " + statusText("lyte", status.lyte)',
  temperature: '"Pi temperature: " + statusText("temperature", status.system)',
  bitrate: '"Stream bitrate: " + statusText("bitrate", status.streamo)',
  'recording-detail': 'recordingText(status.recs)',
  'streaming-detail': 'streamingText(status.streamo)',
  'playback-state': 'statusText("playback_state", status.recs.playback)',
  'playback-selection': 'statusText("playback_selection", status.recs.playback)',
  'playback-position': 'statusText("playback_position", status.recs.playback)',
  'mixer-detail': 'mixerDetail(status.mixers[0])',
  'osc-detail': 'oscRecorderDetail(status.recs.osc[0])',
};
for (const [name, expression] of Object.entries(checks)) {
  assert.equal(result(expression), expected[name], name);
}

const meters = {};
for (const name of ['cpu', 'memory', 'disk']) {
  const span = {textContent: ''};
  const meter = {removeAttribute() {}};
  meters[name] = {span, querySelector: selector => selector === 'span' ? span : meter};
}
context.document.querySelectorAll = selector => {
  const match = selector.match(/^\[data-meter="(.*)"\]$/);
  return match ? [meters[match[1]]] : [];
};
result('updatePerformance(status)');
for (const name of ['cpu', 'memory', 'disk']) {
  assert.equal(meters[name].span.textContent, expected[`${name}-value`], name);
}
