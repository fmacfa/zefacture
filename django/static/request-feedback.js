/* Keep normal browser submissions and large downloads on their native paths. */
(() => {
  "use strict";
  const indicator = document.querySelector("[data-request-status]");
  if (!indicator) return;
  const message = indicator.querySelector("[data-request-message]");
  const active = new Map();
  let navigation = null;

  function render() {
    const last = Array.from(active.values()).at(-1);
    message.textContent = last ? last.label : "";
    indicator.hidden = !last;
  }

  function start(owner, control, label) {
    if (active.has(owner)) return active.get(owner);
    const previousBusy = owner.getAttribute("aria-busy");
    const previousDisabled = control?.getAttribute("aria-disabled");
    const cleanups = [];
    owner.setAttribute("aria-busy", "true");
    if (control) {
      control.setAttribute("aria-disabled", "true");
      control.classList.add("request-pending");
    }
    const entry = {label, cleanups, finish() {
      if (active.get(owner) !== entry) return;
      for (const cleanup of cleanups) cleanup();
      active.delete(owner);
      if (navigation === entry) navigation = null;
      if (previousBusy === null) owner.removeAttribute("aria-busy");
      else owner.setAttribute("aria-busy", previousBusy);
      if (control) {
        control.classList.remove("request-pending");
        if (previousDisabled == null) control.removeAttribute("aria-disabled");
        else control.setAttribute("aria-disabled", previousDisabled);
      }
      render();
    }};
    active.set(owner, entry);
    render();
    return entry;
  }

  // Asynchronous form handlers call finish() on both success and failure.
  window.ZeRequests = {start};

  function nativeRequest(owner, control, label, download) {
    const entry = start(owner, control, label);
    if (!download) navigation = entry;
    // Some browsers don't report cancelled navigations back to the source page.
    const timeout = setTimeout(entry.finish, 180000);
    entry.cleanups.push(() => clearTimeout(timeout));
    return entry;
  }

  function monitorDownload(entry) {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    const id = Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
    const cookie = "zf_download_" + id;
    const poll = setInterval(() => {
      if (document.cookie.split("; ").includes(cookie + "=1")) entry.finish();
    }, 250);
    entry.cleanups.push(() => {
      clearInterval(poll);
      document.cookie = cookie + "=; Max-Age=0; Path=/; SameSite=Strict" +
        (location.protocol === "https:" ? "; Secure" : "");
    });
    return id;
  }

  // Reject a repeated submission before a form-level AJAX handler can send it.
  document.addEventListener("submit", event => {
    if (active.has(event.target)) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);

  // At the window, HTML validation, form handlers and confirmation dialogs have run.
  window.addEventListener("submit", event => {
    if (event.defaultPrevented) return;
    const form = event.target;
    const control = event.submitter;
    const target = control?.getAttribute("formtarget") || form.target;
    if (target && target !== "_self") return;
    const method = (control?.getAttribute("formmethod") || form.method).toLowerCase();
    if (method === "dialog") return;
    const download = form.hasAttribute("data-download") || control?.hasAttribute("data-download");
    const label = control?.dataset.loadingLabel || form.dataset.loadingLabel ||
      (download ? "Préparation du téléchargement…" : method === "get" ? "Chargement des résultats…" : "Traitement de la demande…");
    const entry = nativeRequest(form, control, label, download);
    if (download && method === "get") {
      const field = document.createElement("input");
      field.type = "hidden";
      field.name = "_zf_download";
      field.value = monitorDownload(entry);
      form.append(field);
      entry.cleanups.push(() => field.remove());
    }
  });

  window.addEventListener("click", event => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest?.("a[href]");
    if (!link || (link.target && link.target !== "_self")) return;
    const url = new URL(link.href);
    if (!["http:", "https:"].includes(url.protocol) || url.origin !== location.origin) return;
    if (url.pathname === location.pathname && url.search === location.search &&
        (url.hash || link.getAttribute("href").startsWith("#"))) return;
    if (active.has(link)) {
      event.preventDefault();
      return;
    }
    const download = link.hasAttribute("data-download") || link.hasAttribute("download");
    const entry = nativeRequest(link, link, download ? "Préparation du téléchargement…" : "Chargement de la page…", download);
    if (download) {
      const original = link.getAttribute("href");
      url.searchParams.set("_zf_download", monitorDownload(entry));
      link.href = url.href;
      entry.cleanups.push(() => link.setAttribute("href", original));
    }
  });

  window.navigation?.addEventListener("navigate", event => {
    const pending = navigation;
    if (pending) event.signal.addEventListener("abort", pending.finish, {once:true});
  });
  function reset() { for (const entry of [...active.values()]) entry.finish(); }
  window.addEventListener("pagehide", reset);
  window.addEventListener("pageshow", event => { if (event.persisted) reset(); });
})();
