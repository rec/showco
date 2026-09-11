(() => {
  function duration(seconds) {
    if (seconds === null) return "unknown time";
    const wholeSeconds = Math.floor(seconds);
    const minutes = Math.floor(wholeSeconds / 60);
    const hours = Math.floor(minutes / 60);
    const remainder = wholeSeconds % 60;
    return hours
      ? `${hours}:${String(minutes % 60).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
      : `${minutes}:${String(remainder).padStart(2, "0")}`;
  }

  function update(playback) {
    document.getElementById("playback-state").textContent = playback.state;
    const selected = playback.state !== "waiting";
    document.getElementById("playback-selection").textContent = selected
      ? `Session ${playback.session}: ${playback.source} channel ${playback.channel} to output ${playback.output_channel}`
      : "No session selected";
    document.getElementById("playback-position").textContent = selected
      ? `${duration(playback.position_seconds)} / ${duration(playback.duration_seconds)}`
      : "";
    for (const form of document.querySelectorAll("#playback-transport form")) {
      const action = form.querySelector("[name=action]").value;
      form.querySelector("button").disabled = action !== "recs-playback-play" && !selected;
    }
  }

  async function refresh() {
    const response = await fetch("/status", {cache: "no-store"});
    if (!response.ok) throw new Error(`status request failed: ${response.status}`);
    update((await response.json()).recs.playback);
  }

  for (const form of document.querySelectorAll("#playback-transport form")) {
    form.addEventListener("submit", async event => {
      event.preventDefault();
      const button = form.querySelector("button");
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      const result = document.getElementById("playback-result");
      try {
        const response = await fetch("/actions", {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
          },
          body: new URLSearchParams(new FormData(form)),
        });
        if (!response.ok) throw new Error(`playback request failed: ${response.status}`);
        const action = await response.json();
        result.textContent = action.message;
        result.className = action.ok ? "ok" : "failed";
        await refresh();
      } catch (error) {
        result.textContent = error.message;
        result.className = "failed";
      } finally {
        button.removeAttribute("aria-busy");
        button.disabled = false;
        await refresh().catch(() => {});
      }
    });
  }

  function poll() {
    refresh().catch(() => {}).finally(() => setTimeout(poll, 1000));
  }

  poll();
})();
