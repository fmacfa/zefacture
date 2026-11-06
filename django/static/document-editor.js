'use strict';
(() => {
  const form = document.getElementById('document-form');
  if (!form) return;
  const lines = JSON.parse(document.getElementById('initial-lines').textContent).map(row=>({...defaults(),...row,quantity:row.quantity??row.qty??'1',unit_price_excluding_tax:row.unit_price_excluding_tax??row.unit_price??'0',vat_category_code:row.vat_category_code||(Number(row.vat_rate??20)>0?'S':'Z')}));
  const inventory = JSON.parse(document.getElementById('inventory-data').textContent);
  const tbody = document.getElementById('line-rows');
  const hidden = document.getElementById('lines-json');
  const status = document.getElementById('save-status');
  let timer, inFlight = false, submitting = false, dirty = false;
  function defaults() { return {description:'',item_reference:'',quantity:'1',unit_code:'C62',unit_price_excluding_tax:'0',discount_percent:'0',vat_rate:'20',vat_category_code:'S'}; }
  function field(row, key, type, label) {
    const input = document.createElement('input'); input.type=type; input.value=row[key] ?? ''; input.setAttribute('aria-label',label);
    if (type==='number') { input.min=key==='quantity'?'0.0001':'0'; input.step='0.0001'; if(key==='vat_rate'||key==='discount_percent')input.max='100'; }
    if(key==='description')input.required=true;
    input.addEventListener('input',()=> { row[key]=input.value; if(key==='vat_rate') {row.vat_category_code=Number(input.value)>0?'S':row.vat_category_code==='S'?'Z':row.vat_category_code; input.closest('tr').querySelector('[data-category]').value=row.vat_category_code;} update();changed(); });
    return input;
  }
  function select(row,key,options) {
    const element=document.createElement('select'); element.setAttribute('aria-label',key==='unit_code'?'Unité':'Catégorie TVA');
    options.forEach(([value,label])=> {const opt=document.createElement('option'); opt.value=value;opt.textContent=label;opt.selected=value===row[key];element.append(opt);});
    element.addEventListener('change',()=> {row[key]=element.value;update();changed();});return element;
  }
  function render() {
    tbody.replaceChildren();
    lines.forEach((row,index)=> {
      const tr=document.createElement('tr');
      const td=()=> {const cell=document.createElement('td');tr.append(cell);return cell;};
      const name=td();name.append(field(row,'description','text','Désignation'));name.append(field(row,'item_reference','text','Référence'));
      td().append(field(row,'quantity','number','Quantité'));
      td().append(select(row,'unit_code',[['C62','Unité'],['HUR','Heure'],['DAY','Jour'],['MON','Mois'],['ANN','Année'],['KGM','Kilogramme'],['LTR','Litre'],['MTR','Mètre'],['MTK','m²'],['MTQ','m³'],['KWH','kWh'],['MIN','Minute'],['LS','Forfait'],['EA','Pièce']]));
      td().append(field(row,'unit_price_excluding_tax','number','Prix unitaire HT'));
      td().append(field(row,'discount_percent','number','Remise'));
      const tax=td();tax.append(field(row,'vat_rate','number','TVA'));
      const category=select(row,'vat_category_code',[['S','Normale'],['Z','Taux zéro'],['E','Exonérée'],['AE','Autoliquidation'],['K','Intra-UE'],['G','Export'],['O','Hors champ']]);category.dataset.category='1';tax.append(category);
      const total=td();total.dataset.total=index;
      const remove=document.createElement('button');remove.type='button';remove.className='text-button';remove.textContent='×';remove.setAttribute('aria-label','Supprimer la ligne '+(index+1));
      remove.addEventListener('click',()=> {if(lines.length>1){lines.splice(index,1);render();changed();}});td().append(remove);tbody.append(tr);
    });update();
  }
  function update() {
    hidden.value=JSON.stringify(lines);
    try {
      const result=ZeMoney.calculate(lines.map(row=>({quantity:row.quantity,price:row.unit_price_excluding_tax,discount:row.discount_percent,rate:row.vat_rate})));
      result.lines.forEach((line,index)=> {tbody.querySelector('[data-total="'+index+'"]').textContent=line.net.toLocaleString('fr-FR',{minimumFractionDigits:2,maximumFractionDigits:2});});
      document.getElementById('totals').textContent='HT '+result.net.toFixed(2)+' · TVA '+result.vat.toFixed(2)+' · TTC '+result.gross.toFixed(2)+' '+form.elements.currency_code.value;
    } catch(error) {document.getElementById('totals').textContent='Vérifiez les valeurs numériques.';}
  }
  async function autosave() {
    if (submitting||inFlight||form.dataset.quote!=='1'||!lines.every(row=>row.description.trim())) return;
    inFlight=true;dirty=false;status.textContent='Enregistrement…';
    try {
      const response=await fetch('/app/devis/brouillon/sauvegarder',{method:'POST',body:new FormData(form),headers:{Accept:'application/json'}});
      const data=await response.json();
      if(!response.ok)throw new Error(data.error||'Enregistrement impossible');
      form.elements.document_uid.value=data.document_uid;
      form.action=data.url;
      status.textContent='Brouillon enregistré';
    } catch(error) {status.textContent=error.message;} finally {inFlight=false;if(dirty&&!submitting)timer=setTimeout(autosave,1000);}
  }
  function changed() {dirty=true;clearTimeout(timer);if(form.dataset.quote==='1')timer=setTimeout(autosave,1200);}
  form.addEventListener('input',()=>{update();changed();});
  form.addEventListener('submit',event=> {update();clearTimeout(timer);if(inFlight){event.preventDefault();status.textContent='Attendez la fin de la sauvegarde puis réessayez.';return;}submitting=true;});
  document.getElementById('add-line').addEventListener('click',()=> {lines.push(defaults());render();changed();});
  document.getElementById('inventory-select')?.addEventListener('change',event=> {
    if(event.target.value==='')return;const item=inventory[Number(event.target.value)];
    const row={...defaults(),description:item.description||item.name,item_reference:item.reference||'',unit_code:item.unit_code,unit_price_excluding_tax:item.unit_price_excluding_tax,vat_rate:item.vat_rate,vat_category_code:Number(item.vat_rate)>0?'S':'Z'};
    if(lines.length===1&&!lines[0].description)lines[0]=row;else lines.push(row);render();changed();event.target.value='';
  });
  render();
})();
