'use strict';
(() => {
  const dialog = document.getElementById('quote-article');
  if (!dialog) return;
  const form = dialog.querySelector('form');
  const submit = form.querySelector('[type=submit]');
  const reuse = form.querySelector('[data-use]');
  const status = form.querySelector('[role=status]');
  let row, existing, onUse, requestId;

  window.openQuoteArticle = (line, callback) => {
    row = line;
    onUse = callback;
    existing = null;
    requestId = Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
    form.reset();
    form.elements.name.value = row.description.split('\n')[0].slice(0,255);
    form.elements.reference.value = row.item_reference || '';
    form.querySelector('[data-price]').textContent = row.unit_price_including_tax != null
      ? 'Prix du catalogue TTC : ' + row.unit_price_including_tax + ' · TVA ' + row.vat_rate + ' %'
      : 'Prix du catalogue HT : ' + row.unit_price_excluding_tax + ' · TVA ' + row.vat_rate + ' %';
    status.textContent = '';
    reuse.hidden = true;
    submit.hidden = false;
    dialog.showModal();
  };
  form.querySelector('[data-close]').addEventListener('click', () => dialog.close());
  reuse.addEventListener('click', () => { if (existing) { onUse(existing); dialog.close(); } });
  form.addEventListener('input', () => { existing = null; reuse.hidden = true; submit.hidden = false; status.textContent = ''; });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (submit.disabled) return;
    const csrf = document.querySelector('#document-form [name=csrfmiddlewaretoken]').value;
    const pending = window.ZeRequests.start(form, submit, 'Ajout au catalogue…');
    submit.disabled = true;
    try {
      const response = await fetch('/app/devis/articles', {
        method:'POST', headers:{'Content-Type':'application/json', Accept:'application/json','X-CSRFToken':csrf},
        body:JSON.stringify({creation_uid:requestId, currency_code:document.getElementById("document-form").elements.currency_code.value, name:form.elements.name.value, item_type:form.elements.item_type.value,
          line:{...row, item_reference:form.elements.reference.value}})
      });
      const answer = await response.json();
      if (!response.ok) throw new Error(answer.error || 'Ajout impossible.');
      if (answer.created) { onUse(answer.item); dialog.close(); }
      else {
        existing = answer.item;
        status.textContent = 'Déjà au catalogue : ' + existing.name + '. Vous pouvez reprendre sa référence.';
        reuse.hidden = false;
        submit.hidden = true;
      }
    } catch (error) { status.textContent = error.message; }
    finally { pending.finish(); submit.disabled = false; }
  });
})();
