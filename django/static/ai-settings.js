"use strict";

for (const form of document.querySelectorAll("[data-ai-probe]")) {
  const result = form.closest("section").querySelector("[data-probe-result]");
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const button = event.submitter || form.querySelector("button");
    const pending = window.ZeRequests.start(form, button, button.dataset.loadingLabel);
    result.hidden = true;
    result.textContent = "";
    const data = new FormData(form);
    data.set("action", "test");
    try {
      const response = await fetch(form.getAttribute("action") || location.pathname, {
        method: "POST", body: data, credentials: "same-origin", headers: {Accept: "application/json"},
      });
      if (response.redirected && new URL(response.url).origin === location.origin) {
        location.assign(response.url);
        return;
      }
      if (!response.headers.get("Content-Type")?.includes("application/json")) {
        throw new Error("Le serveur n’a pas pu terminer le test (HTTP " + response.status + ").");
      }
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "La connexion au fournisseur a échoué.");
      result.classList.remove("error");
      result.textContent = body.message;
    } catch (error) {
      result.classList.add("error");
      result.textContent = error instanceof TypeError ? "Connexion au serveur interrompue. Vous pouvez relancer le test." : error.message;
    } finally {
      pending.finish();
      result.hidden = !result.textContent;
    }
  });
}
