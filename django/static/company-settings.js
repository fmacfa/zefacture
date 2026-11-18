"use strict";

const companyForm = document.querySelector("[data-company-settings-form]");
const initialSettings = new URLSearchParams(new FormData(companyForm)).toString();

function warnAboutUnsavedSettings(event) {
  event.preventDefault();
  event.returnValue = true;
}

function updateUnsavedWarning() {
  const values = new URLSearchParams(new FormData(companyForm)).toString();
  if (companyForm.hasAttribute("data-unsaved") || values !== initialSettings) {
    window.addEventListener("beforeunload", warnAboutUnsavedSettings);
  } else {
    window.removeEventListener("beforeunload", warnAboutUnsavedSettings);
  }
}

companyForm.addEventListener("input", updateUnsavedWarning);
companyForm.addEventListener("change", updateUnsavedWarning);
companyForm.addEventListener("submit", () => {
  window.removeEventListener("beforeunload", warnAboutUnsavedSettings);
});
updateUnsavedWarning();
