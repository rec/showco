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

  function streamingText(twitcho) {
    return `${twitcho.stream_state}${twitcho.muted ? ", muted" : ""}`;
  }

  function lyteDetail(lyte) {
    if (lyte.service.last_error) {
      return `${lyte.service.state}: ${lyte.service.last_error}`;
    }
    if (lyte.service.state === "disabled") return "disabled";
    const details = [`${lyte.daemon_state}, ${lyte.output_state}`];
    if (lyte.host) details.push(lyte.host);
    if (lyte.active_test) details.push("test active");
    else if (lyte.queued_test) details.push("test queued");
    if (lyte.frame_send_count !== null) details.push(`${lyte.frame_send_count} frames`);
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

  function trackKey(channel) {
    return `${channel.device}\\u0000${channel.channels.join(",")}`;
  }

  function revertTrackName(form) {
    const input = form.querySelector("[name=track_name]");
    input.value = form.dataset.savedTrackName;
    input.setCustomValidity("");
  }

  function saveTrackName(form) {
    const input = form.querySelector("[name=track_name]");
    if (input.value === form.dataset.savedTrackName) return Promise.resolve();
    input.setCustomValidity("");
    return fetch("/actions", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: new URLSearchParams({
        action: "recs-track-name",
        device: form.dataset.device,
        channel: form.dataset.channel,
        track_name: input.value,
      }),
    })
      .then(response => {
        if (!response.ok) {
          throw new Error(`track name request failed: ${response.status}`);
        }
        return response.json();
      })
      .then(result => {
        if (!result.ok) throw new Error(result.message);
        form.dataset.savedTrackName = input.value;
      })
      .catch(error => {
        input.setCustomValidity(error.message);
        input.reportValidity();
      });
  }

  function channelForms() {
    return [...document.querySelectorAll("#channels .level")];
  }

  function saveTrackNames() {
    let saved = Promise.resolve();
    for (const form of channelForms()) {
      saved = saved.then(() => saveTrackName(form));
    }
    return saved;
  }

  function saveStereo(event) {
    const input = event.currentTarget;
    const form = input.closest(".level");
    input.setCustomValidity("");
    input.disabled = true;
    fetch("/actions", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: new URLSearchParams({
        action: "recs-set-stereo",
        device: form.dataset.device,
        channels: form.dataset.channels,
      }),
    })
      .then(response => {
        if (!response.ok) {
          throw new Error(`stereo request failed: ${response.status}`);
        }
        return response.json();
      })
      .then(result => {
        if (!result.ok) throw new Error(result.message);
        return updateStatus();
      })
      .catch(error => {
        input.checked = !input.checked;
        input.disabled = false;
        input.setCustomValidity(error.message);
        input.reportValidity();
      });
  }

  function calibrateChannel(event) {
    const button = event.currentTarget;
    const form = button.closest(".level");
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.textContent = "Calibrating...";
    button.title = "";
    fetch("/actions", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: new URLSearchParams({
        action: "recs-calibrate",
        device: form.dataset.device,
        channels: form.dataset.channels,
      }),
    })
      .then(response => {
        if (!response.ok) {
          throw new Error(`calibration request failed: ${response.status}`);
        }
        return response.json();
      })
      .then(result => {
        if (!result.ok) throw new Error(result.message);
        button.textContent = "Calibrated";
        return updateStatus();
      })
      .catch(error => {
        button.textContent = "Calibration failed";
        button.title = error.message;
      })
      .finally(() => {
        button.disabled = false;
        button.removeAttribute("aria-busy");
      });
  }

  function revertTrackNames() {
    for (const form of channelForms()) revertTrackName(form);
  }

  function mutableAttributeValue(input) {
    if (input.dataset.valueType === "boolean") return input.checked;
    if (input.dataset.valueType === "number") return Number(input.value);
    if (input.dataset.valueType === "json") return JSON.parse(input.value);
    return input.value;
  }

  function saveMutableAttribute(event) {
    const input = event.currentTarget;
    const attribute = input.closest(".mutable-attribute");
    input.setCustomValidity("");
    let value;
    try {
      value = mutableAttributeValue(input);
    } catch (error) {
      input.setCustomValidity(error.message);
      input.reportValidity();
      return;
    }
    const savedValue = JSON.stringify(value);
    if (savedValue === attribute.dataset.savedValue) return;
    fetch("/actions", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: new URLSearchParams({
        action: "recs-set-attr",
        address: attribute.dataset.address,
        value: savedValue,
      }),
    })
      .then(response => {
        if (!response.ok) {
          throw new Error(`attribute request failed: ${response.status}`);
        }
        return response.json();
      })
      .then(result => {
        if (!result.ok) throw new Error(result.message);
        attribute.dataset.savedValue = savedValue;
      })
      .catch(error => {
        input.setCustomValidity(error.message);
        input.reportValidity();
      });
  }

  function channelForm(channel, trackName, savedTrackName, channels) {
    const form = document.createElement("div");
    form.className = `level ${channel.state}`;
    form.dataset.device = channel.device;
    form.dataset.channel = channel.name;
    form.dataset.channels = channel.channels.join(",");
    form.dataset.savedTrackName = savedTrackName;
    const label = document.createElement("label");
    const caption = document.createElement("span");
    caption.className = "channel-caption";
    const title = document.createElement("b");
    title.textContent = channel.name;
    const input = document.createElement("input");
    input.name = "track_name";
    input.value = trackName;
    label.append(title, input);
    const state = document.createElement("span");
    state.className = `channel-state ${
      channel.on ? "indicator-red" : "indicator-green"
    }`;
    const recordingState = channel.on ? "recording" : "not recording";
    state.setAttribute("aria-label", recordingState);
    state.title = recordingState;
    state.textContent = "•";
    caption.append(state, title);
    label.append(caption, input);
    const stereo = document.createElement("label");
    stereo.className = "stereo";
    const stereoInput = document.createElement("input");
    stereoInput.type = "checkbox";
    stereoInput.checked = channel.channels.length === 2;
    stereoInput.disabled = !stereoEnabled(channel, channels);
    stereoInput.addEventListener("change", saveStereo);
    stereo.append(stereoInput, "Stereo");
    const waveform = document.createElement("canvas");
    waveform.className = "waveform";
    waveform.setAttribute("aria-label", "Live waveform");
    const calibrate = document.createElement("button");
    calibrate.className = "calibrate-channel";
    calibrate.type = "button";
    calibrate.textContent = "Calibrate";
    calibrate.addEventListener("click", calibrateChannel);
    form.append(label, stereo, waveform, calibrate);
    return form;
  }

  function stereoEnabled(channel, channels) {
    return channel.channels.length === 2 || channels.some(other =>
      other.device === channel.device
      && other.channels.length === 1
      && channel.channels.length === 1
      && other.channels[0] === channel.channels[0] + 1,
    );
  }

  function updateChannels(channels) {
    const container = document.getElementById("channels");
    if (!container) return;
    if (document.activeElement.closest("#channels .level")) return;
    const forms = new Map(
      [...container.querySelectorAll(".level")].map(form => [trackKey({
        device: form.dataset.device,
        channels: form.dataset.channels.split(",").map(Number),
      }), form]),
    );
    container.replaceChildren(...channels.map(channel => {
      const form = forms.get(trackKey(channel));
      if (!form) return channelForm(channel, channel.name, channel.name, channels);
      updateChannelForm(form, channel, channels);
      return form;
    }));
  }

  function updateChannelForm(form, channel, channels) {
    form.className = `level ${channel.state}`;
    form.dataset.device = channel.device;
    form.dataset.channel = channel.name;
    form.dataset.channels = channel.channels.join(",");
    const state = form.querySelector(".channel-state");
    state.className = `channel-state ${channel.on ? "indicator-red" : "indicator-green"}`;
    state.setAttribute("aria-label", channel.on ? "recording" : "not recording");
    state.title = state.getAttribute("aria-label");
    const stereo = form.querySelector(".stereo input");
    stereo.checked = channel.channels.length === 2;
    stereo.disabled = !stereoEnabled(channel, channels);
  }

  function atBottom() {
    return window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2;
  }

  function scrollToBottom() {
    window.scrollTo(0, document.documentElement.scrollHeight);
  }

  function updateRecsErrors(errors) {
    const container = document.getElementById("recs-errors");
    if (!container) return;
    const follow = atBottom();
    container.replaceChildren();
    const errorsToShow = errors.slice(-Number(container.dataset.limit));
    if (!errorsToShow.length) {
      const noErrors = document.createElement("p");
      noErrors.textContent = "No errors";
      container.append(noErrors);
      return;
    }
    const list = document.createElement("ul");
    for (const error of errorsToShow) {
      const item = document.createElement("li");
      const timestamp = document.createElement("time");
      timestamp.className = "error-time";
      timestamp.textContent = new Date(error.timestamp).toLocaleTimeString();
      const message = document.createElement("span");
      message.textContent = error.message;
      item.append(timestamp, message);
      list.append(item);
    }
    container.append(list);
    if (follow) requestAnimationFrame(scrollToBottom);
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

  function updateStatus() {
    return fetch("/status", { cache: "no-store" })
      .then(response => {
      if (!response.ok) throw new Error(`status request failed: ${response.status}`);
        return response.json();
      })
      .then(status => {
      updateService(
        "recording", status.recs.service, recordingText(status.recs), "recs-health",
      );
      const recsSnapshot = document.getElementById("recs-snapshot");
      if (recsSnapshot) {
        recsSnapshot.textContent = `recs snapshot: ${status.recs.snapshot_error || "connected"}`;
      }
      updateService(
        "streaming", status.twitcho.service, streamingText(status.twitcho),
        "twitcho-health",
      );
      const lyteHealth = document.getElementById("lyte-health");
      if (lyteHealth) lyteHealth.textContent = `lyte: ${lyteDetail(status.lyte)}`;
      updateChannels(status.recs.channels);
      updateRecsErrors(status.recs.errors);
      updatePerformance(status);
      updateReadiness(status.readiness);
      updateIncidents(status.incidents);
      const temperature = document.getElementById("temperature");
      if (temperature) {
        temperature.textContent = status.system.temperature_c === null
          ? status.system.temperature_error || "unknown"
          : `${status.system.temperature_c.toFixed(1)} °C`;
      }
      const bitrate = document.getElementById("bitrate");
      if (bitrate) {
        bitrate.textContent = status.twitcho.output_bitrate_kbps === null
          ? "unknown"
          : `${status.twitcho.output_bitrate_kbps.toFixed(0)} kbps`;
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
      })
      .catch(() => {});
  }

  function pollStatus() {
    updateStatus().then(() => setTimeout(pollStatus, 1000));
  }

  const saveTrackNamesButton = document.getElementById("save-track-names");
  if (saveTrackNamesButton) {
    saveTrackNamesButton.addEventListener("click", saveTrackNames);
  }
  const revertTrackNamesButton = document.getElementById("revert-track-names");
  if (revertTrackNamesButton) {
    revertTrackNamesButton.addEventListener("click", revertTrackNames);
  }
  for (const input of document.querySelectorAll("#mutable-attributes input")) {
    input.addEventListener("blur", saveMutableAttribute);
  }
  for (const button of document.querySelectorAll(".calibrate-channel")) {
    button.addEventListener("click", calibrateChannel);
  }

  if (document.getElementById("recs-errors")) {
    requestAnimationFrame(scrollToBottom);
  }
  pollStatus();
