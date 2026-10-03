// Synthetic browser regression for per-task retry visibility. No commerce API calls.
const {chromium} = require('C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const output = process.argv[2];
if (!output) throw Error('Evidence directory required');
fs.mkdirSync(output, {recursive: true});

const version = {channel: 'synthetic', version: 'v1'};
const failed = (id, template, extra = {}) => ({
  id, task_id: id, template, title: id, execution_state: 'failed',
  version, steps: [], checkpoint: {}, ...extra,
});
const tasks = [
  failed('publication-no-matching-worker', 'publication', {executor_connected: false}),
  failed('profit-matching-worker', 'profit', {executor_connected: true, allowed_actions: {retry: false}}),
  failed('publication-missing-field', 'publication'),
  failed('publication-old-version', 'publication', {
    executor_connected: true, version: {channel: 'synthetic', version: 'older'}, allowed_actions: {retry: false},
  }),
  failed('profit-unknown', 'profit', {
    executor_connected: true, current_step: 'coverage', allowed_actions: {retry: false},
    profit_unknown_readback: {status: 'UNKNOWN', session_readback: 'NOT_VERIFIED'},
  }),
  failed('delisting-server-allowed', 'delisting', {allowed_actions: {retry: true}}),
];
const posts = [];
const errors = [];
const server = http.createServer((request, response) => {
  const filename = request.url === '/' ? '/task_workspace.html' : request.url;
  const file = path.join(root, 'web', filename);
  if (!file.startsWith(path.join(root, 'web')) || !fs.existsSync(file)) {
    response.writeHead(404); response.end(); return;
  }
  response.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript'
    : file.endsWith('.css') ? 'text/css' : 'text/html');
  response.end(fs.readFileSync(file));
});

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = 'http://127.0.0.1:' + server.address().port;
  const browser = await chromium.launch({
    headless: true,
    executablePath: 'C:/Users/Windows11/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe',
  });
  try {
    const page = await browser.newPage();
    page.on('pageerror', error => errors.push(String(error)));
    await page.route('**/*', route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.origin !== base) return route.abort();
      if (url.pathname === '/api/orbit/navigation') {
        return route.fulfill({json: {ok: true, navigation: []}});
      }
      if (!url.pathname.startsWith('/api/orbit/tasks')) return route.continue();
      const id = url.pathname.split('/').filter(Boolean)[3];
      if (request.method() !== 'GET') {
        posts.push({path: url.pathname, method: request.method()});
        return route.fulfill({status: 409, json: {error: 'synthetic write denied'}});
      }
      if (id) return route.fulfill({json: {task: tasks.find(task => task.id === id), events: []}});
      return route.fulfill({json: {
        tasks, executor: {connected: true, templates: ['profit']},
        release: version, dispatcher: {state: 'polling'},
      }});
    });

    await page.goto(base);
    await page.waitForFunction(() => document.querySelector('#countAll')?.textContent === '6');
    assert.match(await page.locator('#connection').innerText(), /任务服务已连接/);

    async function check(id, expected) {
      await page.locator(`[data-detail="${id}"]`).last().click();
      await page.waitForFunction(taskId => document.querySelector('#detailTitle')?.textContent === taskId, id);
      assert.equal(await page.locator('#retryTask').count(), expected ? 1 : 0, id);
      if (expected) {
        assert.match(await page.locator('#retryTask').innerText(), /恢复任务/);
      }
      await page.locator('[data-close="detailDialog"]').click();
    }

    await check('publication-no-matching-worker', false);
    await check('publication-missing-field', false);
    await check('publication-old-version', false);
    await check('profit-unknown', false);
    await check('profit-matching-worker', false);
    await check('delisting-server-allowed', true);
    assert.deepEqual(posts, []);
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'BROWSER_RESULT.json'), JSON.stringify({
      synthetic: true, global_executor_connected: true,
      checked: tasks.map(task => task.id), posts, errors,
    }, null, 2));
    console.log('PASS synthetic per-task retry browser regression');
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => {console.error(error); server.close(); process.exitCode = 1;});
