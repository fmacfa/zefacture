const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const base=process.env.TEST_BASE_URL;
(async()=>{
  assert.equal(new URL(base).hostname,'127.0.0.1');
  const browser=await chromium.launch();
  try{
    const page=await browser.newPage({viewport:{width:390,height:844}});
    await page.goto(base+'/connexion');
    await page.getByLabel('Email',{exact:true}).fill('demo@example.test');
    await page.getByLabel('Mot de passe',{exact:true}).fill('DemoForBrowser123!');
    await page.getByRole('button',{name:'Se connecter',exact:true}).click();
    await page.waitForURL('**/app');
    await page.goto(base+'/app/entreprise/ia');
    const google=page.locator('#google');
    const key=crypto.randomBytes(18).toString('hex');
    await google.getByLabel('Clé API',{exact:true}).fill(key);
    await google.getByRole('button',{name:'Enregistrer la connexion'}).click();
    await google.getByText('Clé de l’entreprise',{exact:true}).waitFor();
    assert.ok(!(await page.content()).includes(key));
    await google.getByText('Modifier la connexion',{exact:true}).click();
    assert.equal(await google.getByLabel('Nouvelle clé API').inputValue(),'');
    await page.goto(base+'/app/imports');
    assert.equal(await page.locator('select[name=pipeline]').inputValue(),'google');
    await page.getByText(/Google Gemini reçoit le texte OCR de chaque fichier/).waitFor();
    await page.goto(base+'/app/notes-de-frais');
    await page.getByRole('link',{name:'Ouvrir le dernier lot importé'}).click();
    await page.getByRole('button',{name:'Analyser 2 fichiers avec Google Gemini'}).waitFor();
    await page.goto(base+'/app/inventaire/factures?q=SUP-BROWSER');
    await page.getByRole('link',{name:/^SUP-BROWSER/}).first().click();
    const choice=page.locator('select[name^=line_]').first();
    await choice.selectOption('new');
    await page.getByRole('button',{name:'Enregistrer les entrées'}).click();
    await page.getByText(/Déjà enregistré/).first().waitFor();
    await page.locator('fieldset .notice a').first().click();
    await page.getByRole('heading',{name:'Acheté sur facture'}).waitFor();
    await page.getByRole('link',{name:'Enregistrer une consommation'}).click();
    assert.equal(await page.locator('select[name=movement_type]').inputValue(),'out');
    for(const width of [320,390,768,1440]){
      await page.setViewportSize({width,height:900});
      for(const route of ['/app/entreprise/ia','/app/inventaire','/app/inventaire/factures']){
        await page.goto(base+route);
        assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth+1),route+' '+width);
      }
    }
    console.log('Clé IA masquée et stock depuis une facture vérifiés sur quatre largeurs.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error.message);process.exitCode=1;});
