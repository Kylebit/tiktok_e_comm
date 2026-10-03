const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

(async () => {
  const [base, output] = process.argv.slice(2);
  const denied = [], writes = [], errors = [];
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.ORBIT_BROWSER_EXECUTABLE ||
      'C:/Users/Windows11/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe',
    args: ['--disable-background-networking', '--no-first-run'],
  });
  const context = await browser.newContext({viewport: {width: 1360, height: 900}, serviceWorkers: 'block'});
  await context.route('**/*', route => {
    const url = route.request().url();
    if (url.startsWith(base + '/')) return route.continue();
    denied.push(url);
    return route.abort();
  });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(String(error)));
  page.on('request', request => { if (!['GET', 'HEAD'].includes(request.method())) writes.push(request.method() + ' ' + request.url()); });
  try {
    await page.goto(base + '/#region=SUMMARY');
    await page.waitForFunction(() => document.querySelector('#inboundUnits')?.textContent !== '—');
    const facts = await page.evaluate(() => {
      const batches = ['MY', 'TH', 'VN', 'PH'].flatMap(region => INBOUND_PLAN.regions[region].batches || []);
      return {
        count: batches.length,
        total: batches.reduce((sum, batch) => sum + batch.totalUnits, 0),
        filtered: calculated.reduce((sum, row) => sum + row.inventory.inbound, 0),
      };
    });
    assert(facts.count > 0 && facts.filtered < facts.total, 'fixture must expose a batch omitted by the >10 recommendation filter');
    assert.equal(await page.locator('#inboundUnits').innerText(),
      `${facts.count} 批 / ${facts.total.toLocaleString('zh-CN')} 件`);
    assert((await page.locator('#inboundUnits').locator('..').innerText()).includes('快照在途批次'));
    await page.screenshot({path: path.join(output, 'supply-inbound-summary.png'), fullPage: true});
    const mismatched = await page.evaluate(() => {
      const batch = INBOUND_PLAN.regions.TH.batches[0];
      batch.totalUnits += 1;
      renderSummary();
      const text = document.querySelector('#inboundUnits').textContent;
      batch.totalUnits -= 1;
      renderSummary();
      return text;
    });
    assert.equal(mismatched, '批次在途待对账');
    const swappedSku = await page.evaluate(() => {
      const batch = INBOUND_PLAN.regions.TH.batches[0];
      const [firstSku, secondSku] = Object.keys(batch.skuQuantities).slice(0, 2);
      const first = batch.skuQuantities[firstSku];
      const second = batch.skuQuantities[secondSku];
      if (first === second) throw new Error('fixture needs unequal SKU quantities');
      batch.skuQuantities[firstSku] = second;
      batch.skuQuantities[secondSku] = first;
      const after = Object.values(batch.skuQuantities).reduce((sum, units) => sum + units, 0);
      renderSummary();
      const text = document.querySelector('#inboundUnits').textContent;
      batch.skuQuantities[firstSku] = first;
      batch.skuQuantities[secondSku] = second;
      renderSummary();
      return {text, before: batch.totalUnits, after};
    });
    assert.equal(swappedSku.before, swappedSku.after, 'country total stays unchanged');
    assert.equal(swappedSku.text, '批次在途待对账');
    assert.deepEqual(errors, []);
    assert.deepEqual(denied, []);
    assert.deepEqual(writes, []);
    fs.writeFileSync(path.join(output, 'supply-inbound-headline.json'), JSON.stringify({passed: true, facts, swappedSku, errors, denied, writes}, null, 2));
  } catch (error) {
    fs.writeFileSync(path.join(output, 'supply-inbound-headline.json'), JSON.stringify({passed: false, error: String(error), errors, denied, writes}, null, 2));
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
