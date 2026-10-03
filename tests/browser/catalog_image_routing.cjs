const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require('C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const root = path.resolve(__dirname, '../..');
const base = 'http://127.0.0.1:49384';
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l2cAAAAASUVORK5CYII=', 'base64');
const item = {
  entity_key: 'internal:0001', sku: '0001', aliases: ['0001'], name: 'Verified original', spec: '',
  image_key: 'first', image_url: 'https://cf.shopee.com.my/file/first',
  image_candidates: [
    {key: 'first', url: 'https://cf.shopee.com.my/file/first'},
    {key: 'second', url: 'https://p16-oec-va.ibyteimg.com/file/second'},
  ],
  binding: 'INTERNAL_SKU', cost: {amount: '5', status: 'SAVED', revision: 'r1', choices: []},
  weight_g: 100, channel_evidence: [],
};
const listing = {ok: true, items: [item], total: 1, coverage: {internal_skus: 1, missing_sku_records: 0, conflicts: 0}, review_copy: null};

(async () => {
  const browser = await chromium.launch({headless: true, executablePath: 'C:/Users/Windows11/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe'});
  const page = await browser.newPage();
  const external = [], imageKeys = [];
  let allImagesFail = false;
  try {
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== base) { external.push(url.href); return route.abort(); }
      if (url.pathname === '/catalog') return route.fulfill({contentType: 'text/html', body: fs.readFileSync(path.join(root, 'web/catalog.html'))});
      if (url.pathname === '/static/catalog_directory.js') return route.fulfill({contentType: 'text/javascript', body: fs.readFileSync(path.join(root, 'web/static/catalog_directory.js'))});
      if (url.pathname.startsWith('/static/')) return route.fulfill({contentType: 'text/javascript', body: ''});
      if (url.pathname === '/api/catalog/data-mode') return route.fulfill({contentType: 'application/json', body: JSON.stringify({ok: true, review_copy: null})});
      if (url.pathname === '/api/catalog/skus') return route.fulfill({contentType: 'application/json', body: JSON.stringify(listing)});
      if (url.pathname === '/api/catalog/image') {
        imageKeys.push(url.searchParams.get('key'));
        if (allImagesFail || url.searchParams.get('key') === 'first') return route.fulfill({status: 502, contentType: 'application/json', body: '{}'});
        return route.fulfill({contentType: 'image/png', body: png});
      }
      return route.fulfill({status: 404, body: ''});
    });
    await page.goto(base + '/catalog');
    await page.waitForFunction(() => document.querySelector('.product-image')?.naturalWidth > 0);
    assert.deepEqual(imageKeys, ['first', 'second']);
    assert.equal(await page.locator('.product-image').count(), 1);
    assert.deepEqual(external, []);
    allImagesFail = true;
    imageKeys.length = 0;
    await page.reload();
    await page.getByText('原图暂不可用').waitFor();
    assert.deepEqual(imageKeys, ['first', 'second']);
    assert.equal(await page.locator('.product-image').count(), 0);
    assert.deepEqual(external, []);
    console.log('catalog image proxy, fallback, and honest failure passed');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
