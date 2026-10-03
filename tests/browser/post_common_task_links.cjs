// Synthetic task API and real Chromium. No business store or provider is used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {chromium} = require('playwright');

const root = path.resolve(__dirname, '../..');
const taskId = 'TASK-123';
const actionId = 'a'.repeat(32);
const offer = '3956742887';
const targets = ['tiktok:MY', 'ozon:RU'];
const scope = {offer_id: offer, skus: ['SKU-1'], shops: [...targets].sort()};
const diagnosticURL = `/product-workspace?offer_id=${offer}&round=final&task_id=${taskId}`
  + `&action_id=${actionId}&review_stage=post-common&generation=1&step_index=2`
  + '#originalPublicationReview';
const binding = {
  offer_id: offer, revision: '4', sku: 'SKU-1', round1_digest: 'sha256:'+'1'.repeat(64),
  round2_digest: 'sha256:'+'2'.repeat(64), targets,
  common_plan_id: 'plan-1', common_payload_digest: 'sha256:'+'3'.repeat(64),
  common_run_id: 'run-1', common_readback_evidence_digest: 'sha256:'+'4'.repeat(64),
  source_digest: 'sha256:'+'5'.repeat(64), adapter: 'post-common-diagnostic/v1',
  review_mode: 'single-final-review/v1', task_id: taskId, action_id: actionId,
  generation: 1, step_index: 2, scope_digest: '6'.repeat(64),
};
const original = {
  task_id: taskId, title: '合成上架诊断', template: 'publication', scope,
  current_step: 'release', execution_state: 'waiting_user',
  review_mode: 'single-final-review/v1',
  required_action: {kind: 'review', label: '查看上架终审诊断（暂不可批准）',
    action_id: actionId, url: diagnosticURL, receipt_binding: binding},
};
let task = structuredClone(original);
const server = http.createServer((req, res) => {
  const pathname = new URL(req.url, 'http://127.0.0.1').pathname;
  const file = pathname === '/' ? 'task_workspace.html' : pathname.slice(1);
  const target = path.resolve(root, 'web', file);
  if (!target.startsWith(path.resolve(root, 'web')+path.sep) || !fs.existsSync(target)) {
    res.writeHead(404); res.end(); return;
  }
  res.setHeader('Content-Type', target.endsWith('.js') ? 'text/javascript'
    : target.endsWith('.css') ? 'text/css' : 'text/html');
  res.end(fs.readFileSync(target));
});

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({headless: true,
    executablePath: process.env.ORBIT_CHROMIUM_BIN});
  try {
    const page = await browser.newPage();
    const writes = [];
    page.on('request', request => {
      if (!['GET', 'HEAD'].includes(request.method())) writes.push(request.url());
    });
    await page.route('**/api/orbit/**', route => {
      const u = new URL(route.request().url());
      if (u.pathname.endsWith('/navigation')) return route.fulfill({json:{ok:true,navigation:[]}});
      if (u.pathname === '/api/orbit/tasks') return route.fulfill({json:{tasks:[task],
        executor:{connected:false}, release:{environment:'preview'}}});
      if (u.pathname === `/api/orbit/tasks/${taskId}`) return route.fulfill({json:{task,events:[]}});
      return route.fulfill({status:404,json:{ok:false}});
    });
    await page.goto(base);
    await page.waitForSelector('#attentionList a');
    assert.equal(await page.locator('#attentionList a').getAttribute('href'), diagnosticURL);
    assert.match(await page.locator('#attentionList a').innerText(), /诊断/);
    await page.locator(`[data-detail="${taskId}"]`).last().click();
    await page.waitForSelector('#detailDialog[open]');
    assert.equal(await page.locator('#detailBody .detail-action a').getAttribute('href'), diagnosticURL);
    await page.locator('[data-close="detailDialog"]').click();

    async function replace(changes, expectedLinks) {
      task = structuredClone(original);
      changes(task);
      await page.locator('#refreshTasks').click();
      await page.waitForFunction((expected) => document.querySelectorAll('#attentionList a').length === expected,
        expectedLinks, {timeout: 3000});
      assert.equal(await page.locator('#attentionList a').count(), expectedLinks);
    }
    await replace(t => { t.required_action.url = '/product-workspace?offer_id=999'; }, 0);
    await page.locator(`[data-detail="${taskId}"]`).last().click();
    await page.waitForSelector('#detailDialog[open]');
    assert.equal(await page.locator('#detailBody .detail-action a').count(), 0);
    await page.locator('[data-close="detailDialog"]').click();
    await replace(t => { t.required_action.receipt_binding.action_id = 'b'.repeat(32); }, 0);
    await replace(t => { t.required_action.receipt_binding.source_digest = ''; }, 0);
    await replace(t => { t.scope.shops = ['ozon:RU']; }, 0);
    await replace(t => { t.required_action.receipt_binding.generation = 2; }, 0);
    await replace(t => { t.review_mode = 'legacy'; }, 0);
    await replace(t => { t.required_action.receipt_binding.adapter = 'single-final-review/v1'; }, 0);

    await replace(t => {
      const b = t.required_action.receipt_binding;
      b.adapter = 'single-final-review/v1';
      b.common_token_digest = 'sha256:'+'7'.repeat(64);
      b.preview_digest = 'sha256:'+'8'.repeat(64);
      delete b.common_run_id; delete b.common_readback_evidence_digest; delete b.source_digest;
      t.required_action.url = `/product-workspace?offer_id=${offer}&task_id=${taskId}`
        + `&action_id=${actionId}&round=final#originalPublicationReview`;
    }, 1);
    assert.match(await page.locator('#attentionList a').innerText(), /冻结候选.*只读/);
    assert.equal(await page.locator('#attentionList a').getAttribute('href'),
      `/product-workspace?offer_id=${offer}&task_id=${taskId}&action_id=${actionId}`
      + '&round=final#originalPublicationReview');
    assert.deepEqual(writes, []);
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
