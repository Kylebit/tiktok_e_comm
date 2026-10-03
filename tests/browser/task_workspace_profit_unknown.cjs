// Synthetic browser check for a profit attempt whose agent outcome is unknown.
const {chromium} = require('C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '../..');
const out = process.argv[2];
if (!out) throw Error('Evidence directory required');
fs.mkdirSync(out, {recursive: true});
const task = {
  id: 'profit-unknown', task_id: 'profit-unknown', template: 'profit',
  title: '合成利润异常', execution_state: 'failed', current_step: 'coverage',
  scope: {month: '2026-08'}, checkpoint: {agent_attempt_started: true},
  profit_unknown_readback: {
    status: 'UNKNOWN', attempt_output: 'D:/synthetic/attempt',
    session_id: 'synthetic-session', session_readback: 'NOT_VERIFIED',
    agent_result_file: {sha256: 'abc123', ownership: 'UNVERIFIED'},
    automatic_resume_allowed: false,
  },
};
const posts = [];
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
  const browser = await chromium.launch({headless: true,
    executablePath: 'C:/Users/Windows11/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe'});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    await page.route('**/*', route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.origin !== base) return route.abort();
      if (url.pathname === '/api/orbit/navigation') return route.fulfill({json: {ok: true, navigation: []}});
      if (!url.pathname.startsWith('/api/orbit/tasks')) return route.continue();
      if (request.method() !== 'GET') {
        posts.push(url.pathname);
        return route.fulfill({status: 409, json: {error: 'web-only'}});
      }
      if (url.pathname.endsWith('/profit-unknown')) return route.fulfill({json: {task, events: []}});
      return route.fulfill({json: {tasks: [task], executor: {connected: false},
        dispatcher: {state: 'stopped'}, release: {channel: 'synthetic'}}});
    });
    await page.goto(base);
    await page.waitForFunction(() => document.querySelector('#taskRows')?.textContent.includes('利润原会话待核对'));
    assert.match(await page.locator('#taskRows').innerText(), /不可自动重跑/);
    await page.locator('[data-detail="profit-unknown"]').last().click();
    await page.waitForFunction(() => document.querySelector('#detailBody')?.textContent.includes('synthetic-session'));
    const detail = await page.locator('#detailBody').innerText();
    assert.match(detail, /未经核实/);
    assert.match(detail, /abc123/);
    assert.equal(await page.locator('#retryTask,#cancelTask').count(), 0);
    assert.deepEqual(posts, []);
    assert.deepEqual(errors, []);
    await page.screenshot({path: path.join(out, 'profit-unknown.png'), fullPage: true});
    console.log('PASS synthetic profit UNKNOWN browser journey');
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => {console.error(error); server.close(); process.exitCode = 1;});
