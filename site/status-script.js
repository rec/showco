  function serviceDetail(service) {
    return service.last_error
      ? `${service.state}: ${service.last_error}`
      : service.state;
  }

  function recordingText(recs) {
    if (!recs.recording) return "stopped";
    const seconds = recs.elapsed_seconds;
    if (seconds === null) return recs.paused
      ? "paused after unknown time, ? files"
      : "recording for unknown time, ? files";
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    const duration = hours
      ? `${hours}:${String(minutes % 60).padStart(2, "0")}:${String(
          Math.floor(seconds % 60),
        ).padStart(2, "0")}`
      : `${minutes}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
    return `${recs.paused ? "paused after" : "recording for"} ${duration}, ${
      recs.file_count ?? "?"
    } files`;
  }

  function streamingText(streamo) {
    return `${streamo.stream_state}${streamo.muted ? ", muted" : ""}`;
  }

  function lyteDetail(lyte) {
    if (lyte.service.last_error) {
      return `${lyte.service.state}: ${lyte.service.last_error}`;
    }
    if (lyte.service.state === "disabled") return "disabled";
    const details = [lyte.running ? 'running' : 'stopped'];
    if (lyte.active_animation) details.push(`animation ${lyte.active_animation}`);
    if (lyte.active_test) details.push("test active");
    else if (lyte.queued_test) details.push("test queued");
    for (const [name, string] of Object.entries(lyte.strings)) {
      details.push(`${name}: ${string.state}, ${string.frame_count} frames${
        string.last_error ? `: ${string.last_error}` : ''}`);
    }
    return details.join(", ");
  }

  function mixerDetail(mixer) {
    if (mixer.error) return `${mixer.state}: ${mixer.error}`;
    const missing = [];
    if (mixer.audio_ready === false) missing.push("USB audio");
    if (mixer.midi_ready === false) missing.push("MIDI");
    const detail = missing.length
      ? `${mixer.state} for ${missing.join(" and ")}`
      : mixer.state;
    return mixer.latency_ms === null
      ? detail
      : `${detail}: ${mixer.latency_ms.toFixed(1)} ms`;
  }

  function updateService(identifier, service, detail, healthIdentifier) {
    const card = document.getElementById(`${identifier}-card`);
    const state = document.getElementById(`${identifier}-state`);
    const detailElement = document.getElementById(`${identifier}-detail`);
    const health = document.getElementById(healthIdentifier);
    if (card) card.className = `card ${service.state}`;
    if (state) state.textContent = service.state;
    if (detailElement) detailElement.textContent = detail;
    if (health) {
      health.textContent = `${healthIdentifier.replace("-health", "")}: ${
        serviceDetail(service)
      }`;
    }
  }

  function atBottom() {
    return window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2;
  }

  function scrollToBottom() {
    window.scrollTo(0, document.documentElement.scrollHeight);
  }

  function updateRecsErrors(errors) {
    const containers = document.querySelectorAll('[data-source="show.recs.errors"]');
    const legacy = document.getElementById("recs-errors");
    for (const container of containers.length ? containers : legacy ? [legacy] : []) {
      const follow = atBottom();
      const limit = Number(container.dataset.limit);
      const visible = limit ? errors.slice(-limit) : errors;
      if (!visible.length) {
        const message = document.createElement("p");
        message.textContent = container.dataset.emptyText || "No errors";
        container.replaceChildren(message);
        continue;
      }
      const list = document.createElement("ul");
      for (const error of visible) {
        const row = container.dataset.template
          ? document.getElementById(container.dataset.template).content.firstElementChild.cloneNode(true)
          : document.createElement("li");
        if (container.dataset.template) {
          for (const field of row.querySelectorAll("[data-value]")) {
            const value = error[field.dataset.value.slice("item.".length)];
            field.textContent = field.dataset.format === "time"
              ? new Date(value).toLocaleTimeString() : value;
          }
        } else {
          const timestamp = document.createElement("time");
          timestamp.className = "error-time";
          timestamp.textContent = new Date(error.timestamp).toLocaleTimeString();
          const message = document.createElement("span");
          message.textContent = error.message;
          row.append(timestamp, message);
        }
        list.append(row);
      }
      container.replaceChildren(list);
      if (follow) requestAnimationFrame(scrollToBottom);
    }
  }

  function byteSize(value) {
    if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GiB`;
    if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
    return `${(value / 1024).toFixed(1)} KiB`;
  }

  function duration(seconds) {
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    return hours
      ? `${hours}:${String(minutes % 60).padStart(2, "0")}:${String(
          Math.floor(seconds % 60),
        ).padStart(2, "0")}`
      : `${minutes}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
  }

  function setPerformance(identifier, percent, text, forceCritical = false) {
    const row = document.getElementById(`${identifier}-performance`);
    const meter = document.getElementById(`${identifier}-meter`);
    const value = document.getElementById(`${identifier}-value`);
    if (!row || !meter || !value) return;
    if (percent === null) meter.removeAttribute("value");
    else meter.value = percent;
    row.className = `performance-row ${
      forceCritical || percent !== null && percent >= 95
        ? "critical"
        : percent !== null && percent >= 85
          ? "warning"
          : "normal"
    }`;
    value.textContent = text;
  }

  function updatePerformance(status) {
    const system = status.system;
    setPerformance(
      "cpu",
      system.cpu_percent,
      system.cpu_percent === null
        ? system.cpu_error || "unknown"
        : `${system.cpu_percent.toFixed(0)}%`,
    );
    const memoryPercent = system.memory_used_bytes === null
      || system.memory_total_bytes === null
      || system.memory_total_bytes <= 0
      ? null
      : 100 * system.memory_used_bytes / system.memory_total_bytes;
    setPerformance(
      "memory",
      memoryPercent,
      memoryPercent === null
        ? system.memory_error || "unknown"
        : `${byteSize(system.memory_used_bytes)} / ${
            byteSize(system.memory_total_bytes)
          } (${memoryPercent.toFixed(0)}%)`,
    );
    const disk = status.recs.disk;
    if (disk === null) {
      setPerformance(
        "disk",
        null,
        status.recs.disk_error
          || status.recs.snapshot_error
          || "recording disk unavailable",
      );
      return;
    }
    const diskPercent = 100 * disk.used_bytes / disk.total_bytes;
    let diskText = `${disk.path}: ${byteSize(disk.free_bytes)} free / ${
      byteSize(disk.total_bytes)
    } (${diskPercent.toFixed(0)}% used)`;
    if (disk.estimated_seconds_remaining !== null) {
      diskText += `, ${duration(disk.estimated_seconds_remaining)} remaining`;
    }
    if (disk.paused_for_disk_space) diskText = `paused: ${diskText}`;
    else if (disk.alert_active) diskText = `alert: ${diskText}`;
    const stale = status.recs.disk_error || status.recs.snapshot_error;
    if (stale) diskText += `, stale: ${stale}`;
    setPerformance(
      "disk",
      diskPercent,
      diskText,
      disk.alert_active || disk.paused_for_disk_space,
    );
  }

  function updateReadiness(readiness) {
    const state = document.getElementById("readiness-state");
    const checks = document.getElementById("readiness-checks");
    if (!state || !checks) return;
    state.textContent = readiness.ready ? "ready" : "not ready";
    checks.replaceChildren(...readiness.checks.map(check => {
      const item = document.createElement("li");
      item.className = check.ok ? "ok" : "failed";
      const name = document.createElement("b");
      name.textContent = check.name;
      item.append(name, `: ${check.message}`);
      return item;
    }));
  }

  function updateIncidents(incidents) {
    const container = document.getElementById("incidents");
    if (!container) return;
    if (!incidents.length) {
      container.textContent = "No incidents.";
      return;
    }
    const list = document.createElement("ul");
    for (const incident of incidents) {
      const item = document.createElement("li");
      item.textContent = `${new Date(incident.timestamp).toLocaleTimeString()} ${incident.message}`;
      list.append(item);
    }
    container.replaceChildren(list);
  }

  function updateInputChecks(checks) {
    const container = document.getElementById("input-checks");
    if (!container) return;
    if (!checks.length) {
      container.textContent = "No recording inputs.";
      return;
    }
    const list = document.createElement("ul");
    for (const check of checks) {
      const item = document.createElement("li");
      item.className = check.ok ? "ok" : "failed";
      item.textContent = `${check.name}: ${check.message}`;
      list.append(item);
    }
    container.replaceChildren(list);
  }

  function updateStatus() {
    return requestStatus()
      .then(status => {
      updateService(
        "recording", status.recs.service, recordingText(status.recs), "recs-health",
      );
      const recsSnapshot = document.getElementById("recs-snapshot");
      if (recsSnapshot) {
        recsSnapshot.textContent = `recs snapshot: ${status.recs.snapshot_error || "connected"}`;
      }
      const recordingProgress = document.getElementById("recording-progress");
      if (recordingProgress) {
        recordingProgress.className = status.recording_progress.ok ? "ok" : "failed";
        recordingProgress.textContent = `recording progress: ${status.recording_progress.message}`;
      }
      updateService(
        "streaming", status.streamo.service, streamingText(status.streamo),
        "streamo-health",
      );
      const lyteHealth = document.getElementById("lyte-health");
      if (lyteHealth) lyteHealth.textContent = `lyte: ${lyteDetail(status.lyte)}`;
      updateChannels(status.recs.channels);
      updateRecsErrors(status.recs.errors);
      updatePerformance(status);
      updateReadiness(status.readiness);
      updateIncidents(status.incidents);
      updateInputChecks(status.input_checks);
      const temperature = document.getElementById("temperature");
      if (temperature) {
        temperature.textContent = status.system.temperature_c === null
          ? status.system.temperature_error || "unknown"
          : `${status.system.temperature_c.toFixed(1)} °C`;
      }
      const bitrate = document.getElementById("bitrate");
      if (bitrate) {
        bitrate.textContent = status.streamo.output_bitrate_kbps === null
          ? "unknown"
          : `${status.streamo.output_bitrate_kbps.toFixed(0)} kbps`;
      }
      const mixers = document.getElementById("mixers");
      if (mixers) {
        mixers.replaceChildren(...status.mixers.map(mixer => {
          const row = document.createElement("p");
          row.textContent = `${mixer.name}: ${mixerDetail(mixer)}`;
          return row;
        }));
      }
      const oscRecorders = document.getElementById("osc-recorders");
      if (oscRecorders) {
        oscRecorders.replaceChildren(...status.recs.osc.map(recorder => {
          const row = document.createElement("p");
          const detail = recorder.last_error
            ? `${recorder.state}: ${recorder.last_error}`
            : recorder.log_path === null
              ? recorder.state
              : `${recorder.state}: ${recorder.log_path} (${recorder.log_size} bytes)`;
          row.textContent = `${recorder.name} OSC recorder: ${detail}`;
          return row;
        }));
        if (!status.recs.osc.length) oscRecorders.textContent = "No OSC recorders.";
      }
      statusConnected();
      })
      .catch(statusFailed);
  }

  function pollStatus() {
    updateStatus().then(() => setTimeout(pollStatus, 1000));
  }

  if (document.getElementById("recs-errors")) {
    requestAnimationFrame(scrollToBottom);
  }
  pollStatus();
