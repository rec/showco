  function trackKey(channel) {
    return `${channel.device}\\u0000${channel.channels.join(",")}`;
  }

  function revertTrackName(form) {
    const input = form.querySelector('[data-action="recs-track-name"]');
    if (!input) return;
    input.value = form.dataset.savedTrackName;
    input.setCustomValidity("");
  }

  function channelAction(fields) {
    return showAction(fields).catch(error => {
      if (!error.outcomeUnknown) throw error;
      return requestStatus()
        .then(status => {
          updateChannels(status.recs.channels);
          statusConnected();
          error.observedStatus = status;
        })
        .catch(statusFailed)
        .then(() => {throw error;});
    });
  }

  function saveTrackName(form) {
    const input = form.querySelector('[data-action="recs-track-name"]');
    if (!input) return Promise.resolve();
    if (input.value === form.dataset.savedTrackName) return Promise.resolve();
    const submittedName = input.value;
    input.setCustomValidity("");
    return channelAction({
      action: input.dataset.action,
      device: form.dataset.device,
      channel: form.dataset.channel,
      track_name: submittedName,
    })
      .then(() => {
        form.dataset.savedTrackName = submittedName;
      })
      .catch(error => {
        const observed = error.observedStatus?.recs.channels.find(channel =>
          channel.device === form.dataset.device
          && channel.channels.join(",") === form.dataset.channels,
        );
        if (observed) form.dataset.savedTrackName = observed.name;
        if (error.outcomeUnknown && observed?.name === submittedName) return;
        const message = error.outcomeUnknown && !observed
          ? `${error.message} Reload this page before trying again.`
          : error.message;
        input.setCustomValidity(message);
        input.reportValidity();
        if (error.outcomeUnknown && !observed) {
          input.disabled = true;
          const warning = document.createElement("small");
          warning.setAttribute("role", "alert");
          warning.textContent = message;
          form.append(warning);
        }
      });
  }

  function channelForms() {
    return [...document.querySelectorAll('[data-source="show.recs.channels"] .level')];
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
    return channelAction({
      action: input.dataset.action,
      device: form.dataset.device,
      channels: form.dataset.channels,
    })
      .then(() => {
        return updateStatus();
      })
      .catch(error => {
        if (!error.outcomeUnknown) {
          input.checked = !input.checked;
          input.disabled = false;
        }
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
    return channelAction({
      action: button.dataset.action,
      device: form.dataset.device,
      channels: form.dataset.channels,
    })
      .then(() => {
        button.textContent = "Calibrated";
        return updateStatus();
      })
      .catch(error => {
        button.textContent = error.outcomeUnknown ? "Calibration outcome unknown" : "Calibration failed";
        button.title = error.outcomeUnknown
          ? `${error.message} Reload this page to inspect state before trying again.`
          : error.message;
        if (error.outcomeUnknown) button.dataset.outcomeUnknown = "true";
      })
      .finally(() => {
        button.disabled = button.dataset.outcomeUnknown === "true";
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
    return channelAction({
      action: "recs-set-attr",
      address: attribute.dataset.address,
      value: savedValue,
    })
      .then(() => {
        attribute.dataset.savedValue = savedValue;
      })
      .catch(error => {
        const message = error.outcomeUnknown
          ? `${error.message} Reload this page to inspect the current value before trying again.`
          : error.message;
        input.setCustomValidity(message);
        input.reportValidity();
        if (error.outcomeUnknown) {
          input.disabled = true;
          const warning = document.createElement("small");
          warning.setAttribute("role", "alert");
          warning.textContent = message;
          attribute.append(warning);
        }
      });
  }

  function channelForm(channel, trackName, savedTrackName, channels, container) {
    const form = document.getElementById(container.dataset.template).content.firstElementChild.cloneNode(true);
    form.dataset.savedTrackName = savedTrackName;
    const input = form.querySelector('[data-action="recs-track-name"]');
    if (input) input.value = trackName;
    const title = form.querySelector(".channel-caption b");
    if (title) title.textContent = channel.name;
    bindChannelActions(form);
    updateChannelForm(form, channel, channels);
    return form;
  }

  function bindChannelActions(form) {
    const stereo = form.querySelector(".stereo input");
    if (stereo) stereo.addEventListener("change", saveStereo);
    const calibrate = form.querySelector(".calibrate-channel");
    if (calibrate) calibrate.addEventListener("click", calibrateChannel);
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
    for (const container of document.querySelectorAll('[data-source="show.recs.channels"]')) {
      const focused = document.activeElement?.closest(".level");
      const editing = focused && container.contains(focused);
      if (!channels.length && !editing) {
        const message = document.createElement("p");
        message.textContent = container.dataset.emptyText;
        container.replaceChildren(message);
        continue;
      }
      const forms = new Map(
        [...container.querySelectorAll(".level")].map(form => [trackKey({
          device: form.dataset.device,
          channels: form.dataset.channels.split(",").map(Number),
        }), form]),
      );
      const added = [];
      const current = channels.map(channel => {
        const form = forms.get(trackKey(channel));
        if (!form) {
          const created = channelForm(channel, channel.name, channel.name, channels, container);
          added.push(created);
          return created;
        }
        updateChannelForm(form, channel, channels);
        return form;
      });
      if (editing) {
        for (const form of forms.values()) {
          if (form !== focused && !current.includes(form)) form.remove();
        }
        container.append(...added);
      } else {
        container.replaceChildren(...current);
      }
    }
  }

  function updateChannelForm(form, channel, channels) {
    form.className = `level ${channel.state}`;
    form.dataset.device = channel.device;
    form.dataset.channel = channel.name;
    form.dataset.channels = channel.channels.join(",");
    const state = form.querySelector(".channel-state");
    if (state) {
      state.className = `channel-state ${channel.on ? "indicator-red" : "indicator-green"}`;
      state.setAttribute("aria-label", channel.on ? "recording" : "not recording");
      state.title = `${state.dataset.label}: ${state.getAttribute("aria-label")}`;
    }
    const stereo = form.querySelector(".stereo input");
    if (stereo) {
      stereo.checked = channel.channels.length === 2;
      stereo.disabled = !stereoEnabled(channel, channels);
    }
  }

  const saveTrackNamesButton = document.getElementById("save-track-names");
  if (saveTrackNamesButton) {
    saveTrackNamesButton.addEventListener("click", saveTrackNames);
  }
  const revertTrackNamesButton = document.getElementById("revert-track-names");
  if (revertTrackNamesButton) {
    revertTrackNamesButton.addEventListener("click", revertTrackNames);
  }
  for (const input of document.querySelectorAll('[data-source="recs.mutable_attributes"] input')) {
    input.addEventListener("blur", saveMutableAttribute);
  }
  for (const form of channelForms()) {
    bindChannelActions(form);
  }
