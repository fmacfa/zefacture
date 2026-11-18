const form = document.querySelector("[data-setup-form]");
const toggle = form.querySelector("[data-password-toggle]");
const password = form.querySelector('input[name="password"]');

toggle.hidden = false;
toggle.addEventListener("click", () => {
  const visible = password.type === "password";
  password.type = visible ? "text" : "password";
  toggle.setAttribute("aria-pressed", String(visible));
});

async function authorizeInstallation() {
  const prefix = "#installation=";
  if (!window.location.hash.startsWith(prefix)) return;

  const token = window.location.hash.slice(prefix.length);
  const status = form.querySelector("[data-setup-status]");
  window.history.replaceState(null, "", window.location.pathname);
  status.hidden = false;
  status.textContent = "Vérification du lien privé…";

  try {
    const response = await fetch(form.dataset.authorizeUrl, {
      method: "POST",
      credentials: "same-origin",
      redirect: "error",
      headers: { "X-CSRFToken": form.elements.csrfmiddlewaretoken.value },
      body: new URLSearchParams({ token }),
    });
    if (response.status === 204) {
      window.location.reload();
      return;
    }
    status.textContent = response.status === 429
      ? "Trop de tentatives. Réessayez plus tard."
      : "Ce lien d’installation est invalide ou a expiré. Demandez un nouveau lien.";
  } catch {
    status.textContent = "Vérification indisponible. Rouvrez le lien pour réessayer.";
  }
}

window.addEventListener("hashchange", authorizeInstallation);
authorizeInstallation();
