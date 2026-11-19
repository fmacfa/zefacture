"use strict";
const manual = document.querySelector("[data-manual-bank]");
if (manual) {
  const category = manual.elements.standalone_category;
  category.addEventListener("change", () => {
    const group = category.selectedOptions[0]?.closest("optgroup");
    if (group) manual.elements.direction.value = group.dataset.direction;
  });
  manual.elements.direction.addEventListener("change", () => {
    const group = category.selectedOptions[0]?.closest("optgroup");
    if (group && group.dataset.direction !== manual.elements.direction.value) category.value = "";
  });
}

const review = document.querySelector("[data-pdf-review]");
if (review) {
  const rows = review.querySelector("[data-pdf-rows]");
  const add = review.querySelector("[data-add-pdf-row]");
  add.hidden = rows.children.length >= Number(review.dataset.limit);
  add.addEventListener("click", () => {
    const slot = rows.children.length;
    if (slot >= Number(review.dataset.limit)) return;
    const row = review.querySelector("[data-pdf-template]").content.firstElementChild.cloneNode(true);
    for (const field of row.querySelectorAll("[name]")) {
      if (["selected", "slot"].includes(field.name)) field.value = slot;
      else field.name += slot;
    }
    rows.append(row);
    row.querySelector('[type="date"]').focus();
    add.hidden = rows.children.length >= Number(review.dataset.limit);
  });
}
