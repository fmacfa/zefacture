const form = document.querySelector("[data-setup-form]");
const status = form.querySelector("[data-setup-status]");
const toggle = form.querySelector("[data-password-toggle]");
const password = form.querySelector('input[name="password"]');

let linkVerification;
let checkingSubmission = false;
let confirmedSubmission = false;

function showStatus(message, state = "hint") {
  status.textContent = message;
  status.dataset.state = state;
}

async function requestAuthorization(options = {}) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 10000);
  try {
    return await fetch(form.dataset.authorizeUrl, {
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      signal: controller.signal,
      ...options,
    });
  } finally {
    window.clearTimeout(timeout);
  }
}

toggle.hidden = false;
toggle.addEventListener("click", () => {
  const visible = password.type === "password";
  password.type = visible ? "text" : "password";
  toggle.setAttribute("aria-pressed", String(visible));
});

form.addEventListener("submit", async event => {
  if (confirmedSubmission) return;

  event.preventDefault();
  if (checkingSubmission) return;

  checkingSubmission = true;
  const pending = window.ZeRequests.start(form, event.submitter, "Vérification de l’autorisation d’installation…");
  try {
    await linkVerification;
    showStatus("Vérification de l’autorisation d’installation…");
    const response = await requestAuthorization();

    if (response.status === 204) {
      form.dataset.setupAuthorized = "true";
      showStatus("Lien privé vérifié. Création du compte…", "ready");
      pending.finish();
      confirmedSubmission = true;
      form.requestSubmit(event.submitter ?? undefined);
    } else if (response.status === 403) {
      form.dataset.setupAuthorized = "false";
      showStatus("Autorisation requise. Ouvrez votre lien privé dans ce navigateur, puis cliquez de nouveau sur « Créer mon espace ».", "error");
      status.focus();
    } else {
      showStatus("Vérification indisponible. Réessayez dans quelques instants.", "error");
      status.focus();
    }
  } catch {
    showStatus("Vérification indisponible. Réessayez dans quelques instants.", "error");
    status.focus();
  } finally {
    pending.finish();
    confirmedSubmission = false;
    checkingSubmission = false;
  }
});

async function authorizeInstallation(token) {
  status.classList.add("request-inline-busy");
  showStatus("Vérification du lien privé…");

  try {
    const response = await requestAuthorization({
      method: "POST",
      headers: { "X-CSRFToken": form.elements.csrfmiddlewaretoken.value },
      body: new URLSearchParams({ token }),
    });

    if (response.status === 204) {
      form.dataset.setupAuthorized = "true";
      showStatus("Lien privé vérifié. Vous pouvez créer votre compte.", "ready");
      return;
    }

    showStatus(response.status === 429
      ? "Trop de tentatives. Réessayez plus tard."
      : "Lien d’installation invalide ou expiré. Demandez un nouveau lien à l’exploitant.", "error");
  } catch {
    showStatus("Vérification indisponible. Rouvrez le lien pour réessayer.", "error");
  } finally {
    status.classList.remove("request-inline-busy");
  }
}

function readInstallationLink() {
  const prefix = "#installation=";
  if (!window.location.hash.startsWith(prefix)) return;

  const token = window.location.hash.slice(prefix.length);
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
  linkVerification = authorizeInstallation(token);
}

window.addEventListener("hashchange", readInstallationLink);
readInstallationLink();
