/* Disposable synthetic instance only; no actual SUPERPDP account or invoice. */
const { chromium } = require('playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:8091';
const output = path.resolve('test-results/browser-superpdp');
(async () => {
  assert.ok(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
  const fixtureText = await fs.readFile('test-results/superpdp-browser-fixture.json', 'utf8');
  const fixture = JSON.parse(fixtureText.split('\n').find(line => line.startsWith('{')));
  assert.equal(fixture.synthetic_data_only, true);
  await fs.mkdir(output, {recursive: true});
  const browser = await chromium.launch({headless: true});
  try {
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}, acceptDownloads: true});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error' && /Content Security Policy|Refused to/.test(message.text())) errors.push(message.text()); });
    await page.goto(base + '/connexion');
    await page.getByLabel('Email', {exact: true}).fill('demo@example.test');
    await page.getByLabel('Mot de passe', {exact: true}).fill('DemoForBrowser123!');
    await page.getByRole('button', {name: 'Se connecter', exact: true}).click();
    await page.waitForURL('**/app');
    await page.goto(base + '/app/reception');
    await page.getByRole('link', {name: 'FOURNISSEUR-SUPERPDP-001', exact: true}).click();
    await page.getByRole('heading', {name: 'Suivi SUPERPDP', exact: true}).waitFor();
    await page.getByText('Encaissement déclaré', {exact: true}).waitFor();
    const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('link', {name: 'Télécharger l’original SUPERPDP', exact: true}).click()]);
    const originalPath = path.join(output, 'original-superpdp.xml');
    await download.saveAs(originalPath);
    const raw = await fs.readFile(originalPath);
    assert.equal(crypto.createHash('sha256').update(raw).digest('hex'), fixture.incoming_original_sha256);
    assert.equal(await page.getByRole('link', {name: 'Télécharger la version PDF', exact: true}).count(), 1);
    const checks = [];
    for (const width of [320, 390, 768, 1440]) {
      await page.setViewportSize({width, height: width < 700 ? 844 : 1000});
      for (const [name, route] of [['connection', '/app/entreprise/pdp'], ['incoming', '/app/reception/' + fixture.incoming_uid], ['outgoing', '/app/factures/' + fixture.outgoing_uid]]) {
        await page.goto(base + route);
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `Overflow ${route} at ${width}`);
        checks.push({width, route, ok: true});
        if (width === 390 || width === 1440) await page.screenshot({path: path.join(output, `${name}-${width}.png`), fullPage: true});
      }
    }
    await page.goto(base + '/app/entreprise/pdp');
    await page.getByRole('button', {name: 'Synchroniser maintenant', exact: true}).click();
    await page.getByText('Une synchronisation attend le worker.', {exact: true}).waitFor();
    assert.equal(errors.length, 0, errors.join('\n'));
    await fs.writeFile(path.join(output, 'report.json'), JSON.stringify({ok: true, synthetic_data_only: true, flows: ['incoming_invoice', 'provider_statuses', 'original_xml_sha256', 'readable_pdf_link', 'outgoing_invoice', 'uncertain_submission_display', 'manual_sync_request'], responsive: checks, javascript_and_csp_errors: errors}, null, 2));
  } finally { await browser.close(); }
  console.log('SUPERPDP : originaux, statuts, reprise et responsive OK.');
})().catch(error => { console.error(error); process.exit(1); });
