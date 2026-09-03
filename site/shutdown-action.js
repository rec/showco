for (const form of document.querySelectorAll("form[method=post]")) {
  form.addEventListener("submit", event => {
    if (form.dataset.confirm === "true" && !confirm("Are you sure?")) {
      event.preventDefault();
      return;
    }
    const button = event.submitter || form.querySelector("button");
    if (!button) return;
    button.setAttribute("aria-busy", "true");
    button.disabled = true;
    button.textContent = "Working...";
  });
}
