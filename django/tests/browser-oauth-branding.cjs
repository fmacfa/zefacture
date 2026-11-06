/* Only the isolated synthetic browser fixture. No real provider is contacted. */
const { chromium } = require('playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:8091';
const output = path.resolve('test-results/browser-oauth');
function totp(secret) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  const bits = [...secret].map(c => alphabet.indexOf(c).toString(2).padStart(5, '0')).join('');
  const key = Buffer.from((bits.match(/.{8}/g) || []).map(b => parseInt(b, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000)));
  const hash = crypto.createHmac('sha1', key).update(counter).digest();
  return ((hash.readUInt32BE(hash[19] & 15) & 0x7fffffff) % 1000000).toString().padStart(6, '0');
}
(async () => {
  assert.ok(['127.0.0.1', 'localhost'].includes(new URL(base).hostname), 'Synthetic local instance required');
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', message => { if (message.type() === 'error' && /Content Security Policy|Refused to/.test(message.text())) errors.push(message.text()); });
    async function passwordLogin() {
      await page.goto(base + '/connexion');
      await page.getByLabel('Email', { exact: true }).fill('demo@example.test');
      await page.getByLabel('Mot de passe', { exact: true }).fill('DemoForBrowser123!');
      await page.getByRole('button', { name: 'Se connecter', exact: true }).click();
    }
    await passwordLogin();
    await page.waitForURL('**/app');
    assert.match(await page.locator('.company-switch').textContent(), /Atelier Démonstration/);
    await page.goto(base + '/app/entreprise/personnalisation');
    await page.getByLabel('Nom d’affichage', { exact: true }).fill('Atelier Studio');
    await page.getByLabel('Couleur principale', { exact: true }).fill('#74245c');
    await page.getByLabel('Présentation des documents', { exact: true }).selectOption('compact');
    await page.getByLabel('Devise par défaut', { exact: true }).fill('CHF');
    await page.getByLabel('Logo de l’entreprise', { exact: true }).setInputFiles({name:'demo.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAs0lEQVR4nO3awQ2CUBAG4WFCKdqVdiFVUIZl2YwNeMAIwQl8583LJnv48zY7PC43yiRO4iRO4iRO4iRO4iRO4iRO4iRu/Kp6fj23aGK63o87AYmTOImTOImTODlUkG1k/pSPC9Nt3CtT1wp1iZM4iZM4iZM4iZM4iZM4iZM4iZM4iZO4cd3nNto9/vuXcjp3o2ESJ3ESJ3ESJ3HDeXK2M4mTOImTOImTOImTOImTOPdu4FdvS60OcfCAoMoAAAAASUVORK5CYII=','base64')});
    await page.getByLabel('Adresse de réponse', { exact: true }).fill('contact@example.test');
    await page.getByLabel('Signature des emails', { exact: true }).fill('Merci,\nAtelier Studio — données fictives');
    await page.getByRole('button', { name: 'Enregistrer la personnalisation', exact: true }).click();
    await page.getByText('Personnalisation enregistrée pour cette entreprise.', { exact: false }).waitFor();
    await page.screenshot({ path: path.join(output, 'branding-desktop.png'), fullPage: true });
    await page.goto(base + '/app/devis/nouveau');
    assert.equal(await page.getByLabel('Devise', { exact: true }).inputValue(), 'CHF');
    await page.locator('select[name=customer_uid]').selectOption({ label: 'Maison des Exemples · FR' });
    await page.getByLabel('Objet', { exact: true }).fill('Document personnalisé fictif');
    await page.getByLabel('Désignation', { exact: true }).fill('Prestation de démonstration');
    await page.getByLabel('Prix unitaire HT', { exact: true }).fill('100');
    await page.getByRole('button', { name: 'Enregistrer le document', exact: true }).click();
    await page.waitForURL(/\/app\/devis\/[a-f0-9]{32}$/);
    const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('link', { name: 'Télécharger le PDF', exact: true }).click()]);
    await download.saveAs(path.join(output, 'branded-quote.pdf'));
    await page.goto(base + '/app/emails/connexion/guide');
    assert.equal(await page.getByLabel('Retour Google pour cette instance', { exact: true }).inputValue(), base + '/app/emails/connexion/google/retour');
    await page.screenshot({ path: path.join(output, 'oauth-guide-desktop.png'), fullPage: true });
    await page.goto(base + '/app/compte/affichage');
    await page.getByLabel('Thème', { exact: true }).selectOption('dark');
    await page.getByLabel('Densité des tableaux', { exact: true }).selectOption('compact');
    await page.getByRole('button', { name: 'Enregistrer les préférences', exact: true }).click();
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
    await page.goto(base + '/app/compte/securite');
    await page.getByLabel('Mot de passe actuel', { exact: true }).fill('DemoForBrowser123!');
    await page.getByRole('button', { name: 'Préparer l’activation', exact: true }).click();
    const secret = await page.getByLabel('Clé d’activation', { exact: true }).inputValue();
    await page.getByLabel('Code à six chiffres', { exact: true }).fill(totp(secret));
    await page.getByRole('button', { name: 'Activer la double authentification', exact: true }).click();
    await page.getByRole('heading', { name: 'Conservez vos codes de secours', exact: true }).waitFor();
    const codes = await page.locator('.recovery-codes code').allTextContents();
    assert.equal(codes.length, 10);
    await page.screenshot({ path: path.join(output, 'security-desktop.png'), fullPage: true, mask: [page.locator('.recovery-codes')] });
    await page.getByRole('button', { name: 'Déconnexion', exact: true }).click();
    await passwordLogin();
    await page.waitForURL('**/connexion/verification');
    await page.getByLabel('Code d’authentification', { exact: true }).fill(codes[0]);
    await page.getByRole('button', { name: 'Vérifier et se connecter', exact: true }).click();
    await page.waitForURL('**/app');
    const responsive = [];
    for (const width of [320, 390, 768, 1440]) {
      await page.setViewportSize({ width, height: width < 700 ? 844 : 1000 });
      for (const [name, route] of [['branding', '/app/entreprise/personnalisation'], ['oauth', '/app/emails/connexion/guide'], ['mail', '/app/emails/connexion'], ['security', '/app/compte/securite'], ['reports', '/app/rapports'], ['crm', '/app/crm'], ['dashboard', '/app']]) {
        await page.goto(base + route);
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `Overflow ${width}: ${route}`);
        responsive.push({ width, route, ok: true });
        if ((width === 390 || width === 1440) && ['branding', 'mail', 'dashboard'].includes(name)) await page.screenshot({ path: path.join(output, `${name}-${width}.png`), fullPage: true });
      }
    }
    assert.equal(errors.length, 0, errors.join('\n'));
    await fs.writeFile(path.join(output, 'report.json'), JSON.stringify({ ok: true, synthetic_data_only: true, flows: ['tenant_branding', 'document_defaults', 'branded_pdf', 'oauth_setup_callbacks', 'dark_theme', 'compact_density', 'mfa_enrollment', 'recovery_login'], responsive, javascript_and_csp_errors: errors }, null, 2));
    console.log('OAuth, personnalisation, MFA et responsive : OK.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
