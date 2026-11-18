"use strict";

(() => {
  const form = document.querySelector("[data-branding-preview-form]");
  const sheet = document.querySelector("[data-invoice-preview]");
  if (!form || !sheet) return;

  const logo = sheet.querySelector(".company-logo");
  const upload = form.elements.logo;
  const remove = form.elements.remove_logo;
  let pendingLogo = "";

  // Match the PDF accent adjusted by core.services.branding.palette.
  function contrastColor(color) {
    const channels = color.match(/[0-9a-f]{2}/gi).map(value => parseInt(value, 16));
    function luminance(values) {
      const weights = [0.2126, 0.7152, 0.0722];
      return values.reduce((sum, channel, index) => {
        const s = channel / 255;
        const linear = s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
        return sum + linear * weights[index];
      }, 0);
    }
    let rgb = channels;
    while (1.05 / (luminance(rgb) + 0.05) < 4.5) {
      rgb = rgb.map(channel => Math.floor(channel * 0.90));
    }
    return "#" + rgb.map(channel => channel.toString(16).padStart(2, "0")).join("");
  }

  function renderLogo() {
    const source = pendingLogo || (remove?.checked ? "" : sheet.dataset.savedLogo);
    logo.hidden = !source;
    if (source) logo.src = source;
    else logo.removeAttribute("src");
  }

  function renderStyle() {
    if (form.elements.display_name) {
      sheet.querySelector(".masthead .brand").textContent =
        form.elements.display_name.value.trim() || sheet.dataset.defaultName;
    }
    const color = form.elements.accent_color?.value;
    if (color && /^#[0-9a-f]{6}$/i.test(color)) {
      sheet.style.setProperty("--invoice-color", contrastColor(color));
    }
    if (form.elements.document_layout) {
      sheet.classList.toggle("compact", form.elements.document_layout.value === "compact");
    }
    if (form.elements.default_currency) {
      for (const unit of sheet.querySelectorAll(".document-currency")) {
        unit.textContent = form.elements.default_currency.value;
      }
    }
    const days = form.elements.payment_due_days;
    if (days?.validity.valid && Number.isInteger(days.valueAsNumber)) {
      const date = new Date(sheet.dataset.issueDate + "T00:00:00Z");
      date.setUTCDate(date.getUTCDate() + days.valueAsNumber);
      sheet.querySelector(".due-date").textContent = date.toLocaleDateString("fr-FR", {
        timeZone: "UTC", year: "numeric", month: "2-digit", day: "2-digit",
      });
    }
    renderLogo();
  }

  function imageType(bytes) {
    const matches = (signature, offset = 0) => signature.every((byte, index) => bytes[offset + index] === byte);
    if (matches([137, 80, 78, 71, 13, 10, 26, 10])) return "image/png";
    if (matches([255, 216, 255])) return "image/jpeg";
    if (matches([82, 73, 70, 70]) && matches([87, 69, 66, 80], 8)) return "image/webp";
    return "";
  }

  if (upload) {
    const error = form.querySelector("[data-preview-file-error]");
    function showError(message) {
      error.textContent = message;
      error.hidden = !message;
      upload.setCustomValidity(message);
    }

    upload.addEventListener("change", async () => {
      const file = upload.files[0];
      pendingLogo = "";
      showError("");
      renderLogo();
      if (!file) return;

      if (file.size > 2 * 1024 * 1024) {
        showError("Choisissez un logo de 2 Mo maximum.");
        return;
      }
      try {
        const type = imageType(new Uint8Array(await file.slice(0, 12).arrayBuffer()));
        if (upload.files[0] !== file) return;
        if (!type) {
          showError("Choisissez une image PNG, JPEG ou WebP.");
          return;
        }
        const reader = new FileReader();
        reader.addEventListener("load", () => {
          if (upload.files[0] === file) {
            pendingLogo = reader.result;
            renderLogo();
          }
        });
        reader.readAsDataURL(file.slice(0, file.size, type));
      } catch {
        if (upload.files[0] === file) showError("Le fichier sélectionné est illisible.");
      }
    });
  }
  form.addEventListener("input", renderStyle);
  form.addEventListener("change", renderStyle);
  renderStyle();
})();
