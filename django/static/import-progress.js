/* Follow the worker using read-only requests for the imports visible on this page. */
(() => {
  "use strict";
  const detail = document.querySelector("[data-import-wait]");
  let rows = document.querySelector("[data-import-rows]");
  const results = document.querySelector("[data-expense-results]");
  let refreshResults = false;
  const connection = document.querySelector("[data-import-connection]");
  let timer, controller, failures = 0, suspended = false;

  function pending() {
    return Array.from(document.querySelectorAll("[data-import-id][data-pending], [data-import-id][data-insert]"));
  }

  function summarize() {
    for (const summary of document.querySelectorAll("[data-import-summary]")) {
      const scope = summary.dataset.importSummary === "upload" ? document.querySelector("[data-batch-files]") : rows;
      const states = Array.from(scope.querySelectorAll("[data-import-id]"));
      const count = (...codes) => states.filter(node => codes.includes(node.dataset.code)).length;
      const parts = [];
      for (const [n, label] of [
        [states.filter(node => node.hasAttribute("data-pending")).length, "en traitement"],
        [count("analyzed", "transcribed"), "à vérifier"],
        [count("failed"), "en échec"],
        [count("uploaded"), "sans analyse"],
      ]) {
        if (n) parts.push(n + " " + label);
      }
      const value = parts.join(" · ");
      if (summary.textContent !== value) summary.textContent = value;
      summary.hidden = !value;
    }
  }

  function schedule(delay) {
    clearTimeout(timer);
    if (!controller && !suspended && !document.hidden && (pending().length || refreshResults)) {
      timer = setTimeout(poll, delay);
    }
  }

  function notice(message) {
    if (connection.textContent !== message) connection.textContent = message;
    connection.hidden = !message;
  }

  function display(id, state) {
    document.querySelectorAll('[data-import-id="' + id + '"]').forEach(badge => {
      if (results && (badge.hasAttribute("data-insert") || badge.dataset.code !== state.code)) refreshResults = true;
      badge.toggleAttribute("data-pending", state.busy);
      badge.removeAttribute("data-insert");
      badge.dataset.code = state.code;
      badge.querySelector(".request-spinner").hidden = !state.busy;
      const label = badge.querySelector("[data-state-label]");
      if (label.textContent !== state.label) label.textContent = state.label;
    });
    if (!results && rows && state.row) {
      const existing = rows.querySelector('[data-import-row="' + id + '"]');
      if (existing) existing.outerHTML = state.row;
      else {
        rows.querySelector("[data-import-empty]")?.remove();
        rows.insertAdjacentHTML("afterbegin", state.row);
      }
    }
  }

  async function poll() {
    controller = new AbortController();
    const timeout = setTimeout(() => controller?.abort(), 15000);
    try {
      const ids = [...new Set(pending().map(node => node.dataset.importId))];
      for (let index = 0; index < ids.length; index += 50) {
        const group = ids.slice(index, index + 50);
        const url = new URL(location.href);
        url.searchParams.set("suivi", group.join(","));
        if (results) url.searchParams.set("etats", "1");
        const missing = rows ? group.filter(id => !rows.querySelector('[data-import-row="' + id + '"]')) : [];
        if (missing.length) url.searchParams.set("ajouter", missing.join(","));
        const response = await fetch(url, {headers: {Accept: "application/json"}, cache: "no-store", signal: controller.signal});
        if (response.status === 403 || response.status === 401 || response.redirected) {
          suspended = true;
          for (const id of ids) display(id, {code: "unavailable", busy: false, label: "Suivi interrompu"});
          notice("Le suivi est interrompu. Reconnectez-vous pour consulter vos documents.");
          return;
        }
        if (response.status === 404 && detail) {
          suspended = true;
          display(group[0], {code: "unavailable", busy: false, label: "Document indisponible"});
          notice("Ce document n’est plus accessible. Vous pouvez revenir à la liste.");
          return;
        }
        if (!response.ok) throw new Error("Suivi indisponible");
        const {items} = await response.json();
        for (const id of group) {
          const state = items[id];
          if (detail && state && !state.busy) {
            // This waiting view has no editable fields; display the normal review form.
            suspended = true;
            location.reload();
            return;
          }
          display(id, state || {code: "unavailable", busy: false, label: "Document indisponible"});
        }
      }
      if (refreshResults && results) {
        const response = await fetch(location.href, {headers: {"X-Expense-Fragment": "results"}, cache: "no-store", signal: controller.signal});
        if (response.redirected || response.status === 403 || response.status === 401) {
          suspended = true;
          notice("Reconnectez-vous pour consulter vos notes de frais.");
          return;
        }
        if (!response.ok) throw new Error("Résultats indisponibles");
        results.innerHTML = await response.text();
        rows = results.querySelector("[data-import-rows]");
        refreshResults = false;
      }
      failures = 0;
      notice("");
      summarize();
    } catch {
      if (!document.hidden && !suspended) {
        failures += 1;
        notice("Connexion au suivi interrompue. Nouvelle tentative automatique…");
      }
    } finally {
      clearTimeout(timeout);
      controller = null;
      schedule(failures ? Math.min(30000, 2000 * 2 ** Math.min(failures, 4)) : 2500);
    }
  }

  document.addEventListener("zf:imports-added", () => { summarize(); schedule(100); });
  document.addEventListener("visibilitychange", () => document.hidden ? clearTimeout(timer) : schedule(0));
  window.addEventListener("pagehide", () => { suspended = true; clearTimeout(timer); controller?.abort(); });
  window.addEventListener("pageshow", () => { suspended = false; schedule(200); });
  summarize();
  schedule(600);
})();
