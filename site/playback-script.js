(() => {
  for (const form of document.querySelectorAll("[data-transport] form")) {
    form.addEventListener("submit", async event => {
      event.preventDefault();
      const button = form.querySelector("button");
      if (button.disabled) return;
      const fields = Object.fromEntries(new FormData(form));
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      if (form.dataset.disabledWhen === "streamo_unavailable") {
        button.textContent = fields.action === "streamo-stop" ? "Stopping..." : "Starting...";
      }
      const result = document.querySelector("[data-action-result]");
      try {
        const message = await showAction(fields);
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
