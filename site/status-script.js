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

  function sourceValue(status, path) {
    return path.split(".").slice(1).reduce((value, field) => value[field], status);
  }

  function statusText(format, value) {
    switch (format) {
      case "readiness": return value ? "ready" : "not ready";
      case "service": return serviceDetail(value);
      case "snapshot": return value || "connected";
      case "progress": return value;
      case "lyte": return lyteDetail(value);
      case "temperature": return value.temperature_c === null
        ? value.temperature_error || "unknown"
        : `${value.temperature_c.toFixed(1)} °C`;
      case "bitrate": return value.output_bitrate_kbps === null
        ? "unknown"
        : `${value.output_bitrate_kbps.toFixed(0)} kbps`;
    }
    throw new Error(`unknown status format: ${format}`);
  }

  function updateConfiguredStatus(status) {
    for (const element of document.querySelectorAll("p[data-value]")) {
      const value = sourceValue(status, element.dataset.value);
      const label = element.dataset.label;
      element.textContent = `${label ? `${label}: ` : ""}${statusText(element.dataset.format, value)}`;
      if (element.dataset.format === "progress") {
        element.className = status.recording_progress.ok ? "ok" : "failed";
      }
    }
    for (const section of document.querySelectorAll("section.readiness")) {
      section.className = `readiness ${status.readiness.ready ? "healthy" : "error"}`;
    }
    for (const card of document.querySelectorAll("[data-service]")) {
      const service = sourceValue(status, card.dataset.service);
      card.className = `card ${service.state}`;
      card.querySelector(".state").textContent = service.state;
      card.querySelector(".service-detail").textContent = card.dataset.format === "recording"
        ? recordingText(status.recs) : streamingText(status.streamo);
    }
  }

  function atBottom() {
    return window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2;
  }

  function scrollToBottom() {
    window.scrollTo(0, document.documentElement.scrollHeight);
  }

  function oscRecorderDetail(recorder) {
    if (recorder.last_error) return `${recorder.state}: ${recorder.last_error}`;
    return recorder.log_path === null
      ? recorder.state
      : `${recorder.state}: ${recorder.log_path} (${recorder.log_size} bytes)`;
  }

  function updateConfiguredLists(status) {
    for (const container of document.querySelectorAll('[data-layout="list"]')) {
      const source = container.dataset.source;
      const items = sourceValue(status, source);
      const limit = Number(container.dataset.limit);
      const visible = limit ? items.slice(-limit) : items;
      const follow = source === "show.recs.errors" && atBottom();
      if (!visible.length) {
        const message = document.createElement("p");
        message.textContent = container.dataset.emptyText;
        container.replaceChildren(message);
        continue;
      }
      const list = document.createElement("ul");
      const template = document.getElementById(container.dataset.template);
      for (const item of visible) {
        const row = template.content.firstElementChild.cloneNode(true);
        if (source === "show.readiness.checks" || source === "show.input_checks") {
          row.className = item.ok ? "ok" : "failed";
        }
        for (const field of row.querySelectorAll("[data-value]")) {
          const value = field.dataset.value === "item"
            ? item : item[field.dataset.value.slice("item.".length)];
          switch (field.dataset.format) {
            case "time": field.textContent = new Date(value).toLocaleTimeString(); break;
            case "mixer_detail": field.textContent = mixerDetail(value); break;
            case "osc_detail": field.textContent = oscRecorderDetail(value); break;
            default: field.textContent = value;
          }
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
    for (const row of document.querySelectorAll(`[data-meter="${identifier}"]`)) {
      const meter = row.querySelector("meter");
      const value = row.querySelector("span");
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

  function updateStatus() {
    return requestStatus()
      .then(status => {
        updateConfiguredStatus(status);
        updateChannels(status.recs.channels);
        updateConfiguredLists(status);
        updatePerformance(status);
        statusConnected();
      })
      .catch(statusFailed);
  }

  function pollStatus() {
    updateStatus().then(() => setTimeout(pollStatus, 1000));
  }

  if (document.querySelectorAll('[data-source="show.recs.errors"]').length) {
    requestAnimationFrame(scrollToBottom);
  }
  pollStatus();
