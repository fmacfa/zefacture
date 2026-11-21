'use strict';
(() => {
  const form = document.getElementById('document-form');
  if (!form) return;
  const deliveryMode = document.getElementById('delivery-mode');
  if (deliveryMode) {
    const address = document.getElementById('delivery-custom');
    const show = () => { address.hidden = deliveryMode.value !== 'custom'; };
    deliveryMode.addEventListener('change', show);
    show();
  }

  const blank = () => ({description:'', item_reference:'', quantity:'1', unit_code:'C62',
    unit_price_excluding_tax:'0', unit_price_including_tax:null, discount_percent:'0', vat_rate:'20', vat_category_code:'S'});
  const lines = JSON.parse(document.getElementById('initial-lines').textContent).map(row => ({
    ...blank(), ...row, quantity:row.quantity ?? row.qty ?? '1',
    unit_price_excluding_tax:row.unit_price_excluding_tax ?? row.unit_price ?? '0',
    vat_category_code:row.vat_category_code || (Number(row.vat_rate ?? 20) ? 'S' : 'Z')
  }));
  const inventory = JSON.parse(document.getElementById('inventory-data').textContent);
  const tbody = document.getElementById('line-rows');
  const hidden = document.getElementById('lines-json');
  const summary = document.getElementById('totals');
  const breakdown = document.getElementById('tax-breakdown');
  const status = document.getElementById('save-status');
  const currency = () => form.elements.currency_code.value;
  const money = amount => window.ZeNumbers.amount(amount);
  const units = [['C62','Unité'],['HUR','Heure'],['DAY','Jour'],['MON','Mois'],['ANN','Année'],['KGM','Kilogramme'],
    ['LTR','Litre'],['MTR','Mètre'],['MTK','m²'],['MTQ','m³'],['KWH','kWh'],['MIN','Minute'],['LS','Forfait'],['EA','Pièce']];
  const categories = [['S','Taxée'],['Z','Taux zéro'],['E','Exonérée'],['AE','Autoliquidation'],['K','Livraison intra-UE'],['G','Export hors UE'],['O','Hors champ']];
  let saveTimer, previewTimer, previewRequest, revision = 0, saving = false, pendingSubmit = false, submitting = false, dirty = false;

  function editableNumber(value) {
    let raw = String(value ?? '');
    const small = /^([+-]?)(\d)(?:\.(\d+))?[eE]-(\d{1,2})$/.exec(raw);
    if (small && Number(small[4]) > 0)
      raw = small[1] + '0.' + '0'.repeat(Number(small[4])-1) + small[2] + (small[3] || '');
    return raw.includes('.') ? raw.replace(/0+$/, '').replace(/\.$/, '') : raw;
  }
  function input(row, key, label, numeric=false) {
    const field = document.createElement('input');
    field.type = numeric ? 'number' : 'text';
    field.value = numeric ? editableNumber(row[key]) : (row[key] ?? '');
    field.setAttribute('aria-label', label);
    if (numeric) {
      field.className='num';
      field.min = key==='quantity' ? '0.0001' : '0';
      field.step = key==='unit_price_excluding_tax' ? '0.00000001' : '0.0001';
      if (key==='vat_rate' || key==='discount_percent') field.max='100';
    }
    if (key==='vat_rate') field.setAttribute('list','common-vat-rates');
    if (key==='description') field.required = true;
    field.addEventListener('input', () => {
      row[key] = field.value;
      if (key==='vat_rate') {
        row.vat_category_code = Number(row.vat_rate)>0 ? 'S' : (row.vat_category_code==='S' ? 'Z' : row.vat_category_code);
        field.closest('tr').querySelector('[data-category]').value = row.vat_category_code;
      }
    });
    return field;
  }
  function select(label, options, value, apply) {
    const element = document.createElement('select');
    element.setAttribute('aria-label',label);
    options.forEach(([key,text]) => element.add(new Option(text,key,false,value===key)));
    element.addEventListener('change', () => { apply(element.value); changed(); });
    return element;
  }
  function button(label, action) {
    const b = document.createElement('button');
    b.type='button'; b.className='text-button'; b.textContent=label;
    b.addEventListener('click',action);
    return b;
  }
  function render() {
    tbody.replaceChildren();
    lines.forEach((row,index) => {
      const tr = tbody.insertRow();
      const cell = label => { const td=tr.insertCell(); td.dataset.label=label; return td; };
      const identity=cell('Désignation / référence');
      identity.append(input(row,'description','Désignation'),input(row,'item_reference','Référence'));
      if (form.dataset.quote==='1') {
        const add = button('Ajouter au catalogue',() => window.openQuoteArticle(row,item => {
          row.item_reference = item.reference || row.item_reference;
          if (!inventory.some(i=>i.item_uid===item.item_uid)) {
            inventory.push(item);
            document.getElementById('inventory-select').add(new Option(item.name,String(inventory.length-1)));
          }
          row.catalogName=item.name;
          render(); changed();
        }));
        identity.append(add);
        if (row.catalogName) { const note=document.createElement('small');note.textContent='Au catalogue · '+row.catalogName;identity.append(note); }
      }
      cell('Quantité').append(input(row,'quantity','Quantité',true));
      cell('Unité').append(select('Unité',units,row.unit_code,v=>{row.unit_code=v;}));
      const gross = row.unit_price_including_tax != null;
      const price=cell('Prix unitaire');
      price.append(input(row,gross?'unit_price_including_tax':'unit_price_excluding_tax',gross?'Prix unitaire TTC':'Prix unitaire HT',true));
      price.append(select('Mode de saisie du prix',[['net','HT'],['gross','TTC']],gross?'gross':'net',mode=>{
        const factor = 1+Number(row.vat_rate)/100;
        if (mode==='gross') row.unit_price_including_tax = Number((Number(row.unit_price_excluding_tax)*factor).toFixed(4)).toString();
        else {
          row.unit_price_excluding_tax=Number((Number(row.unit_price_including_tax)/factor).toFixed(8)).toString();
          row.unit_price_including_tax=null;
        }
        render();
      }));
      cell('Remise de ligne (%)').append(input(row,'discount_percent','Remise',true));
      const tax=cell('TVA');
      const cat=select('Catégorie TVA',categories,row.vat_category_code,v=>{
        row.vat_category_code=v;
        row.vat_rate=v==='S' ? (Number(row.vat_rate) ? row.vat_rate : '20') : '0';
        render();
      });
      cat.dataset.category='1';
      tax.append(input(row,'vat_rate','TVA',true),cat);
      const total=cell('Total HT');total.dataset.total=index;total.className='num';
      const remove=button('×',()=>{if(lines.length>1){lines.splice(index,1);render();changed();}});
      remove.setAttribute('aria-label','Supprimer la ligne '+(index+1));
      cell('Actions').append(remove);
    });
    update();
  }

  function update() {
    const intraHelp = document.getElementById('intra-vat-help');
    if (intraHelp) intraHelp.hidden = !lines.some(row => row.vat_category_code==='K');
    const options = document.getElementById('inventory-select')?.options;
    if(options) inventory.forEach((item,index) => {
      options[index+1].disabled = Boolean(item.currency_code && item.currency_code!==currency());
      if(item.currency_code) options[index+1].textContent = item.name+' · '+item.currency_code;
    });
    hidden.value = JSON.stringify(lines);
    const current = ++revision;
    clearTimeout(previewTimer);
    previewRequest?.abort();
    summary.setAttribute('aria-busy','true');
    summary.classList.add('request-inline-busy');
    previewTimer=setTimeout(async()=>{
      previewRequest=new AbortController();
      try {
        const response=await fetch('/app/documents/calculer',{
          method:'POST', signal:previewRequest.signal,
          headers:{Accept:'application/json','Content-Type':'application/json','X-CSRFToken':form.elements.csrfmiddlewaretoken.value},
          body:JSON.stringify({lines, global_discount_type:form.elements.global_discount_type.value,
            global_discount_value:form.elements.global_discount_value.value})
        });
        const data=await response.json();
        if(current!==revision)return;
        if(!response.ok)throw new Error(data.error||'Calcul indisponible.');
        data.lines.forEach((amount,i)=>{tbody.querySelector('[data-total="'+i+'"]').textContent=money(amount);});
        const parts=[];
        if(Number(data.allowance))parts.push('Remise globale HT −'+money(data.allowance));
        parts.push('HT '+money(data.net),'TVA '+money(data.vat));
        if(Number(data.rounding))parts.push('Arrondi '+money(data.rounding));
        parts.push('TTC '+money(data.payable)+' '+currency());
        summary.textContent=parts.join(' · ');
        breakdown.textContent=data.taxes.map(t=>(t.category==='S'?'TVA':t.label)+' '+window.ZeNumbers.number(editableNumber(t.rate))+' % · base '+money(t.basis)+' · TVA '+money(t.tax)).join('  /  ');
      } catch(error) {
        if(current!==revision||error.name==='AbortError')return;
        summary.textContent=error.message;
        breakdown.textContent='';
        tbody.querySelectorAll('[data-total]').forEach(cell=>{cell.textContent='—';});
      } finally {
        if(current===revision){summary.removeAttribute('aria-busy');summary.classList.remove('request-inline-busy');}
      }
    },120);
  }
  async function autosave() {
    if(submitting || saving || form.dataset.quote!=='1' || !lines.every(row=>row.description.trim()))return;
    saving=true;dirty=false;status.classList.add('request-inline-busy');status.textContent='Enregistrement…';
    try {
      const response=await fetch('/app/devis/brouillon/sauvegarder',{method:'POST',body:new FormData(form),headers:{Accept:'application/json'}});
      const data=await response.json();
      if(!response.ok)throw new Error(data.error||'Enregistrement impossible');
      form.elements.document_uid.value=data.document_uid;form.action=data.url;status.textContent='Brouillon enregistré';
    } catch(error){status.textContent=error.message;}
    finally {
      status.classList.remove('request-inline-busy');
      saving=false;
      if(pendingSubmit) { pendingSubmit=false; form.requestSubmit(); }
      else if(dirty&&!submitting) saveTimer=setTimeout(autosave,1000);
    }
  }
  function changed() {
    dirty=true;update();clearTimeout(saveTimer);
    if(form.dataset.quote==='1')saveTimer=setTimeout(autosave,1200);
  }
  form.addEventListener('input',changed);
  form.addEventListener('submit',event=>{
    hidden.value=JSON.stringify(lines);clearTimeout(saveTimer);
    if(saving){event.preventDefault();pendingSubmit=true;status.textContent='Finalisation de l’enregistrement…';return;}
    submitting=true;
  });
  document.getElementById('add-line').addEventListener('click',()=>{lines.push(blank());render();changed();});
  document.getElementById('inventory-select')?.addEventListener('change',event=>{
    if(event.target.value==='')return;
    const item=inventory[Number(event.target.value)];
    const row={...blank(),description:item.description||item.name,item_reference:item.reference||'',unit_code:item.unit_code,
      unit_price_excluding_tax:item.unit_price_excluding_tax,unit_price_including_tax:item.unit_price_including_tax??null,
      vat_rate:item.vat_rate,vat_category_code:item.vat_category_code||(Number(item.vat_rate)>0?'S':'Z')};
    if(lines.length===1&&!lines[0].description)lines[0]=row;else lines.push(row);
    event.target.value='';render();changed();
  });
  const reduction=form.elements.global_discount_value;
  reduction.value=editableNumber(reduction.value);
  render();
})();
