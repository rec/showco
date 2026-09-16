let lightingState = null;
let lightingBusy = false;
let lightingRequest = 0;
let lastLightingAction = '';
let lastLightingActionAt = -Infinity;
let lightingEditorRevision = null;
const lightingEditor = document.getElementById('lighting-editor');
const lightingResult = document.getElementById('lighting-result');

function lightingRow(cue = {name: '', animation: ''}) {
  const row = document.createElement('fieldset');
  for (const [name, label] of [['name', 'Cue name'], ['animation', 'Installation look']]) {
    const caption = document.createElement('label'); caption.textContent = label;
    const input = document.createElement('input'); input.name = name;
    input.value = cue[name]; input.maxLength = 200;
    if (name === 'animation') input.setAttribute('list', 'lighting-looks');
    caption.append(input); row.append(caption);
  }
  for (const label of ['Move up', 'Move down', 'Remove']) {
    const button = document.createElement('button'); button.type = 'button'; button.textContent = label;
    button.addEventListener('click', () => {
      if (label === 'Remove') row.remove();
      else if (label === 'Move up' && row.previousElementSibling) row.previousElementSibling.before(row);
      else if (label === 'Move down' && row.nextElementSibling) row.nextElementSibling.after(row);
    }); row.append(button);
  }
  return row;
}

function lightingControls() {
  const state = lightingState?.lighting;
  const pending = state?.pending !== null;
  const unavailable = !lightingState || lightingBusy;
  const lyte = lightingState?.show.lyte;
  document.querySelectorAll('[data-lighting-action]').forEach(button => {
    const action = button.dataset.lightingAction;
    const resolve = ['lighting-accept', 'lighting-cancel', 'lighting-retry'].includes(action);
    button.disabled = unavailable || (resolve !== pending)
      || lyte?.service.state === 'disabled'
      || (action === 'lighting-save' && lightingState.show.performance_locked)
      || (action === 'lighting-go' && state.current + 1 >= state.cues.length)
      || (action === 'lighting-back' && state.current < 1)
      || (['lighting-go', 'lighting-back', 'lighting-retry'].includes(action) && !lyte?.running);
  });
  document.querySelectorAll('#lighting-editor input, #lighting-editor button, #add-lighting, #reload-lighting')
    .forEach(control => {control.disabled = unavailable || pending || lightingState.show.performance_locked || lyte?.service.state === 'disabled';});
}

function renderLighting(data) {
  lightingState = data; updateShowControls(data.show);
  const state = data.lighting; const lyte = data.show.lyte;
  const label = index => state.cues[index] ? `${index + 1}. ${state.cues[index].name} (${state.cues[index].animation})` : null;
  document.getElementById('lighting-position').textContent = `Current cue: ${label(state.current) || 'Not started'}. Next: ${label(state.current + 1) || 'End of list'}.`;
  document.getElementById('lighting-live').textContent = `lyte: ${lyte.service.state}. Active: ${lyte.active_animation || 'none'}. Queued: ${lyte.queued_animation || 'none'}. Blackout: ${lyte.blackout ? 'on' : 'off'}. Test: ${lyte.active_test || lyte.queued_test ? 'active or queued' : 'off'}. ${lyte.service.last_error || ''}`;
  document.getElementById('lighting-message').textContent = state.message;
  document.getElementById('lighting-resolution').hidden = state.pending === null;
  document.getElementById('lighting-pending').textContent = state.pending === null ? '' : `Unconfirmed cue: ${label(state.pending)}`;
  if (lightingEditorRevision === null) {
    lightingEditorRevision = state.revision;
    lightingEditor.replaceChildren(...state.cues.map(lightingRow));
    if (!state.cues.length) lightingEditor.append(lightingRow());
  }
  document.getElementById('lighting-looks').replaceChildren(...lyte.animations.map(name => {
    const option = document.createElement('option'); option.value = name; return option;
  }));
  lightingControls();
}

async function refreshLighting() {
  if (lightingBusy) return;
  const request = ++lightingRequest;
  const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch('/workflow-status', {cache: 'no-store', signal: controller.signal});
    if (!response.ok) throw new Error(`Cue status unavailable: ${response.status}`);
    const data = await response.json();
    if (request !== lightingRequest) return;
    renderLighting(data); statusConnected();
  } catch (error) {
    if (request !== lightingRequest) return;
    lightingState = null; lightingControls(); statusFailed(error);
  } finally {clearTimeout(timer);}
}

async function sendLighting(button) {
  if (lightingBusy || !lightingState || button.disabled) return;
  const action = button.dataset.lightingAction;
  if (action === lastLightingAction && Date.now() - lastLightingActionAt < 600) return;
  lastLightingAction = action; lastLightingActionAt = Date.now();
  const fields = {action, revision: lightingState.lighting.revision};
  if (action === 'lighting-save') {
    fields.revision = lightingEditorRevision;
    fields.cues = JSON.stringify(Array.from(lightingEditor.children).map(row => ({
      name: row.querySelector('[name=name]').value.trim(), animation: row.querySelector('[name=animation]').value.trim(),
    })));
  }
  fields.confirmation = document.getElementById('confirm-lighting').checked ? 'resolve' : '';
  lightingRequest++; lightingBusy = true; lightingControls(); lightingResult.textContent = 'Sending; check state before submitting again.';
  try {
    lightingResult.textContent = await showAction(fields);
    if (action === 'lighting-save') lightingEditorRevision = null;
  } catch (error) {lightingResult.textContent = error.message;}
  finally {
    document.getElementById('confirm-lighting').checked = false;
    // Keep controls disabled until a fresh status arrives after the action.
    lightingState = null; lightingBusy = false; lightingControls();
    await refreshLighting();
  }
}

document.querySelectorAll('[data-lighting-action]').forEach(button => button.addEventListener('click', () => sendLighting(button)));
document.getElementById('add-lighting').addEventListener('click', () => lightingEditor.append(lightingRow()));
document.getElementById('reload-lighting').addEventListener('click', () => {lightingEditorRevision = null; refreshLighting();});
function pollLighting() {refreshLighting().finally(() => setTimeout(pollLighting, 2000));}
pollLighting();
