// Bank records used here belong to a disposable local instance.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');

(async () => {
  const base = process.env.TEST_BASE_URL;
  assert.equal(new URL(base).hostname, '127.0.0.1');
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({viewport: {width:390, height:844}});
    await page.goto(base + '/connexion');
    await page.getByLabel('Email', {exact:true}).fill('demo@example.test');
    await page.getByLabel('Mot de passe', {exact:true}).fill('DemoForBrowser123!');
    await page.getByRole('button', {name:'Se connecter', exact:true}).click();
    await page.waitForURL('**/app');
    await page.goto(base + '/app/finances/comptes');
    const account = page.locator('#compte-manuel');
    await account.locator('[name=name]').fill('Compte de relecture');
    await account.getByRole('button', {name:'Créer le compte'}).click();
    await page.getByRole('link', {name:'Importer des relevés', exact:true}).click();
    const form = page.locator('#releve form');
    await form.getByLabel('Compte de destination').selectOption({label:'Compte de relecture · EUR'});
    const source = await browser.newPage();
    await source.setContent('<html lang="fr"><body><pre>12/05/2026  Paie de test  -81,25</pre></body></html>');
    const bytes = await source.pdf();
    await source.close();
    await form.getByLabel('Fichiers des relevés').setInputFiles([
      {name:'releve.pdf', mimeType:'application/pdf', buffer:bytes},
      {name:'autre.csv', mimeType:'text/csv', buffer:Buffer.from('date;libelle;montant\n2026-05-13;Recette de test;2.00\n')}
    ]);
    await form.getByRole('button', {name:'Importer les relevés'}).click();
    await page.getByRole('heading', {name:'Suivi des relevés'}).waitFor();
    await page.getByText('autre.csv',{exact:true}).waitFor();
    await page.getByRole('link',{name:'Vérifier les opérations'}).click({timeout:60000});
    await page.getByRole('heading', {name:'Vérifier le relevé PDF'}).waitFor();
    const row = page.locator('[data-pdf-rows] fieldset').first();
    assert.equal(await row.getByLabel('Enregistrer cette ligne').isChecked(), false);
    assert.equal(await row.getByLabel('Sens', {exact:true}).inputValue(), 'outgoing');
    await row.getByLabel('Enregistrer cette ligne').check();
    await page.getByRole('button', {name:'Ajouter une ligne'}).click();
    assert.equal(await page.locator('[data-pdf-rows] [name=date_2]').count(), 1);
    for (const width of [320,390,768,1440]) {
      await page.setViewportSize({width,height:844});
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    }
    await page.getByRole('button', {name:'Confirmer les lignes cochées'}).click();
    await page.getByRole('link', {name:'Voir les transactions du compte'}).click();
    await page.getByRole('link', {name:/Sans facture liée/}).click();
    const line = page.getByRole('row').filter({hasText:'Paie de test'});
    await line.getByRole('link', {name:'Traiter'}).click();
    await page.getByLabel('Catégorie', {exact:true}).selectOption('salary');
    await page.getByLabel('Motif ou bénéficiaire').fill('Paie vérifiée en banque');
    await page.getByRole('button', {name:'Enregistrer le classement'}).click();
    await line.getByText('Salaires', {exact:true}).waitFor();
    await line.getByText('Paie vérifiée en banque').waitFor();
    await page.goto(base + '/app/finances/transactions#releve');
    await form.getByLabel('Compte de destination').selectOption({label:'Compte de relecture · EUR'});
    const padded = Buffer.concat([bytes, Buffer.alloc(4000000 - bytes.length, 32)]);
    const parts = [];
    page.on('request', request => {
      if (request.method() === 'POST' && new URL(request.url()).pathname.endsWith('/transactions/import')) parts.push(request.url());
    });
    await form.getByLabel('Fichiers des relevés').setInputFiles([1,2,3,4].map(i=>({
      name:'lot-'+i+'.pdf', mimeType:'application/pdf', buffer:padded
    })));
    await form.getByRole('button',{name:'Importer les relevés'}).click();
    await page.getByRole('heading',{name:'Suivi des relevés'}).waitFor();
    assert.equal(parts.length,2);
    assert.equal(await page.getByText('Fichier en double',{exact:true}).count(),3);
    console.log('Import multifichier, relecture PDF, classement, grands lots et responsive validés.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
