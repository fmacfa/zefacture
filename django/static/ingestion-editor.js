'use strict';
(() => {
  const form=document.getElementById('document-form');
  const source=document.getElementById('extraction-data');
  if(!form||!source)return;
  const extracted=JSON.parse(source.textContent);
  const supplier=extracted.supplier||extracted.seller||{};
  extracted.supplier={...supplier,legal_name:supplier.legal_name||supplier.name||'',country_code:supplier.country_code||'FR'};
  extracted.invoice=extracted.invoice||{};
  extracted.invoice.currency_code=extracted.invoice.currency_code||'EUR';
  const fields=form.querySelectorAll('[data-extract]');
  for(const input of fields) {
    const [group,key]=input.dataset.extract.split('.');
    input.value=extracted[group]?.[key]||'';
  }
  function serialize() {
    for(const input of fields) {
      const [group,key]=input.dataset.extract.split('.');
      extracted[group]??={};extracted[group][key]=input.value;
    }
    extracted.lines=JSON.parse(document.getElementById('lines-json').value||'[]');
    form.elements.extraction_json.value=JSON.stringify(extracted);
  }
  form.addEventListener('input',serialize);
  form.addEventListener('submit',serialize);
  serialize();
  form.dispatchEvent(new Event('input',{bubbles:true}));
})();
