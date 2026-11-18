/* Run against the disposable Compose instance before loading other fixtures. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const { mkdir, writeFile } = require('node:fs/promises');
const path = require('node:path');

const origin = process.env.TEST_BASE_URL;
const setupLink = process.env.TEST_SETUP_LINK;
const output = process.env.TEST_FIRST_CONNECTION_OUTPUT || 'test-results/browser-first-connection';

async function run() {
  assert.ok(origin && /^https?:\/\/127\.0\.0\.1:\d+$/.test(origin), 'Test instance must be on loopback.');
  const installation = new URL(setupLink);
  assert.ok(installation.origin === origin && installation.pathname === '/connexion/installation' &&
    /^#installation=[A-Za-z0-9_-]{43}$/.test(installation.hash), 'A disposable setup link is required.');
  await mkdir(output, { recursive: true });
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    await page.goto(origin + '/connexion');
    assert.equal(new URL(page.url()).pathname, '/connexion/installation');
    await page.getByRole('heading', { name: 'Créer le premier compte' }).waitFor();
    assert.ok(await page.getByRole('button', { name: 'Créer mon espace' }).isDisabled());
    await page.goto(setupLink);
    await page.waitForURL(origin + '/connexion/installation');
    await page.waitForFunction(() => !document.querySelector('fieldset').disabled);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert.equal(await page.locator('[name="setup_code"], [name="terms"]').count(), 0);
    await page.screenshot({ path: path.join(output, 'first-connection-mobile.png'), fullPage: true });

    await page.setViewportSize({ width: 1440, height: 960 });
    await page.screenshot({ path: path.join(output, 'first-connection-desktop.png'), fullPage: true });
    await page.getByLabel('Adresse e-mail', { exact: true }).fill('demo@example.test');
    await page.getByLabel('Votre nom', { exact: true }).fill('Camille Démonstration');
    await page.getByLabel('Nom de l’entreprise', { exact: true }).fill('Atelier Démonstration');
    const password = page.getByLabel('Mot de passe', { exact: true });
    const toggle = page.getByRole('button', { name: 'Afficher le mot de passe', exact: true });
    await toggle.click();
    assert.equal(await password.getAttribute('type'), 'text');
    await toggle.click();
    assert.equal(await password.getAttribute('type'), 'password');
    await password.fill('DemoForBrowser123!');
    await Promise.all([
      page.waitForURL(origin + '/app'),
      page.getByRole('button', { name: 'Créer mon espace' }).click(),
    ]);
    await page.getByRole('heading', { name: "Vue d'ensemble" }).waitFor();

    const visitor = await browser.newPage();
    await visitor.goto(origin + '/connexion/installation');
    assert.equal(new URL(visitor.url()).pathname, '/connexion');
    await visitor.getByRole('button', { name: 'Se connecter' }).waitFor();
    await writeFile(path.join(output, 'report.json'), JSON.stringify({
      ok: true,
      checks: ['first-connection-redirect', 'private-link', 'mobile-layout', 'owner-created', 'setup-closed'],
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
