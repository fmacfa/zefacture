const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const base = process.env.TEST_BASE_URL;
const output = path.resolve("test-results/browser-import-progress");
module.exports = async function checkExpenseImportProgress(context) {
  assert.equal(new URL(base).hostname, "127.0.0.1");
  const page = await context.newPage();
  try {
    await page.setViewportSize({width: 390, height: 844});
    await page.emulateMedia({reducedMotion: "reduce"});
    const errors = [];
    page.on("pageerror", err => errors.push(err.message));
    await page.goto(base + "/app/notes-de-frais");
    const supplier = await fs.readFile(path.resolve("test-results/browser/supplier.pdf"));
    const payload = [
      {name: "frais-demo.pdf", mimeType: "application/pdf", buffer: Buffer.concat([supplier, Buffer.from("\n% receipt demo\n")])},
      {name: "illisible-demo.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-incomplet")},
    ];
    await page.getByLabel("Fichiers", {exact: true}).setInputFiles(payload);
    await page.getByText(/2 fichiers sélectionnés.*Cliquez sur Importer/).waitFor();
    await page.getByLabel("Analyse après import").selectOption("local");
    let allowUpload, allowStatus;
    const holdUpload = new Promise(resolve => { allowUpload = resolve; });
    const holdStatus = new Promise(resolve => { allowStatus = resolve; });
    await page.route("**/app/notes-de-frais", async route => {
      if (route.request().method() === "POST") await holdUpload;
      await route.continue();
    });
    await page.route(/\/app\/notes-de-frais\?suivi=/, async route => {
      await holdStatus;
      await route.continue();
    });
    await page.getByRole("button", {name: "Importer", exact: true}).click();
    await page.getByText("Envoi du fichier 1 sur 2…", {exact: true}).waitFor();
    allowUpload();
    await page.getByText(/2 fichiers importés/).waitFor();
    const badge = page.locator("[data-batch-files] [data-pending] .request-spinner").first();
    assert.ok(await badge.isVisible());
    assert.equal(await badge.evaluate(el => getComputedStyle(el).animationName), "none");
    await fs.mkdir(output, {recursive: true});
    await page.screenshot({path: path.join(output, "attente-mobile.png")});
    allowStatus();
    const success = page.locator("[data-import-row]").filter({hasText: "frais-demo.pdf"});
    const failure = page.locator("[data-import-row]").filter({hasText: "illisible-demo.pdf"});
    await success.getByText("Analyse terminée · à vérifier", {exact:true}).waitFor({timeout:90000});
    await failure.getByText("Échec du traitement", {exact:true}).waitFor({timeout:90000});
    assert.equal(await page.locator("[data-batch-files] [data-pending]").count(), 0);
    await page.getByRole("link", {name:"Voir les documents de ce lot"}).click();
    assert.match(page.url(), /app\/notes-de-frais\?lot=/);
    await page.getByRole("link", {name:"frais-demo.pdf", exact:true}).click();
    assert.ok(Number(await page.locator('input[name=expense_amount]').inputValue()) > 0);
    assert.equal(await page.locator("[data-import-wait]").count(), 0);
    assert.deepEqual(errors, []);
    await fs.writeFile(path.join(output, "report.json"), JSON.stringify({ok:true, flows:["selection","upload","pending","completed","failed","expense_batch","prefilled_amount","reduced_motion"]}, null, 2));
  } finally {
    await page.close();
  }
};
