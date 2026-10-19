(() => {
  for (const form of document.querySelectorAll("[data-transport] form")) {
    form.addEventListener("submit", async event => {
      event.preventDefault();
      const button = form.querySelector("button");
      if (button.disabled) return;
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      if (form.dataset.disabledWhen === "streamo_unavailable") {
        button.textContent = button.textContent === "Stop Twitch" ? "Stopping..." : "Starting...";
      }
      const result = document.querySelector("[data-action-result]");
      try {
        const message = await showAction(Object.fromEntries(new FormData(form)));
        if (result) {
          result.textContent = message;
          result.className = "ok";
        }
      } catch (error) {
        if (result) {
          result.textContent = error.message;
          result.className = "failed";
        }
      } finally {
        button.removeAttribute("aria-busy");
        button.disabled = true;
        await globalThis.refreshShowStatus().catch(statusFailed);
      }
    });
  }

})();
