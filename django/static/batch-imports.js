"use strict";
(() => {
  const form = document.querySelector("[data-batch-upload]");
  if (!form || !window.crypto?.randomUUID) return;
  const {file: input, source_kind: type} = form.elements;
  const list = form.querySelector("[data-batch-files]");
  const status = form.querySelector("[data-batch-progress]");
  const link = form.querySelector("[data-batch-link]");
  const button = form.querySelector("button:not([type]),button[type=submit]");
  let entries = [], batch, running = false;

  form.elements.pipeline.addEventListener("change", event => {
    const option = event.target.selectedOptions[0];
    const help = document.getElementById("import-pipeline-help");
    if (option.dataset.provider) {
      help.textContent = "Dès l’import, " + option.dataset.provider + " reçoit le texte OCR de chaque fichier pour préremplir les champs.";
    } else {
      help.textContent = option.value === "local" ? "Chaque fichier est lu sur ce serveur pour préremplir les champs." : "Les originaux seront enregistrés. Vous pourrez lancer l’analyse depuis leur fiche.";
    }
  });

  function prepare() {
    list.replaceChildren();
    document.dispatchEvent(new Event("zf:imports-added"));
    link.hidden = true;
    button.disabled = false;
    batch = crypto.randomUUID().replaceAll("-", "");
    entries = Array.from(input.files, file => {
      const choice = type.cloneNode(true);
      choice.removeAttribute("name");
      choice.value = type.value;
      choice.setAttribute("aria-label", "Type de " + file.name);
      const result = document.createElement("span");
      result.textContent = "À envoyer";
      if (input.files.length > 1) {
        const line = document.createElement("li"), name = document.createElement("strong"), label = document.createElement("label");
        name.textContent = file.name;
        label.append("Type de document", choice);
        line.append(name, label, result);
        list.append(line);
      }
      return {file, choice, result, state: "selected"};
    });
    const count = entries.length;
    status.textContent = count ? (count === 1 ? "1 fichier sélectionné." : count + " fichiers sélectionnés.") + " Cliquez sur Importer pour lancer l’envoi." : "";
  }

  function showResult(entry, row) {
    const anchor = document.createElement("a");
    anchor.href = row.url;
    entry.state = row.state;
    if (row.state === "duplicate") {
      anchor.className = "duplicate-badge";
      anchor.textContent = row.label;
      entry.result.replaceChildren(anchor);
      return;
    }
    const badge = document.createElement("span"), spinner = document.createElement("span"), label = document.createElement("span");
    badge.className = "import-state";
    badge.dataset.importId = row.ingestion_uid;
    badge.dataset.insert = "";
    badge.dataset.code = row.queued ? "queued" : "uploaded";
    badge.toggleAttribute("data-pending", row.queued);
    spinner.className = "request-spinner";
    spinner.setAttribute("aria-hidden", "true");
    spinner.hidden = !row.queued;
    label.dataset.stateLabel = "";
    label.textContent = row.queued ? "En attente de traitement" : "Importé sans analyse";
    badge.append(spinner, label);
    anchor.textContent = "Ouvrir la fiche";
    entry.result.replaceChildren(badge, anchor);
    document.dispatchEvent(new Event("zf:imports-added"));
  }

  input.addEventListener("change", prepare);
  type.addEventListener("change", () => entries.forEach(entry => { entry.choice.value = type.value; }));
  form.addEventListener("submit", async event => {
    if (entries.length < 2) return;
    event.preventDefault();
    if (running) return;
    if (entries.length > 50) {
      status.textContent = "Sélectionnez au maximum 50 fichiers.";
      return;
    }
    running = true;
    const data = new FormData(form);
    const pending = window.ZeRequests.start(form, button, "Envoi des fichiers…");
    const controls = Array.from(form.elements).filter(control => !control.disabled && control.type !== "hidden");
    controls.forEach(control => { control.disabled = true; });
    try {
      for (const [index, entry] of entries.entries()) {
        if (["imported", "duplicate"].includes(entry.state)) continue;
        status.textContent = "Envoi du fichier " + (index + 1) + " sur " + entries.length + "…";
        entry.result.classList.add("request-inline-busy");
        entry.result.textContent = "Envoi en cours…";
        data.set("file", entry.file);
        data.set("source_kind", entry.choice.value);
        data.set("batch_uid", batch);
        try {
          const response = await fetch(form.action, {method: "POST", body: data, headers: {Accept: "application/json"}});
          if (!response.headers.get("Content-Type")?.includes("application/json")) throw new Error("Connexion interrompue ou fichier trop volumineux.");
          const body = await response.json();
          if (!response.ok) throw new Error(body.error || "Échec de l’import.");
          const row = body.files[0];
          if (row.state === "failed") throw new Error(row.label);
          showResult(entry, row);
        } catch (error) {
          entry.state = "failed";
          entry.result.textContent = "À reprendre · " + error.message;
        } finally {
          entry.result.classList.remove("request-inline-busy");
        }
      }
      const added = entries.filter(e => e.state === "imported").length;
      const copies = entries.filter(e => e.state === "duplicate").length;
      const failed = entries.length - added - copies;
      const summary = [];
      if (added) summary.push(added === 1 ? "1 fichier importé" : added + " fichiers importés");
      if (copies) summary.push(copies === 1 ? "1 doublon" : copies + " doublons");
      if (failed) summary.push(failed + " à reprendre");
      status.textContent = summary.join(" · ") + ". " + (failed ? "Cliquez sur Importer pour réessayer les fichiers en erreur." : added ? "Consultez l’état de chaque fichier ci-dessous." : "Les liens ouvrent les originaux.");
      link.href = location.pathname + "?lot=" + batch;
      link.hidden = !added;
    } finally {
      pending.finish();
      running = false;
      controls.forEach(control => { control.disabled = false; });
      button.disabled = entries.every(e => e.state !== "failed");
    }
  });
})();
