(() => {
  for (const form of document.querySelectorAll("[data-transport] form")) {
    form.addEventListener("submit", async event => {
      event.preventDefault();
      const button = form.querySelector("button");
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      const result = document.querySelector("[data-action-result]");
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
        if (result) {
          result.textContent = action.message;
          result.className = action.ok ? "ok" : "failed";
        }
        await globalThis.refreshShowStatus();
      } catch (error) {
        if (result) {
          result.textContent = error.message;
          result.className = "failed";
        }
      } finally {
        button.removeAttribute("aria-busy");
        button.disabled = false;
        await globalThis.refreshShowStatus().catch(statusFailed);
      }
    });
  }

})();
