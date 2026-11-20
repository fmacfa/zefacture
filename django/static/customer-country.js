(() => {
  const form = document.querySelector("[data-customer-form]");
  if (!form) return;
  const input = form.elements.country_code;
  function refresh() {
    const value = input.value.trim().toUpperCase();
    const option = Array.from(input.list?.options || []).find(item => item.value.toUpperCase() === value);
    const code = option?.value.slice(-3, -1) || (value === "FRANCE" ? "FR" : value);
    for (const group of form.querySelectorAll("[data-french-registration]")) {
      group.hidden = Boolean(code && code !== "FR" && !group.querySelector("input").value);
    }
  }
  input.addEventListener("input", refresh);
  refresh();
})();
