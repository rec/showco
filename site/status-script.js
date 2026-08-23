  function serviceDetail(service) {
    return service.last_error
      ? `${service.state}: ${service.last_error}`
      : service.state;
  }

  function recordingText(recs) {
    if (!recs.recording) return "stopped";
    const seconds = recs.elapsed_seconds;
    if (seconds === null) return "recording for unknown time, ? files";
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    const duration = hours
      ? `${hours}:${String(minutes % 60).padStart(2, "0")}:${String(
          Math.floor(seconds % 60),
        ).padStart(2, "0")}`
      : `${minutes}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
    return `recording for ${duration}, ${recs.file_count ?? "?"} files`;
  }

  function streamingText(twitcho) {
    return `${twitcho.stream_state}${twitcho.muted ? ", muted" : ""}`;
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
    form.append(label, stereo);
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
    const names = new Map(
      [...container.querySelectorAll(".level")].map(form => [
        `${form.dataset.device}\\u0000${form.dataset.channel}`,
        {
          trackName: form.querySelector("[name=track_name]").value,
          savedTrackName: form.dataset.savedTrackName,
        },
      ]),
    );
    container.replaceChildren(...channels.map(channel => {
      const name = names.get(trackKey(channel));
      return channelForm(
        channel,
        name?.trackName ?? channel.name,
        name?.savedTrackName ?? channel.name,
        channels,
      );
    }));
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
      updateService(
        "streaming", status.twitcho.service, streamingText(status.twitcho),
        "twitcho-health",
      );
      updateChannels(status.recs.channels);
      updateRecsErrors(status.recs.errors);
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
      const x18Recorder = document.getElementById("x18-recorder");
      if (x18Recorder) {
        x18Recorder.textContent = status.x18.last_error || status.x18.log_path === null
          ? status.x18.last_error || status.x18.state
          : `${status.x18.state}: ${status.x18.log_path} (${status.x18.log_size} bytes)`;
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

  if (document.getElementById("recs-errors")) {
    requestAnimationFrame(scrollToBottom);
  }
  pollStatus();
