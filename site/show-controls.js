function showControlsUnavailable() {
  const coverage = document.getElementById('recording-coverage');
  if (coverage) {
    coverage.textContent = 'Input recording status unavailable';
    coverage.className = 'failed';
  }
  const inputs = document.getElementById('recording-inputs');
  if (inputs) inputs.textContent = 'No current input evidence.';
  const destination = document.getElementById('recording-destination');
  if (destination) {
    destination.className = 'failed';
    destination.textContent = 'Recording destination: unavailable';
  }
  const lock = document.getElementById('performance-lock-state');
  if (lock) lock.textContent = 'Performance lock: unknown (status unavailable)';
  const banner = document.getElementById('fault-banner');
  if (banner) {
    delete banner.dataset.faults;
    banner.className = 'failed';
    banner.textContent = 'Live state unavailable. Reconnect before relying on these controls.';
  }
  document.querySelectorAll('[data-performance-action], #performance-lock-form button')
    .forEach(button => {
      if (button.dataset.performanceAction !== 'streamo-stop') button.disabled = true;
    });
  document.querySelectorAll('[data-disabled-when="streamo_unavailable"] button')
    .forEach(button => {
      if (button.hasAttribute('aria-busy')) return;
      const form = button.closest('form');
      const canStop = form.dataset.twitchStopAvailable === 'true';
      button.disabled = !canStop;
      button.textContent = canStop ? 'Stop Twitch (status unavailable)' : 'Twitch status unavailable';
      if (canStop) form.querySelector('input[name="action"]').value = 'streamo-stop';
    });
  document.querySelectorAll('form[data-action="recs-pause-recording"] button, form[data-action="recs-resume-recording"] button')
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
      const uncertain = new Error('Action response timed out; outcome unknown. Check state before submitting again.');
      uncertain.outcomeUnknown = true;
      throw uncertain;
    }
    if (error instanceof TypeError) {
      const uncertain = new Error('Connection lost; action outcome unknown. Check state before submitting again.');
      uncertain.outcomeUnknown = true;
      throw uncertain;
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
  const signature = JSON.stringify(faults);
  if (banner.dataset.faults === signature) return;
  banner.dataset.faults = signature;
  banner.className = faults.length ? 'failed' : 'ok';
  banner.replaceChildren();
  if (!faults.length) {
    banner.textContent = 'No active observed faults. Physical output still needs confirmation.';
    return;
  }
  const list = document.createElement('ul');
  for (const fault of faults) {
    const item = document.createElement('li');
    const detail = document.createElement('p');
    detail.textContent = `${fault.name}: ${fault.message}. Since ${new Date(fault.started_at).toLocaleString()}. ${fault.next_action}`;
    item.append(detail);
    if (fault.acknowledged) {
      const notice = document.createElement('span');
      notice.textContent = 'Acknowledged; still unresolved.';
      item.append(notice);
    } else {
      const button = document.createElement('button');
      const result = document.createElement('span');
      result.setAttribute('role', 'status');
      button.textContent = 'Acknowledge';
      button.addEventListener('click', async () => {
        button.disabled = true;
        try {
          await showAction({action: 'acknowledge-fault', name: fault.name, started_at: fault.started_at});
          await requestStatus();
        } catch (error) {result.textContent = error.message; button.disabled = false;}
      });
      item.append(button, result);
    }
    list.append(item);
  }
  const link = document.createElement('a');
  link.href = '/health';
  link.textContent = 'Inspect health and history';
  banner.append(list, link);
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
