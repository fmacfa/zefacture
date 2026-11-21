/* The CI runner provides a disposable local app seeded by browser_fixture.py. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:8091';
assert.ok(['localhost','127.0.0.1'].includes(new URL(base).hostname));

(async () => {
  const output = path.resolve(process.env.TEST_OUTPUT || 'test-results/browser-commercial');
  await fs.mkdir(output, {recursive:true});
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({viewport:{width:1280,height:900}});
    const errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto(base+'/connexion');
    await page.getByLabel('Email',{exact:true}).fill('demo@example.test');
    await page.getByLabel('Mot de passe',{exact:true}).fill('DemoForBrowser123!');
    await Promise.all([page.waitForURL('**/app'),page.getByRole('button',{name:'Se connecter',exact:true}).click()]);
    await page.goto(base+'/app/devis/nouveau');
    const row=page.locator('#line-rows tr').first();
    await row.getByLabel('Désignation',{exact:true}).fill('Visite technique de démonstration');
    await row.getByLabel('Référence',{exact:true}).fill('DEMO-TTC-'+Date.now());
    await row.getByLabel('Mode de saisie du prix').selectOption('gross');
    await row.getByLabel('Prix unitaire TTC').fill('120');
    await page.getByRole('button',{name:'+ Ajouter une ligne'}).click();
    const second=page.locator('#line-rows tr').nth(1);
    await second.getByLabel('Désignation',{exact:true}).fill('Service de démonstration');
    await second.getByLabel('Prix unitaire HT').fill('100');
    await second.getByLabel('TVA',{exact:true}).fill('10');
    await page.getByLabel('Valeur de la remise').fill('10');
    await page.waitForFunction(()=>/TTC 207[,.]00/.test(document.querySelector('#totals').textContent));
    await row.getByRole('button',{name:'Ajouter au catalogue'}).click();
    const dialog=page.getByRole('dialog',{name:'Ajouter au catalogue'});
    await dialog.getByRole('button',{name:'Ajouter au catalogue'}).click();
    await dialog.waitFor({state:'hidden'});
    await row.getByRole('button',{name:'Ajouter au catalogue'}).click();
    await dialog.getByRole('button',{name:'Ajouter au catalogue'}).click();
    await dialog.getByRole('button',{name:'Utiliser cet article'}).click();
    await page.waitForFunction(()=>/^[a-f0-9]{32}$/.test(document.querySelector('[name=document_uid]').value));
    await page.getByRole('button',{name:'Enregistrer le document'}).click();
    await page.waitForURL(/\/app\/devis\/[0-9a-f]{32}$/);
    await page.goto(page.url()+'/modifier');
    assert.equal(await row.getByLabel('Mode de saisie du prix').inputValue(),'gross');
    await page.waitForFunction(()=>/TTC 207[,.]00/.test(document.querySelector('#totals').textContent));
    for(const width of [1280,390]){
      await page.setViewportSize({width,height:844});
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'editor overflows');
      if(width===390) assert.ok((await row.getByLabel('Prix unitaire TTC').boundingBox()).width>100,'mobile price field too narrow');
      await page.locator('#document-form .line-editor').scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(output,'editor-'+width+'.png')});
    }
    await page.goto(base+'/app/factures?suivi=overdue');
    await page.getByLabel('Sélectionner DEMO-RELANCE',{exact:true}).check();
    await page.getByRole('button',{name:'Préparer les relances sélectionnées'}).click();
    await page.getByRole('heading',{name:'Vérifier les relances'}).waitFor();
    assert.match(await page.locator('.email-body').innerText(),/800[,.]00 EUR/);
    await page.screenshot({path:path.join(output,'reminder-review.png')});
    await page.getByRole('button',{name:'Confirmer l’envoi de 1 relance'}).click();
    await page.getByText('Mise en file d’attente',{exact:true}).waitFor();
    assert.deepEqual(errors,[]);
    console.log('Prix TTC, remise, catalogue, réouverture et relance du solde vérifiés.');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
