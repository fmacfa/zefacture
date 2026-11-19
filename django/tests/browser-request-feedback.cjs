// Use a delayed synthetic provider response on the disposable browser fixture.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');

(async () => {
  const base = process.env.TEST_BASE_URL;
  assert.equal(new URL(base).hostname, '127.0.0.1');
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({viewport:{width:390,height:844}, reducedMotion:'reduce'});
    await page.goto(base+'/connexion');
    await page.getByLabel('Email', {exact:true}).fill('demo@example.test');
    await page.getByLabel('Mot de passe', {exact:true}).fill('DemoForBrowser123!');
    await page.getByRole('button', {name:'Se connecter',exact:true}).click();
    await page.waitForURL('**/app');
    await page.goto(base+'/app/entreprise/ia#google');
    const status = page.locator('[data-request-status]');
    const card = page.locator('#google');
    const button = card.getByRole('button',{name:'Tester avec un ticket fictif'});
    let calls = 0;
    let respond;
    await page.route('**/app/entreprise/ia', async route => {
      if (route.request().method() !== 'POST') return route.continue();
      calls++;
      assert.equal(route.request().headers().accept, 'application/json');
      assert.match(route.request().postData(), /name="action"\r?\n\r?\ntest/);
      await new Promise(resolve => { respond = resolve; });
      await route.fulfill({status:calls === 1 ? 200 : 502, contentType:'application/json',
        body:JSON.stringify(calls === 1 ? {message:'Connexion de test réussie.'} : {error:'Fournisseur de test indisponible.'})});
    });
    for (const expected of ['Connexion de test réussie.', 'Fournisseur de test indisponible.']) {
      const expectedCalls = calls + 1;
      respond = null;
      await button.click();
      await status.waitFor({state:'visible'});
      assert.match(await status.innerText(), /Google Gemini/);
      assert.equal(await button.getAttribute('aria-disabled'),'true');
      assert.equal(await status.locator('.request-spinner').evaluate(el=>getComputedStyle(el).animationName),'none');
      await button.evaluate(b=>b.form.requestSubmit(b));
      for (let tries=0; !respond && tries<100; tries++) await page.waitForTimeout(20);
      assert.ok(respond,'Le fournisseur doit avoir reçu le test.');
      assert.equal(calls,expectedCalls);
      respond();
      await card.getByText(expected,{exact:true}).waitFor();
      await status.waitFor({state:'hidden'});
      assert.equal(await button.isEnabled(),true);
      assert.match(page.url(), /#google$/);
    }
    page.once('dialog',dialog=>dialog.dismiss());
    await card.getByRole('button',{name:'Désactiver et retirer la clé'}).click();
    assert.equal(await status.isVisible(),false);
    await page.goto(base+'/app/finances/transactions');
    const exportLink = page.getByRole('link',{name:'Exporter le CSV'});
    await page.evaluate(() => {
      const status = document.querySelector('[data-request-status]');
      new MutationObserver(() => {
        if (!status.hidden && status.textContent.includes('téléchargement')) window.sawDownloadPending = true;
      }).observe(status, {attributes:true, childList:true, subtree:true});
    });
    await page.route(/\/app\/finances\/transactions\/export\?/, async route => {
      assert.match(new URL(route.request().url()).searchParams.get('_zf_download'), /^[a-f0-9]{32}$/);
      await new Promise(resolve => setTimeout(resolve,350));
      await route.continue();
    });
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      exportLink.click({noWaitAfter:true}),
    ]);
    assert.equal(download.suggestedFilename(),'transactions.csv');
    assert.equal(await page.evaluate(()=>window.sawDownloadPending),true);
    await status.waitFor({state:'hidden',timeout:5000});
    assert.ok(!(await exportLink.getAttribute('href')).includes('_zf_download'));
    assert.equal(await exportLink.isEnabled(),true);
    console.log('Indicateur du test Google, double clic, erreur, confirmation annulée et téléchargement natif validés.');
  } finally {
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
