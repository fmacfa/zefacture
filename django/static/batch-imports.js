'use strict';
(() => {
  const form=document.querySelector('[data-batch-upload]');
  if (!form || !window.crypto?.randomUUID) return;
  const input=form.elements.file, type=form.elements.source_kind;
  const list=form.querySelector('[data-batch-files]'), status=form.querySelector('[data-batch-progress]'), link=form.querySelector('[data-batch-link]');
  let entries=[], batch='', running=false;
  function prepare() {
    list.replaceChildren(); link.hidden=true; status.textContent='';
    batch=crypto.randomUUID().replaceAll('-','');
    entries=Array.from(input.files).map(file => {
      const line=document.createElement('li'), name=document.createElement('strong'), label=document.createElement('label'), result=document.createElement('span');
      name.textContent=file.name;
      const choice=type.cloneNode(true);
      choice.removeAttribute('name'); choice.value=type.value;
      choice.setAttribute('aria-label','Type de '+file.name);
      label.append('Type de document',choice);
      if (input.files.length>1) { line.append(name,label,result); list.append(line); }
      return {file,choice,result,done:false};
    });
  }
  input.addEventListener('change',prepare);
  type.addEventListener('change',()=>entries.forEach(entry=>{entry.choice.value=type.value;}));
  form.addEventListener('submit',async event=>{
    if(entries.length<2) return;
    event.preventDefault();
    if(running) return;
    if(entries.length>50){status.textContent='Sélectionnez au maximum 50 fichiers par lot.';return;}
    running=true;
    const button=form.querySelector('button[type=submit],button:not([type])');
    const pending=window.ZeRequests.start(form,button,"Import des documents en cours…");
    button.disabled=true; input.disabled=true; type.disabled=true;
    for(const entry of entries) entry.choice.disabled=true;
    try {
      for(const [index,entry] of entries.entries()){
        if(entry.done) continue;
        status.textContent='Import du fichier '+(index+1)+' sur '+entries.length+'.';
        const data=new FormData(form);
        data.set('file',entry.file); data.set('source_kind',entry.choice.value); data.set('batch_uid',batch);
        try {
          const response=await fetch(form.action,{method:'POST',body:data,headers:{Accept:'application/json'},credentials:'same-origin'});
          if(!response.headers.get('Content-Type')?.includes('application/json')) throw new Error('Connexion interrompue ou fichier trop volumineux.');
          const body=await response.json();
          if(!response.ok) throw new Error(body.error||'Échec de l’import.');
          const row=body.files[0], anchor=document.createElement('a');
          anchor.href=row.url; anchor.textContent=row.label;
          if (row.state==='duplicate') anchor.classList.add('duplicate-badge');
          entry.result.replaceChildren(anchor);
          entry.done=true;
        } catch(error) {
          entry.result.textContent='À reprendre · '+error.message;
        }
      }
      const done=entries.filter(entry=>entry.done).length;
      status.textContent=done+' sur '+entries.length+' traités.'+(done<entries.length?' Cliquez sur Importer pour reprendre les fichiers en erreur.':'');
      link.href='/app/imports?lot='+batch; link.hidden=false;
    } finally {
      pending.finish();
      running=false;button.disabled=false;input.disabled=false;type.disabled=false;
      for(const entry of entries) entry.choice.disabled=false;
    }
  });
})();
