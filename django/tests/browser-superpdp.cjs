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
    await page.getByRole('heading', {name: 'Factures reçues via SUPERPDP'}).waitFor();
    const row = page.getByRole('row').filter({hasText: 'FOURNISSEUR-SUPERPDP-001'});
    const zipForm = page.locator('form.reception-filters');
    await zipForm.getByLabel('Du', {exact: true}).fill('2026-11-01');
    await zipForm.getByLabel('Au', {exact: true}).fill('2026-11-30');
    for (const [format, suffix] of [['pdf', 'pdf'], ['xml', 'xml'], ['both', 'pdf-xml']]) {
      await zipForm.getByLabel('Format du ZIP').selectOption(format);
      const [zip] = await Promise.all([page.waitForEvent('download'), zipForm.getByRole('button', {name: 'Télécharger le ZIP'}).click()]);
      assert.equal(zip.suggestedFilename(), `factures-fournisseurs_2026-11-01_2026-11-30_${suffix}.zip`);
      const bytes = await fs.readFile(await zip.path());
      assert.equal(bytes.readUInt32LE(0), 0x04034b50);
      assert.equal(bytes.includes(Buffer.from('PDF/')), format !== 'xml');
      assert.equal(bytes.includes(Buffer.from('XML/')), format !== 'pdf');
    }
    for (const [label, filename] of [['Original XML', 'fournisseur-superpdp-001-original.xml'], ['PDF de lecture', 'fournisseur-superpdp-001-lecture.pdf']]) {
      const [file] = await Promise.all([page.waitForEvent('download'), row.getByRole('link', {name: label, exact: true}).click()]);
      assert.equal(file.suggestedFilename(), filename);
      const bytes = await fs.readFile(await file.path());
      if (label === 'Original XML') assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'), fixture.incoming_original_sha256);
      else assert.equal(bytes.subarray(0, 5).toString(), '%PDF-');
    }
    await page.getByRole('heading', {name: 'Totaux de la sélection'}).waitFor();
    await row.getByRole('link', {name: 'Aperçu', exact: true}).click();
    const raster = page.getByRole('img', {name: /Page 1 sur/});
    await raster.evaluate(img => img.decode());
    await page.getByRole('button', {name: 'Agrandir le zoom'}).click();
    assert.equal(await page.locator('.reader-zoom output').textContent(), '125 %');
    await page.getByRole('button', {name: 'Ajuster à la largeur'}).click();
    assert.equal(await page.locator('.reader-zoom output').textContent(), '100 %');
    const readerUrl = page.url();
    await page.goto(base + '/app/reception');
    await row.getByRole('link', {name: 'Voir la facture', exact: true}).click();
    await page.getByRole('heading', {name: 'Suivi SUPERPDP', exact: true}).waitFor();
    await page.getByText('Encaissement déclaré', {exact: true}).waitFor();
    const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('link', {name: 'Télécharger l’original SUPERPDP', exact: true}).click()]);
    const originalPath = path.join(output, 'original-superpdp.xml');
    await download.saveAs(originalPath);
    const raw = await fs.readFile(originalPath);
    assert.equal(crypto.createHash('sha256').update(raw).digest('hex'), fixture.incoming_original_sha256);
    assert.equal(await page.getByRole('link', {name: 'Télécharger la version PDF', exact: true}).count(), 1);
    await page.getByRole('link', {name: 'Créer un nouveau contact', exact: true}).click();
    await page.getByLabel('Nom du contact').fill('Camille Suivi');
    await page.getByRole('button', {name: 'Créer et lier le contact'}).click();
    await page.getByRole('link', {name: 'Camille Suivi'}).waitFor();
    await page.getByRole('link', {name: 'Modifier le contact'}).click();
    assert.equal(await page.getByLabel('Nom du contact').inputValue(), 'Camille Suivi');
    await page.getByLabel('Fonction (facultatif)').fill('Comptabilité');
    await page.getByRole('button', {name: 'Enregistrer les coordonnées'}).click();
    await page.locator('#contact-crm').getByText(/Comptabilité/).first().waitFor();
    await page.getByLabel('Nouvelle note').fill('Suivi interne de démonstration');
    await page.getByRole('button', {name: 'Ajouter la note'}).click();
    await page.getByText('Suivi interne de démonstration', {exact: true}).first().waitFor();
    await page.goto(base + '/app/imports');
    await page.getByLabel('Fichiers', {exact: true}).setInputFiles({
      name: 'scan-exemple.tiff', mimeType: 'image/tiff', buffer: Buffer.from(fixture.two_page_scan, 'base64'),
    });
    await page.getByRole('button', {name: 'Importer', exact: true}).click();
    await page.getByRole('link', {name: 'Aperçu', exact: true}).click();
    await page.getByRole('link', {name: 'Page suivante'}).click();
    const scan = page.getByRole('img', {name: /Page 2 sur 2/});
    await scan.evaluate(img => img.decode());
    assert.deepEqual(await scan.evaluate(img => [img.naturalWidth, img.naturalHeight]), [48, 32]);
    await page.getByRole('link', {name: 'Page précédente'}).click();
    await page.getByRole('img', {name: /Page 1 sur 2/}).evaluate(img => img.decode());
    await page.goto(base + '/app?preset=all');
    await page.getByRole('navigation', {name: 'Factures fournisseurs par statut'}).getByRole('link', {name: /^Payées/}).click();
    assert.equal(new URL(page.url()).searchParams.get('status'), 'paid');
    await page.goto(base + '/app/finances/comptes');
    await page.getByLabel('Client ID', {exact: true}).fill('browser-demo');
    await page.getByLabel('Client Secret', {exact: true}).fill('test-bank-credential');
    await page.getByRole('button', {name: 'Enregistrer les paramètres'}).click();
    await page.getByRole('button', {name: 'Relier ma banque', exact: true}).waitFor();
    await page.getByText('Modifier les paramètres Bridge', {exact: true}).click();
    assert.equal(await page.locator('[name="client_secret"]').inputValue(), '');
    await page.getByRole('link', {name: 'Voir les transactions'}).click();
    await page.getByLabel('Rechercher une opération').fill('Virement test');
    await page.getByRole('button', {name: 'Filtrer', exact: true}).click();
    assert.equal(new URL(page.url()).searchParams.get('q'), 'Virement test');
    await page.goto(base + '/app/compte');
    const secret = page.locator('input[name="password"]');
    await secret.fill('abc');
    await page.getByText(/Faible. Le minimum/).waitFor();
    await secret.fill('Nuages rouges Trois Falaises 482!');
    await page.getByText('Très robuste.', {exact:true}).waitFor();
    await secret.fill('');
    await page.goto(base + '/app/compte/affichage');
    await page.getByLabel('Séparateur des milliers').selectOption('dot');
    assert.equal(await page.locator('#number-preview').textContent(), '1.234.567,89');
    await page.getByRole('button', {name:'Enregistrer les préférences'}).click();
    await page.goto(base + '/app/finances/transactions');
    const transaction = page.locator('form').filter({has:page.getByRole('heading',{name:'Nouvelle transaction'})});
    await transaction.getByLabel('Montant',{exact:true}).fill('1.234,56');
    assert.equal(await transaction.locator('input[name=amount]').inputValue(),'1234.56');
    await transaction.getByLabel('Libellé').fill('Règlement de test');
    await transaction.getByRole('button',{name:'Enregistrer',exact:true}).click();
    await page.getByText('1.234,56 EUR',{exact:true}).waitFor();
    await page.goto(base + '/app/imports');
    await page.locator('[name=pipeline]').selectOption('none');
    await page.getByLabel('Fichiers',{exact:true}).setInputFiles([
      {name:'copie-test.tiff',mimeType:'image/tiff',buffer:Buffer.from(fixture.two_page_scan,'base64')},
      {name:'vente-test.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.7 test navigateur lot')},
    ]);
    await page.getByLabel('Type de vente-test.pdf').selectOption('sales_invoice');
    await page.getByRole('button',{name:'Importer',exact:true}).click();
    await page.getByText('Doublon exact · original déjà importé',{exact:true}).waitFor();
    await page.getByRole('link',{name:'Voir les documents de ce lot'}).click();
    await page.getByRole('link',{name:'vente-test.pdf'}).click();
    await page.getByLabel('Nom du client').waitFor();
    const checks = [];
    for (const width of [320, 390, 768, 1440]) {
      await page.setViewportSize({width, height: width < 700 ? 844 : 1000});
      for (const [name, route] of [['bank-accounts', '/app/finances/comptes'], ['bank-transactions', '/app/finances/transactions'], ['dashboard', '/app?preset=all'], ['connection', '/app/entreprise/pdp'], ['inbox', '/app/reception'], ['reader', new URL(readerUrl).pathname], ['incoming', '/app/reception/' + fixture.incoming_uid], ['outgoing', '/app/factures/' + fixture.outgoing_uid]]) {
        await page.goto(base + route);
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `Overflow ${route} at ${width}`);
        checks.push({width, route, ok: true});
        if (width === 390 || width === 1440) await page.screenshot({path: path.join(output, `${name}-${width}.png`), fullPage: true});
      }
    }
    const readyInbox = await (await page.goto(base + '/app/reception')).text();
    await page.getByRole('searchbox', {name: 'Rechercher'}).fill('FOURNISSEUR-SUPERPDP-001');
    await page.getByRole('button', {name: 'Filtrer', exact: true}).click();
    await page.getByRole('button', {name: 'Synchroniser SUPERPDP', exact: true}).click();
    await page.getByRole('link', {name: 'Actualiser la liste', exact: true}).waitFor();
    assert.equal(new URL(page.url()).searchParams.get('q'), 'FOURNISSEUR-SUPERPDP-001');
    // The CI worker is stopped here. Replay its completed, server-rendered inbox for the poll.
    await page.route(page.url(), route => route.fulfill({contentType: 'text/html', body: readyInbox}), {times: 1});
    await page.getByRole('searchbox', {name: 'Rechercher'}).fill('filtre en cours de saisie');
    await page.getByRole('button', {name: 'Synchroniser SUPERPDP', exact: true}).waitFor();
    assert.equal(await page.getByRole('searchbox', {name: 'Rechercher'}).inputValue(), 'filtre en cours de saisie');
    await page.goto(base + '/app/entreprise/pdp');
    await page.getByRole('button', {name: 'Synchroniser maintenant', exact: true}).click();
    await page.getByText('Une synchronisation attend le worker.', {exact: true}).waitFor();
    assert.equal(errors.length, 0, errors.join('\n'));
    await fs.writeFile(path.join(output, 'report.json'), JSON.stringify({ok: true, synthetic_data_only: true, flows: ['incoming_invoice', 'provider_statuses', 'original_xml_sha256', 'readable_pdf_download', 'outgoing_invoice', 'uncertain_submission_display', 'manual_sync_request', 'inbox_refresh_preserves_filters', 'pdf_zoom', 'multipage_scan', 'invoice_contact', 'internal_note', 'dashboard_status_filter', 'bank_settings', 'bank_transactions_filter', 'password_strength', 'number_settings', 'mixed_import_batch'], responsive: checks, javascript_and_csp_errors: errors}, null, 2));
  } finally { await browser.close(); }
  console.log('SUPERPDP : originaux, statuts, reprise et responsive OK.');
})().catch(error => { console.error(error); process.exit(1); });
