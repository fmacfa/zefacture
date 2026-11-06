'use strict';
(() => {
  const dialog=document.getElementById('quick-customer');
  if(!dialog)return;
  document.getElementById('new-customer').addEventListener('click',()=>dialog.showModal());
  dialog.querySelector('[data-close]').addEventListener('click',()=>dialog.close());
  const form=dialog.querySelector('form');
  form.addEventListener('submit',async event=>{
    event.preventDefault();
    const button=form.querySelector('[type=submit]');button.disabled=true;
    const error=dialog.querySelector('[role=alert]');error.hidden=true;
    try {
      const response=await fetch('/app/devis/nouveau/client',{method:'POST',body:new FormData(form),headers:{Accept:'application/json'}});
      const data=await response.json();if(!response.ok)throw new Error(data.error||'Création impossible');
      const select=document.querySelector('#document-form select[name=customer_uid]');
      const option=new Option(data.legal_name,data.customer_uid,true,true);select.add(option);
      select.dispatchEvent(new Event('input',{bubbles:true}));dialog.close();form.reset();
    }catch(problem){error.textContent=problem.message;error.hidden=false;}finally{button.disabled=false;}
  });
})();
