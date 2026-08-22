for (const form of document.querySelectorAll("form[data-confirm=true]")) {
  form.addEventListener("submit", event => {
    if (!confirm("Are you sure?")) {
      event.preventDefault();
    }
  });
}
