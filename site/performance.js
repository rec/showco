let performanceBusy = false;
let pinSignature = '';
let pinnedInputs = [];
let wakeLock = null;
let wantAwake = false;
let requestingAwake = false;

function inputKey(channel) { return JSON.stringify([channel.device, channel.channels]); }

function saveDisplaySettings() {
  try {
    localStorage.setItem('showco-performance', JSON.stringify({
      pins: pinnedInputs, dim: document.getElementById('dim-display').checked,
    }));
  } catch {
    document.getElementById('performance-result').textContent = 'Browser settings cannot be saved; changes apply only to this page.';
  }
}

try {
  const settings = JSON.parse(localStorage.getItem('showco-performance') || '{}');
  pinnedInputs = Array.isArray(settings.pins) ? settings.pins.filter(value => typeof value === 'string') : [];
  document.getElementById('dim-display').checked = settings.dim === true;
  document.body.classList.toggle('dim-display', settings.dim === true);
} catch {
  document.getElementById('performance-result').textContent = 'Saved display settings unavailable; using defaults.';
}

document.getElementById('dim-display').addEventListener('change', event => {
  document.body.classList.toggle('dim-display', event.target.checked);
  saveDisplaySettings();
});

function renderPerformance(status) {
  const recs = status.recs;
  document.getElementById('performance-recording').textContent =
    `recs ${recs.service.state}; recording ${recs.paused ? 'paused' : recs.recording ? 'requested' : 'stopped'}`;
  const disk = recs.disk;
  document.getElementById('performance-disk').textContent = disk
    ? `${disk.path}: ${(disk.free_bytes / 1073741824).toFixed(1)} GiB free; ${disk.estimated_seconds_remaining === null ? 'remaining time unknown' : Math.floor(disk.estimated_seconds_remaining / 60) + ' minutes estimated remaining'}`
    : 'Recording destination and capacity unavailable';
  document.getElementById('performance-progress').textContent =
    `Audio writes: ${status.recording_progress.message}. Silence filtering may pause file growth.`;
  document.getElementById('performance-stream').textContent =
    `streamO ${status.streamo.service.state}; ${status.streamo.stream_state}${status.streamo.muted ? '; muted' : ''}`;
  document.querySelectorAll('[data-performance-action]').forEach(button => {
    button.disabled = performanceBusy || recs.service.state !== 'connected';
  });
  const signature = JSON.stringify(recs.channels.map(channel => [inputKey(channel), channel.name]));
  if (signature !== pinSignature) {
    pinSignature = signature;
    const choices = document.getElementById('input-pins');
    choices.replaceChildren();
    for (const channel of recs.channels) {
      const label = document.createElement('label');
      const input = document.createElement('input');
      input.type = 'checkbox';
      const key = inputKey(channel);
      input.checked = pinnedInputs.includes(key);
      input.addEventListener('change', () => {
        pinnedInputs = input.checked ? [...pinnedInputs, key] : pinnedInputs.filter(value => value !== key);
        saveDisplaySettings();
      });
      label.append(input, document.createTextNode(`${channel.device}: ${channel.name}`));
      choices.append(label);
    }
  }
  const inputs = document.getElementById('performance-inputs');
  inputs.replaceChildren();
  for (const key of pinnedInputs) {
    const channel = recs.channels.find(value => inputKey(value) === key);
    const row = document.createElement('p');
    row.textContent = channel
      ? `${channel.device}: ${channel.name}: ${channel.state}; ${channel.on ? 'recording enabled' : 'not recording'}`
      : `Pinned input unavailable: ${key}`;
    if (!channel) {
      const remove = document.createElement('button');
      remove.textContent = 'Unpin unavailable input';
      remove.addEventListener('click', () => {
        pinnedInputs = pinnedInputs.filter(value => value !== key);
        saveDisplaySettings();
      });
      row.append(remove);
    }
    inputs.append(row);
  }
  if (!pinnedInputs.length) inputs.textContent = 'Choose the inputs you want to watch.';
}

document.querySelectorAll('[data-performance-action]').forEach(button => {
  button.addEventListener('click', async () => {
    if (performanceBusy || button.disabled) return;
    performanceBusy = true;
    document.querySelectorAll('[data-performance-action]').forEach(value => {value.disabled = true;});
    const result = document.getElementById('performance-result');
    result.textContent = 'Sending action...';
    try {
      const fields = {action: button.dataset.performanceAction};
      if (fields.action === 'recs-marker') fields.label = 'performance moment';
      result.textContent = await showAction(fields);
    } catch (error) {result.textContent = error.message;}
    finally {performanceBusy = false;}
    // A fresh poll must re-enable controls after an uncertain response.
  });
});

async function requestAwake() {
  if (requestingAwake) return;
  const state = document.getElementById('awake-status');
  if (!('wakeLock' in navigator)) {state.textContent = 'Screen awake unavailable in this browser or connection.'; return;}
  requestingAwake = true;
  try {
    const acquired = await navigator.wakeLock.request('screen');
    if (!wantAwake) {await acquired.release(); return;}
    wakeLock = acquired;
    state.textContent = 'Screen awake: active';
    wakeLock.addEventListener('release', () => {
      wakeLock = null;
      state.textContent = 'Screen awake: released';
    });
  } catch (error) {state.textContent = `Screen awake unavailable: ${error.message}`;}
  finally {requestingAwake = false;}
}

document.getElementById('keep-awake').addEventListener('click', async event => {
  wantAwake = !wantAwake;
  event.target.textContent = wantAwake ? 'Allow screen sleep' : 'Keep screen awake';
  if (wantAwake) await requestAwake();
  else if (wakeLock) await wakeLock.release();
});
document.addEventListener('visibilitychange', () => {
  if (wantAwake && document.visibilityState === 'visible' && !wakeLock) requestAwake();
});

function pollPerformance() {
  requestStatus().then(status => {renderPerformance(status); statusConnected();})
    .catch(error => {
      statusFailed(error);
      document.getElementById('performance-recording').textContent = 'Recording state unknown: status unavailable';
    }).finally(() => setTimeout(pollPerformance, 1000));
}
pollPerformance();
