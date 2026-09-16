let lastStatusReceived = null;

function requestStatus() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5000);
  return fetch('/status', {cache: 'no-store', signal: controller.signal})
    .then(response => {
      if (!response.ok) throw new Error(`status request failed: ${response.status}`);
      return response.json();
    })
    .then(status => {
      if (typeof updateShowControls === 'function') updateShowControls(status);
      return status;
    })
    .finally(() => clearTimeout(timer));
}

function statusConnected() {
  lastStatusReceived = new Date();
  const indicator = document.getElementById('connection-status');
  if (indicator) {
    indicator.className = 'ok';
    indicator.textContent = `Connected; updated ${lastStatusReceived.toLocaleTimeString()}`;
  }
}

function statusFailed(error) {
  if (typeof showControlsUnavailable === 'function') showControlsUnavailable();
  const indicator = document.getElementById('connection-status');
  if (indicator) {
    indicator.className = 'failed';
    const last = lastStatusReceived
      ? `${lastStatusReceived.toLocaleTimeString()} (${Math.floor((Date.now() - lastStatusReceived.getTime()) / 1000)}s ago)` : 'never';
    indicator.textContent = `Status unavailable: ${error.message}. Last update: ${last}. Displayed values may be stale.`;
  }
  const readiness = document.getElementById('readiness-state');
  if (readiness) readiness.textContent = 'unknown (status unavailable)';
}
