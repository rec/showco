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
      pins: pinnedInputs, dim: document.getElementById('dim-display')?.checked === true,
    }));
  } catch {
    const result = document.getElementById('performance-result');
    if (result) result.textContent = 'Browser settings cannot be saved; changes apply only to this page.';
  }
}

try {
  const settings = JSON.parse(localStorage.getItem('showco-performance') || '{}');
  pinnedInputs = Array.isArray(settings.pins) ? settings.pins.filter(value => typeof value === 'string') : [];
  const dim = document.getElementById('dim-display');
  if (dim) dim.checked = settings.dim === true;
  document.body.classList.toggle('dim-display', settings.dim === true);
} catch {
  const result = document.getElementById('performance-result');
  if (result) result.textContent = 'Saved display settings unavailable; using defaults.';
}

document.getElementById('dim-display')?.addEventListener('change', event => {
  document.body.classList.toggle('dim-display', event.target.checked);
  saveDisplaySettings();
});

function renderPerformance(status) {
  const recs = status.recs;
  const recording = document.getElementById('performance-recording');
  if (recording) recording.textContent =
    `recs ${recs.service.state}; recording ${recs.service.state !== 'connected' || !recs.snapshot_available ? 'state unavailable' : recs.paused ? 'paused' : 'enabled'}`;
  const disk = recs.disk;
  const destination = document.getElementById('performance-disk');
  if (destination) destination.textContent = disk
    ? `${disk.path}: ${(disk.free_bytes / 1073741824).toFixed(1)} GiB free; ${disk.estimated_seconds_remaining === null ? 'remaining time unknown' : Math.floor(disk.estimated_seconds_remaining / 60) + ' minutes estimated remaining'}`
    : 'Recording destination and capacity unavailable';
  if (destination) {
    const stale = recs.disk_error || recs.snapshot_error;
    if (disk && stale) destination.textContent += `; stale: ${stale}`;
    if (disk?.on_system_disk && !stale) destination.textContent += '; warning: system disk';
    destination.className = stale || disk?.on_system_disk ? 'failed' : '';
  }
  const progress = document.getElementById('performance-progress');
  if (progress) progress.textContent =
    `Audio writes: ${status.recording_progress.message}. Silence filtering may pause file growth.`;
  const stream = document.getElementById('performance-stream');
  if (stream) stream.textContent =
    `streamO ${status.streamo.service.state}; ${status.streamo.stream_state}${status.streamo.muted ? '; muted' : ''}`;
  document.querySelectorAll('[data-performance-action]').forEach(button => {
    const action = button.dataset.performanceAction;
    if (action === 'streamo-stop') {
      button.dataset.twitchStopAvailable = String(status.twitch_stop_available);
    }
    button.disabled = performanceBusy || (action === 'streamo-stop'
      ? !status.twitch_stop_available
      : recs.service.state !== 'connected' ||
        (['recs-pause-recording', 'recs-resume-recording'].includes(action) &&
          !status.recording_controls_available));
  });
  const signature = JSON.stringify(recs.channels.map(channel => [inputKey(channel), channel.name]));
  const choices = document.getElementById('input-pins');
  if (choices && signature !== pinSignature) {
    pinSignature = signature;
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
  if (!inputs) return;
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
    if (result) result.textContent = 'Sending action...';
    try {
      const fields = {action: button.dataset.performanceAction};
      if (fields.action === 'recs-marker') fields.label = 'performance moment';
      const message = await showAction(fields);
      if (result) result.textContent = message;
    } catch (error) {if (result) result.textContent = error.message;}
    finally {
      performanceBusy = false;
      document.querySelectorAll('[data-performance-action="streamo-stop"]').forEach(value => {
        value.disabled = value.dataset.twitchStopAvailable !== 'true';
      });
    }
    // Recording controls still require a fresh poll after an uncertain response.
  });
});

async function requestAwake() {
  if (requestingAwake) return;
  const state = document.getElementById('awake-status');
  if (!state) return;
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

document.getElementById('keep-awake')?.addEventListener('click', async event => {
  wantAwake = !wantAwake;
  event.target.textContent = wantAwake ? 'Allow screen sleep' : 'Keep screen awake';
  if (wantAwake) await requestAwake();
  else if (wakeLock) await wakeLock.release();
});
document.addEventListener('visibilitychange', () => {
  if (wantAwake && document.visibilityState === 'visible' && !wakeLock) requestAwake();
});

function pollPerformance() {
  requestStatus().then(status => {renderPerformance(status); statusConnected(status);})
    .catch(error => {
      statusFailed(error);
      const recording = document.getElementById('performance-recording');
      if (recording) recording.textContent = 'Recording state unknown: status unavailable';
      const destination = document.getElementById('performance-disk');
      if (destination) {
        destination.textContent = 'Recording destination and capacity unavailable';
        destination.className = 'failed';
      }
    }).finally(() => setTimeout(pollPerformance, 1000));
}
pollPerformance();
