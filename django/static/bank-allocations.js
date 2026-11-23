(() => {
  const form = document.querySelector("[data-allocation-form]");
  if (!form) return;
  const lines = form.querySelector("[data-allocation-lines]");
  const add = form.querySelector("[data-add-line]");
  const output = form.querySelector("[data-allocation-balance]");
  const format = new Intl.NumberFormat("fr-FR", {minimumFractionDigits: 2, maximumFractionDigits: 2});
  function update() {
    let sum = 0;
    let valid = true;
    Array.from(lines.children).forEach((row, i) => {
      row.querySelector("[data-line-number]").textContent = i + 1;
      row.querySelector("[data-remove-line]").hidden = lines.children.length === 1;
      const raw = row.querySelector("[name=allocation_amount]").value.trim().replace(",", ".");
      if (raw && !/^[+-]?\d+(?:\.\d{1,2})?$/.test(raw)) valid = false;
      sum += Math.round(Number(raw) * 100);
    });
    add.hidden = lines.children.length >= 100;
    const remaining = Math.round(Number(form.dataset.net) * 100) - sum;
    output.textContent = valid ? "Reste à répartir " + format.format(remaining / 100) + " " + form.dataset.currency : "";
  }
  add.addEventListener("click", () => {
    const row = lines.firstElementChild.cloneNode(true);
    row.querySelectorAll("select, input").forEach(input => { input.value = ""; });
    lines.append(row);
    update();
    row.querySelector("select").focus();
  });
  lines.addEventListener("click", event => {
    const remove = event.target.closest("[data-remove-line]");
    if (!remove || lines.children.length === 1) return;
    remove.closest("fieldset").remove();
    update();
  });
  lines.addEventListener("input", update);
  update();
})();
