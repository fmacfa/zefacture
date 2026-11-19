'use strict';
(() => {
  const form=document.getElementById('document-form');
  const source=document.getElementById('extraction-data');
  if(!form||!source)return;
  const extracted=JSON.parse(source.textContent);
  const side=form.querySelector('[data-extract="buyer.legal_name"]') ? 'buyer' : 'supplier';
  const party=extracted[side]||(side==='buyer'?extracted.customer:extracted.seller)||{};
  extracted[side]={...party,legal_name:party.legal_name||party.name||'',country_code:party.country_code||'FR'};
  for(const group of ['invoice','totals']) {
    if (!extracted[group] || typeof extracted[group] !== 'object' || Array.isArray(extracted[group])) extracted[group]={};
  }
  extracted.invoice.currency_code=extracted.invoice.currency_code||'EUR';
  const fields=form.querySelectorAll('[data-extract]');
  for(const input of fields) {
    const [group,key]=input.dataset.extract.split('.');
    input.value=extracted[group]?.[key]??'';
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
