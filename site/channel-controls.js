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
    const submittedName = input.value;
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
        track_name: submittedName,
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
        form.dataset.savedTrackName = submittedName;
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
