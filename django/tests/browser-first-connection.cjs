/* Run against the disposable Compose instance before loading other fixtures. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const { mkdir, writeFile } = require('node:fs/promises');
const path = require('node:path');

const origin = process.env.TEST_BASE_URL;
const setupLink = process.env.TEST_SETUP_LINK;
const output = process.env.TEST_FIRST_CONNECTION_OUTPUT || 'test-results/browser-first-connection';

async function assertIdentity(page) {
  const brand = page.getByRole('link', { name: 'ZeFacture, accueil', exact: true });
  const mark = brand.locator('img');
  assert.match(await mark.getAttribute('src'), /\/zefacture-mark(?:\.[a-z0-9]+)?\.svg$/);
  assert.ok(await mark.evaluate(image => image.complete && image.naturalWidth > 0));
  assert.equal(await page.locator('link[rel=icon]').getAttribute('href'), await mark.getAttribute('src'));
  assert.equal(await page.evaluate(async () => (await document.fonts.load('16px "DM Sans"')).length), 1);
  assert.match(await page.locator('body').evaluate(body => getComputedStyle(body).fontFamily), /^"DM Sans"/);
}

async function run() {
  assert.ok(origin && /^https?:\/\/127\.0\.0\.1:\d+$/.test(origin), 'Test instance must be on loopback.');
  const installation = new URL(setupLink);
  assert.ok(installation.origin === origin && installation.pathname === '/connexion/installation' &&
    /^#installation=[A-Za-z0-9_-]{43}$/.test(installation.hash), 'A disposable setup link is required.');
  await mkdir(output, { recursive: true });
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    await page.goto(origin + '/source');
    await assertIdentity(page);
    const source = page.getByRole('link', { name: 'Télécharger le code source' });
    assert.equal(await source.evaluate(link => getComputedStyle(link).backgroundColor), 'rgb(227, 107, 67)');
    assert.equal(await source.getAttribute('href'), '/source/archive');
    assert.equal(await page.locator('.public-page').evaluate(card =>
      getComputedStyle(card).borderTopColor), 'rgb(227, 107, 67)');
    await page.getByRole('link', { name: 'Connexion', exact: true }).click();
    assert.equal(new URL(page.url()).pathname, '/connexion/installation');
    await page.getByRole('heading', { name: 'Créer le premier compte' }).waitFor();
    await assertIdentity(page);
    const submit = page.getByRole('button', { name: 'Créer mon espace' });
    assert.ok(await submit.isEnabled());
    assert.equal(await page.locator('.setup-story').evaluate(panel =>
      getComputedStyle(panel).backgroundColor), 'rgb(24, 60, 50)');
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert.equal(await page.locator('[name="setup_code"], [name="terms"]').count(), 0);
    assert.equal(await submit.evaluate(button => getComputedStyle(button).backgroundColor), 'rgb(227, 107, 67)');
    await page.screenshot({ path: path.join(output, 'first-connection-mobile.png'), fullPage: true });

    await page.setViewportSize({ width: 1440, height: 960 });
    await page.screenshot({ path: path.join(output, 'first-connection-desktop.png'), fullPage: true });
    const fields = [
      ['Nom de l’entreprise', 'Atelier Démonstration'],
      ['Votre nom', 'Camille Démonstration'],
      ['Adresse e-mail', 'demo@example.test'],
      ['Mot de passe', 'DemoForBrowser123!'],
    ];
    for (const [label, value] of fields) {
      const field = page.getByLabel(label, { exact: true });
      await field.click();
      await field.fill(value);
    }
    await submit.click();
    const status = page.getByRole('status');
    assert.match(await status.textContent(), /lien privé/);
    assert.ok(await status.evaluate(element => document.activeElement === element));

    await page.goto(setupLink);
    await page.waitForURL(origin + '/connexion/installation');
    await page.waitForFunction(() => document.querySelector('[data-setup-form]').dataset.setupAuthorized === 'true');
    assert.ok(await status.isHidden());
    for (const [label, value] of fields) {
      assert.equal(await page.getByLabel(label, { exact: true }).inputValue(), value);
    }
    const password = page.getByLabel('Mot de passe', { exact: true });
    const toggle = page.getByRole('button', { name: 'Afficher le mot de passe', exact: true });
    await toggle.click();
    assert.equal(await password.getAttribute('type'), 'text');
    await toggle.click();
    assert.equal(await password.getAttribute('type'), 'password');
    await Promise.all([
      page.waitForURL(origin + '/app'),
      submit.click(),
    ]);
    await page.getByRole('heading', { name: "Vue d'ensemble" }).waitFor();
    await assertIdentity(page);
    assert.equal(await page.locator('.sidebar').evaluate(sidebar =>
      getComputedStyle(sidebar).backgroundColor), 'rgb(24, 60, 50)');

    const visitor = await browser.newPage();
    await visitor.goto(origin + '/connexion/installation');
    assert.equal(new URL(visitor.url()).pathname, '/connexion');
    await visitor.getByRole('button', { name: 'Se connecter' }).waitFor();
    await assertIdentity(visitor);
    await writeFile(path.join(output, 'report.json'), JSON.stringify({
      ok: true,
      checks: ['shared-identity', 'editable-before-authorization', 'private-link', 'mobile-layout', 'owner-created', 'setup-closed'],
    }, null, 2));
    console.log('First connection passed on the disposable instance.');
  } finally {
    await browser.close();
  }
}

run().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
