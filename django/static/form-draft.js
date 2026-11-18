"use strict";

(() => {
  for (const select of document.querySelectorAll("[data-suggested-value]")) {
    const custom = select.nextElementSibling;
    function toggleCustomValue() {
      const active = select.value === select.dataset.suggestedValue;
      custom.hidden = custom.disabled = !active;
    }
    select.addEventListener("change", toggleCustomValue);
    toggleCustomValue();
  }

  for (const editor of document.querySelectorAll("[data-customer-form]")) {
    const label = editor.querySelector("[data-customer-name]");
    const name = editor.elements.legal_name;
    const details = editor.querySelector("[data-customer-business-details]");
    function showCustomerFields() {
      const business = editor.elements.type.value !== "individual";
      label.textContent = business ? "Raison sociale" : "Nom et prénom";
      name.setAttribute("aria-label", label.textContent);
      name.autocomplete = business ? "organization" : "name";
      details.hidden = !business && !details.querySelector(".errorlist");
    }
    editor.addEventListener("change", showCustomerFields);
    showCustomerFields();
  }

  function formState(form) {
    return JSON.stringify(Array.from(new FormData(form), ([name, value]) => [
      name,
      value instanceof File ? [value.name, value.size, value.lastModified && value.name ? value.lastModified : 0] : value,
    ]));
  }

  const baseline = new Map(
    Array.from(document.querySelectorAll("[data-form-draft]"), form => [form, formState(form)])
  );
  const saved = new Set();

  function warn(event) {
    event.preventDefault();
    event.returnValue = true;
  }

  function updateWarning() {
    const unsaved = Array.from(baseline).some(([form, initial]) => (
      !saved.has(form) && (form.hasAttribute("data-unsaved") || formState(form) !== initial)
    ));
    if (unsaved) window.addEventListener("beforeunload", warn);
    else window.removeEventListener("beforeunload", warn);
  }

  for (const form of baseline.keys()) {
    form.addEventListener("input", () => {
      saved.delete(form);
      updateWarning();
    });
    form.addEventListener("change", updateWarning);
    form.addEventListener("submit", event => {
      if (!event.defaultPrevented) saved.add(form);
      updateWarning();
    });
  }
  updateWarning();
})();
