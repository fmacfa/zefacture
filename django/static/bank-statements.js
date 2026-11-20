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

const upload = document.querySelector("[data-bank-upload]");
if (upload) {
  const input = upload.elements.file;
  const status = upload.querySelector("[data-bank-selection]");
  const limit = Number(upload.dataset.maxFiles);
  let sending = false;
  function checkSelection() {
    const files = Array.from(input.files);
    const tooLarge = files.find(file => file.size > 5000000);
    const error = files.length > limit ? "Sélectionnez au maximum " + limit + " fichiers." :
      tooLarge ? "Le fichier « " + tooLarge.name + " » dépasse 5 Mo." : "";
    input.setCustomValidity(error);
    status.hidden = !files.length;
    status.textContent = error || files.length + " fichier(s) sélectionné(s) · " + files.map(file => file.name).join(", ");
  }
  input.addEventListener("change", checkSelection);
  upload.addEventListener("submit", async event => {
    if (sending) { event.preventDefault(); return; }
    checkSelection();
    if (!input.reportValidity()) { event.preventDefault(); return; }
    const files = Array.from(input.files);
    if (files.reduce((sum, f) => sum + f.size, 0) <= 15000000) return;
    event.preventDefault();
    sending = true;
    const submit = event.submitter;
    const payload = new FormData(upload);
    payload.delete("file");
    const pending = window.ZeRequests?.start(upload, submit, "Envoi des relevés…");
    let next = "";
    try {
      for (let i = 0; i < files.length;) {
        const data = new FormData();
        for (const [key, value] of payload) data.append(key, value);
        data.set("offset", i);
        let bytes = 0;
        status.textContent = "Envoi en cours · " + i + " / " + files.length + " fichiers transmis.";
        while (i < files.length && bytes + files[i].size <= 15000000) {
          data.append("file", files[i]); bytes += files[i++].size;
        }
        const response = await fetch(upload.action, {method:"POST", body:data, headers:{Accept:"application/json"}, credentials:"same-origin"});
        if (response.redirected) throw new Error("Reconnectez-vous pour poursuivre l’envoi.");
        const json = await response.json();
        if (!response.ok || !json.url) throw new Error(json.error || "Envoi interrompu.");
        next = json.url;
      }
      location.assign(next);
    } catch (error) {
      status.textContent = (error.message || "Envoi interrompu.") + " Vous pouvez renvoyer la même sélection.";
      if (next) {
        const link = document.createElement("a");
        link.href = next; link.textContent = "Voir les fichiers déjà transmis";
        status.append(" ", link);
      }
    } finally { sending = false; pending?.finish(); }
  });
}

if (document.querySelector("[data-bank-poll]")) {
  async function refreshBatch() {
    const current = document.querySelector("[data-bank-poll]");
    if (!current) return;
    try {
      if (!document.hidden && !current.contains(document.activeElement)) {
        const response = await fetch(location.href, {cache:"no-store"});
        if (response.redirected) { location.assign(response.url); return; }
        if (response.ok) {
          const parsed = new DOMParser().parseFromString(await response.text(), "text/html");
          const updated = parsed.getElementById("bank-batch-files");
          if (updated) current.replaceWith(updated);
        }
      }
    } catch { /* A later poll resumes the same server-side batch. */ }
    if (document.querySelector("[data-bank-poll]")) setTimeout(refreshBatch, 3000);
  }
  setTimeout(refreshBatch, 3000);
}
