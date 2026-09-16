let workflowState = null;
let workflowBusy = false;
let editorRevision = null;
let expectedSignature = '';
let recoverySignature = '';
let lastWorkflowAction = '';
let lastWorkflowActionAt = -Infinity;

function workflowResult(message) {
  const result = document.getElementById('workflow-result');
  if (result) result.textContent = message;
}

function songRow(song = {title: '', seconds: 0, notes: ''}) {
  const row = document.createElement('fieldset');
  row.dataset.songRow = 'true';
  for (const [name, label, value] of [['title', 'Song title', song.title],
    ['minutes', 'Expected minutes (0 for unknown)', song.seconds / 60], ['notes', 'Performer notes', song.notes]]) {
    const caption = document.createElement('label'); caption.textContent = label;
    const input = document.createElement(name === 'notes' ? 'textarea' : 'input');
    input.name = name; input.value = value;
    if (name === 'minutes') {input.type = 'number'; input.min = '0'; input.max = '1440'; input.step = '0.1';}
    else input.maxLength = name === 'title' ? 200 : 1000;
    caption.append(input); row.append(caption);
  }
  for (const label of ['Move up', 'Move down', 'Remove']) {
    const button = document.createElement('button'); button.type = 'button'; button.textContent = label;
    button.addEventListener('click', () => {
      if (label === 'Remove') row.remove();
      else if (label === 'Move up' && row.previousElementSibling) row.previousElementSibling.before(row);
      else if (label === 'Move down' && row.nextElementSibling) row.nextElementSibling.after(row);
    });
    row.append(button);
  }
  return row;
}

function renderWorkflows(data) {
  workflowState = data;
  updateShowControls(data.show);
  const state = data.setlist;
  const position = document.getElementById('cue-position');
  if (position) {
    const current = state.songs[state.current]; const next = state.songs[state.next_index];
    position.textContent = `Current: ${state.interval ? 'Interval' : current ? current.title : 'Not started'}. Next: ${next ? next.title : 'End of set'}.`;
    document.getElementById('cue-notes').textContent = current
      ? `${current.notes} ${current.seconds ? `Expected duration: ${(current.seconds / 60).toFixed(1)} minutes.` : ''}` : '';
    document.getElementById('cue-elapsed').textContent = state.started_at
      ? `Set elapsed: ${Math.max(0, Math.floor((Date.now() - new Date(state.started_at).getTime()) / 60000))} minutes` : 'Set not started';
    document.getElementById('cue-message').textContent = state.message;
    document.getElementById('cue-resolution').hidden = !state.pending;
  }
  const editor = document.getElementById('setlist-editor');
  if (editor && editorRevision === null) {
    editorRevision = state.revision;
    editor.replaceChildren(...state.songs.map(songRow));
    if (!state.songs.length) editor.append(songRow());
  }
  const expected = document.getElementById('expected-inputs');
  if (expected) {
    const signature = JSON.stringify(data.inputs);
    if (signature !== expectedSignature) {
      expectedSignature = signature; expected.replaceChildren();
      for (const channel of data.inputs) {
        const label = document.createElement('label');
        const input = document.createElement('input'); input.type = 'checkbox'; input.value = channel.key;
        input.checked = data.soundcheck.expected_inputs.includes(channel.key);
        label.append(input, document.createTextNode(`${channel.key} (${channel.name})`)); expected.append(label);
      }
    }
    document.getElementById('soundcheck-scope').textContent = data.soundcheck_error || `Current destination: ${data.soundcheck.destination || 'unavailable'}. Expected inputs: ${data.soundcheck.expected_inputs.join('; ') || 'not selected'}.`;
    const results = document.getElementById('soundcheck-results'); results.replaceChildren();
    for (const name of ['disk', 'inputs', 'recording', 'playback', 'lights', 'stream']) {
      const result = data.soundcheck.results[name]; const row = document.createElement('p');
      row.textContent = result
        ? `${name}: ${data.soundcheck_error ? 'unverified' : result.state}, ${new Date(result.checked_at).toLocaleString()}. ${result.evidence}` : `${name}: not checked`;
      row.className = result && result.state === 'passed' && !data.soundcheck_error ? 'ok' : 'failed'; results.append(row);
    }
  }
  const options = document.getElementById('recovery-options');
  if (options) {
    const names = ['recs', 'lyte', 'streamo'].filter(name => !['connected', 'disabled'].includes(data.show[name].service.state));
    const signature = JSON.stringify(names);
    if (signature !== recoverySignature) {
      recoverySignature = signature; options.replaceChildren();
      for (const name of names) {
        const displayName = name === 'streamo' ? 'streamO' : name;
        const section = document.createElement('section');
        const explanation = document.createElement('p');
        explanation.textContent = `${displayName} is unavailable. Restarting ${displayName} interrupts ${name === 'recs' ? 'recording and playback; audio during the interruption is lost' : name === 'lyte' ? 'lighting output' : 'the live stream'}. Other services are not restarted. Unlock performance protection first.`;
        const label = document.createElement('label'); const confirm = document.createElement('input');
        confirm.type = 'checkbox'; confirm.dataset.confirmService = name;
        label.append(confirm, document.createTextNode(`I intend to interrupt ${displayName}`));
        const button = document.createElement('button'); button.type = 'button';
        button.dataset.workflowAction = 'recovery-restart'; button.dataset.service = name; button.textContent = `Restart ${displayName} only`;
        button.addEventListener('click', () => sendWorkflow(button));
        section.append(explanation, label, button); options.append(section);
      }
    }
    const recovery = data.recovery;
    document.getElementById('recovery-state').textContent = recovery.service
      ? `${recovery.service === 'streamo' ? 'streamO' : recovery.service}: ${recovery.outcome}. Original failure: ${recovery.original_failure}. ${recovery.message}`
      : 'No service recovery requested. Refresh and inspect current health first.';
  }
  document.querySelectorAll('[data-workflow-action]').forEach(button => {
    const action = button.dataset.workflowAction;
    const cue = action.startsWith('setlist-') && action !== 'setlist-save';
    const protectedAction = !cue && action !== 'recovery-refresh';
    button.disabled = workflowBusy || (protectedAction && data.show.performance_locked)
      || (cue && Boolean(state.pending) !== ['setlist-accept', 'setlist-retry'].includes(action));
    if (['setlist-next', 'setlist-skip'].includes(action) && state.next_index >= state.songs.length) button.disabled = true;
    if (action === 'setlist-repeat' && state.current < 0) button.disabled = true;
    if (action === 'recovery-restart' && data.recovery.outcome === 'checking') button.disabled = true;
    if (action.startsWith('soundcheck-') && data.soundcheck_error) button.disabled = true;
  });
  document.querySelectorAll('#setlist-editor input, #setlist-editor textarea, #setlist-editor button, #add-song, #reload-setlist')
    .forEach(control => {control.disabled = workflowBusy || data.show.performance_locked;});
}

async function refreshWorkflows() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch('/workflow-status', {cache: 'no-store', signal: controller.signal});
    if (!response.ok) throw new Error(`Workflow status unavailable: ${response.status}`);
    renderWorkflows(await response.json()); statusConnected();
  } catch (error) {
    workflowState = null;
    document.querySelectorAll('[data-workflow-action]').forEach(button => {button.disabled = true;});
    statusFailed(error);
    throw error;
  } finally {clearTimeout(timer);}
}

async function sendWorkflow(button) {
  if (workflowBusy || !workflowState || button.disabled) return;
  const data = workflowState; const action = button.dataset.workflowAction;
  if (action === lastWorkflowAction && Date.now() - lastWorkflowActionAt < 600) return;
  lastWorkflowAction = action; lastWorkflowActionAt = Date.now();
  const fields = {action, revision: action.startsWith('setlist-') ? data.setlist.revision : data.recovery.revision, scope: data.soundcheck.scope};
  if (action === 'setlist-save') {
    fields.revision = editorRevision;
    fields.songs = JSON.stringify(Array.from(document.querySelectorAll('[data-song-row]')).map(row => ({
      title: row.querySelector('[name=title]').value.trim(), notes: row.querySelector('[name=notes]').value,
      seconds: Math.round(Number(row.querySelector('[name=minutes]').value) * 60),
    })));
  }
  if (['setlist-accept', 'setlist-retry'].includes(action)) fields.confirmation = document.getElementById('confirm-cue-resolution').checked ? 'resolve' : '';
  if (action === 'soundcheck-begin') fields.expected = JSON.stringify(Array.from(document.querySelectorAll('#expected-inputs input:checked')).map(input => input.value));
  if (action === 'soundcheck-check' || action === 'soundcheck-skip') {
    fields.action = 'soundcheck-check';
    fields.step = action === 'soundcheck-skip' ? document.getElementById('skip-step').value : button.dataset.step;
    fields.skip = action === 'soundcheck-skip' ? 'yes' : '';
    fields.confirmation = document.getElementById('confirm-observation').checked ? 'observed' : '';
    fields.note = document.getElementById('check-note').value;
  }
  if (['soundcheck-start-recording', 'soundcheck-pause-recording', 'soundcheck-lights'].includes(action)) fields.confirmation = document.getElementById('confirm-output').checked ? 'output' : '';
  if (action === 'recovery-restart') {
    fields.service = button.dataset.service;
    fields.confirmation = document.querySelector(`[data-confirm-service="${fields.service}"]`).checked ? fields.service : '';
  }
  workflowBusy = true;
  document.querySelectorAll('#setlist-editor input, #setlist-editor textarea, #setlist-editor button, #add-song, #reload-setlist')
    .forEach(control => {control.disabled = true;});
  document.querySelectorAll('[data-workflow-action]').forEach(value => {value.disabled = true;});
  workflowResult('Working; do not submit this action again...');
  try {
    workflowResult(await showAction(fields));
    if (action === 'setlist-save') editorRevision = null;
  } catch (error) {workflowResult(error.message);}
  finally {
    workflowBusy = false;
    for (const id of ['confirm-cue-resolution', 'confirm-observation', 'confirm-output']) {
      const checkbox = document.getElementById(id); if (checkbox) checkbox.checked = false;
    }
    document.querySelectorAll('[data-confirm-service]').forEach(input => {input.checked = false;});
    await refreshWorkflows().catch(() => {});
  }
}

document.querySelectorAll('[data-workflow-action]').forEach(button => button.addEventListener('click', () => sendWorkflow(button)));
const addSong = document.getElementById('add-song');
if (addSong) addSong.addEventListener('click', () => document.getElementById('setlist-editor').append(songRow()));
const reloadSetlist = document.getElementById('reload-setlist');
if (reloadSetlist) reloadSetlist.addEventListener('click', () => {
  editorRevision = null;
  refreshWorkflows().catch(error => workflowResult(error.message));
});
function pollWorkflows() {refreshWorkflows().catch(() => {}).finally(() => setTimeout(pollWorkflows, 2000));}
pollWorkflows();
