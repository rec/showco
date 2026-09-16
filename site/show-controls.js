function showControlsUnavailable() {
  const lock = document.getElementById('performance-lock-state');
  if (lock) lock.textContent = 'Performance lock: unknown (status unavailable)';
  const banner = document.getElementById('fault-banner');
  if (banner) {
    banner.className = 'failed';
    banner.textContent = 'Live state unavailable. Reconnect before relying on these controls.';
  }
  document.querySelectorAll('[data-performance-action], #performance-lock-form button')
    .forEach(button => {button.disabled = true;});
}

async function showAction(fields) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch('/actions', {
      method: 'POST', headers: {'Accept': 'application/json'},
      body: new URLSearchParams(fields), signal: controller.signal,
    });
    if (!response.ok) throw new Error(`Action request failed: ${response.status}`);
    const result = await response.json();
    if (!result.ok) throw new Error(result.message);
    return result.message;
  } catch (error) {
    if (error.name === 'AbortError') {
      throw new Error('Action response timed out; outcome unknown. Check state before submitting again.');
    }
    if (error instanceof TypeError) {
      throw new Error('Connection lost; action outcome unknown. Check state before submitting again.');
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

function updateShowControls(status) {
  const lock = document.getElementById('performance-lock-state');
  if (lock) lock.textContent = status.performance_locked
    ? 'Performance in progress: protected actions locked' : 'Performance lock: off';
  document.querySelectorAll('#performance-lock-form button').forEach(button => {
    button.disabled = button.value === (status.performance_locked ? 'performance-lock' : 'performance-unlock');
  });
  const error = document.getElementById('monitoring-error');
  if (error) error.textContent = status.monitoring_error || '';
  const banner = document.getElementById('fault-banner');
  if (!banner) return;
  const faults = status.active_faults;
  banner.className = faults.length ? 'failed' : 'ok';
  banner.replaceChildren();
  if (!faults.length) {
    banner.textContent = 'No active observed faults. Physical output still needs confirmation.';
    return;
  }
  // Server order keeps service and disk failures ahead of audio-write observations.
  const fault = faults.find(value => !value.acknowledged) || faults[0];
  const detail = document.createElement('p');
  detail.textContent = `${fault.name}: ${fault.message}. Since ${new Date(fault.started_at).toLocaleString()}. ${fault.next_action}`;
  const summary = document.createElement('p');
  summary.textContent = `${faults.length} active fault(s). ${fault.acknowledged ? 'Acknowledged; still unresolved.' : ''}`;
  const link = document.createElement('a');
  link.href = '/health';
  link.textContent = 'Inspect health and history';
  banner.append(detail, summary, link);
  if (!fault.acknowledged) {
    const button = document.createElement('button');
    button.textContent = 'Acknowledge';
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        await showAction({action: 'acknowledge-fault', name: fault.name, started_at: fault.started_at});
        updateShowControls(await requestStatus());
      } catch (error) {summary.textContent = error.message; button.disabled = false;}
    });
    banner.append(button);
  }
}

const lockForm = document.getElementById('performance-lock-form');
if (lockForm) lockForm.addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.submitter;
  if (!button || button.disabled) return;
  button.disabled = true;
  const result = document.getElementById('lock-result');
  try {
    result.textContent = await showAction({
      action: button.value,
      confirmation: lockForm.querySelector('[name=confirmation]').checked ? 'unlock' : '',
    });
    lockForm.querySelector('[name=confirmation]').checked = false;
    updateShowControls(await requestStatus());
  } catch (error) {result.textContent = error.message; button.disabled = false;}
});

function pollShowControls() {
  requestStatus().then(statusConnected).catch(statusFailed)
    .finally(() => setTimeout(pollShowControls, 1000));
}

showControlsUnavailable();
