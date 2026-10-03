const {chromium}=require('C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..');
const tasks=[
  {task_id:'TASK-20260909-001',source_key:'existing05-august-1115c78',title:'八月 TK 和 Shopee 利润',template:'profit',execution_state:'external_task',scope:{month:'2026-08',platforms:['tiktok','shopee'],shops:[]},external_task:{observed_at:'2026-09-19T05:29:49.304428+00:00',live_status:'unknown',observed_status:'未整体完成'},checkpoint:{artifact_files:{'profit-status-20260919-v3.html':'a12299061a47967bdd15086bf17b070200392e0013d8af491a0d107ee791492e'}},result_url:'/api/orbit/tasks/TASK-20260909-001/artifacts/profit-status.html',steps:[]},
  {task_id:'verified-july',title:'七月任务',template:'profit',execution_state:'completed',scope:{month:'2026-07',shops:['shop-A'],skus:[],platforms:['tiktok'],sites:['MY']},request_scope:{month:'2026-07',shops:['shop-A'],skus:[]},steps:[{key:'coverage',checkpoint:{reports:[{platform:'tiktok',site:'MY',shop_id:'shop-A',cutoff:'2026-07-28',report:{sha256:'a'.repeat(64)},audit:{status:'PASSED'}}],manifest:{sha256:'b'.repeat(64)}}}],result_url:'/api/orbit/tasks/verified-july/artifacts/index.html'}
];
(async()=>{const browser=await chromium.launch({headless:true,executablePath:'C:/Users/Windows11/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe'});try{
  const page=await browser.newPage();const posts=[],errors=[];page.on('pageerror',e=>errors.push(String(e)));
  await page.addInitScript(()=>{const NativeDate=Date;globalThis.Date=class extends NativeDate{constructor(...args){super(...(args.length?args:['2026-09-27T12:00:00+08:00']));}static now(){return new NativeDate('2026-09-27T12:00:00+08:00').getTime();}};});
  await page.context().route('**/*',route=>{const u=new URL(route.request().url()),p=u.pathname;if(p==='/api/orbit/navigation')return route.fulfill({json:{navigation:[],tools:[{id:'manage-profit-settlement',title:'利润结算',description:'月度利润',stage:'WORKFLOW_RUNTIME_REQUIRED',status:'需核对',method:'python R/scripts/orbit_tools.py --runtime-root R help manage-profit-settlement',entry_available:false}],capabilities:[],tool_catalog:{state:'VERIFIED_LOCAL_CODE_ONLY'}}});if(p==='/api/orbit/operations-runtime')return route.fulfill({json:{execution_mode:'web-only',worker_enabled:false}});if(p==='/api/orbit/tasks'){if(route.request().method()==='POST'){posts.push(route.request().postDataJSON());return route.fulfill({json:{task:tasks[0]}});}return route.fulfill({json:{tasks,executor:{connected:false},dispatcher:{state:'stopped'},release:{environment:'stable'}}});}if(p.startsWith('/api/orbit/tasks/'))return route.fulfill({json:{task:tasks.find(t=>t.task_id===p.split('/')[4]),events:[]}});if(p.startsWith('/profit-original/artifacts/'))return route.fulfill({contentType:'text/html',body:'<title>2026-07 TikTok 历史报告</title><h1>历史七月报告</h1>'});const file=path.join(root,'web',p==='/profit'?'profit_hub.html':p==='/knowledge'?'knowledge.html':p);return route.fulfill({contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html',body:fs.readFileSync(file)});});
  await page.goto('http://127.0.0.1/profit');await page.waitForSelector('#profitTasks article');
  const julyLinks=page.locator('#julyTikTokReports a[data-site]');
  assert.equal(await julyLinks.count(),4,'first-level profit entry should expose four July TikTok original reports');
  const th=page.locator('#julyTikTokReports a[data-site="TH"]');
  const thPath='/profit-original/artifacts/profit_reports_monthly/2026-07/tiktok_TH_2026-07-01_2026-07-31.monthly-profit.html';
  assert.equal(await th.getAttribute('href'),thPath);
  assert.ok((await th.boundingBox()).y<(await page.locator('#profitForm').boundingBox()).y,'July entry should precede task creation');
  const popupPromise=page.waitForEvent('popup');await th.click();const historical=await popupPromise;
  await historical.waitForLoadState();assert.equal(new URL(historical.url()).pathname,thPath);
  await historical.close();
  assert.equal(await page.locator('#profitMonth').inputValue(),'2026-08');
  assert.ok((await page.locator('body').innerText()).includes('2026 年 7 月 TikTok 完整原始报告'));
  assert.ok((await page.locator('.history-access').innerText()).includes('不证明八月已结算'));
  assert.ok((await page.locator('body').innerText()).includes('已保存的任务不会自动执行'));
  assert.ok((await page.locator('#profitTasks').innerText()).includes('历史观测'));
  assert.ok((await page.locator('#profitTasks').innerText()).includes('2026-09-19'));
  assert.ok((await page.locator('#profitTasks').innerText()).includes('0018'));
  assert.ok((await page.locator('#profitTasks').innerText()).includes('0810'));
  await page.locator('[data-profit-task="TASK-20260909-001"]').click();
  await page.waitForFunction(()=>document.querySelector('#profitDetail').textContent.includes('逐店完整结算截止日：待核'));
  assert.ok((await page.locator('#profitDetail').innerText()).includes('八月下单订单'));
  const augustGate=await page.locator('[data-history-profit-gate]').innerText();
  for(const fact of ['2026-09-19','8 月下单','九月结算','逐店连续完全结算','精确 shop_id','22% 是广告估算','0018 / 0810','最高成本只是历史诊断情景','不能作为最终利润'])
    assert.ok(augustGate.includes(fact),`historical August gate must disclose ${fact}`);
  const screenshot=process.env.ORBIT_PROFIT_SCREENSHOT||process.argv[2];
  if(screenshot)await page.screenshot({path:screenshot,fullPage:true});
  assert.ok(!(await page.locator('#profitDetail').innerText()).includes('2026-08-24'),'old HTML cutoff must not become structured coverage');
  const originalAugust=structuredClone(tasks[0]);
  const currentCoverage={key:'coverage',checkpoint:{reports:[{platform:'tiktok',site:'MY',shop_id:'exact-shop',cutoff:'2026-08-24',report:{sha256:'c'.repeat(64)},audit:{status:'PASSED'}}],manifest:{sha256:'d'.repeat(64)}}};
  for(const [label,change] of [
    ['changed month',task=>{task.scope.month='2026-07';}],
    ['completed task',task=>{task.execution_state='completed';}],
  ]){
    tasks[0]=structuredClone(originalAugust);change(tasks[0]);
    await page.reload();await page.waitForSelector('[data-profit-task="TASK-20260909-001"]');
    assert.ok(!(await page.locator('#profitTasks').innerText()).includes('0018 / 0810'),`${label} must not show stale historical gate in task list`);
    await page.locator('[data-profit-task="TASK-20260909-001"]').click();
    await page.waitForFunction(()=>document.querySelector('#profitDetail').textContent.includes('状态：'));
    assert.equal(await page.locator('[data-history-profit-gate]').count(),0,`${label} must not override current result with historical warning`);
  }
  for(const [label,change,partial] of [
    ['failed coverage audit',report=>{report.audit.status='FAILED';},false],
    ['invalid coverage cutoff',report=>{report.cutoff='2026-09-01';},false],
    ['invalid report digest',report=>{report.report.sha256='bad';},false],
    ['one valid site',report=>{},true],
  ]){
    tasks[0]=structuredClone(originalAugust);
    tasks[0].steps=[structuredClone(currentCoverage)];change(tasks[0].steps[0].checkpoint.reports[0]);
    await page.reload();await page.waitForSelector('[data-profit-task="TASK-20260909-001"]');
    await page.locator('[data-profit-task="TASK-20260909-001"]').click();
    await page.waitForSelector('[data-history-profit-gate]');
    const warning=await page.locator('[data-history-profit-gate]').innerText();
    for(const fact of ['未形成最终利润','0018 / 0810','22% 是广告估算'])
      assert.ok(warning.includes(fact),`${label} must retain historical ${fact}`);
    assert.ok(warning.includes(partial?'已有部分逐站截止日，完整覆盖仍待核':'当前任务没有可验证的实时截止日'),`${label} must describe verified coverage accurately`);
    const detail=await page.locator('#profitDetail').innerText();
    assert.ok(detail.includes(partial?'表格是已登记证据':'表格有登记行，但没有可用的结构化逐站覆盖'),`${label} with a coverage table must describe its limited evidence`);
    assert.ok(!detail.includes('没有结构化逐站覆盖登记'),`${label} with a coverage table must not deny its existence`);
  }
  tasks[0]=originalAugust;
  await page.reload();await page.waitForSelector('[data-profit-task="TASK-20260909-001"]');
  await page.locator('#profitMonth').fill('2026-07');await page.locator('#profitShops').fill('shop-A');await page.locator('#submitProfit').click();
  assert.equal(posts.length,0,'exact month and shop must reuse existing task');
  await page.waitForFunction(()=>document.querySelector('#profitDetail').textContent.includes('2026-07-28'));
  assert.ok((await page.locator('#profitDetail').innerText()).includes('2026-07-28'));
  assert.ok((await page.locator('#profitDetail').innerText()).includes('已登记报告 SHA-256'));
  await page.locator('#profitShops').fill('shop-B');await page.locator('#submitProfit').click();
  assert.equal(posts.length,1);assert.deepEqual(posts[0].scope,{month:'2026-07',shops:['shop-B'],skus:[]});assert.equal(posts[0].reuse_existing_scope,true);
  await page.locator('#profitMonth').fill('2026-08');await page.locator('#profitShops').fill('shop-A');await page.locator('#submitProfit').click();
  assert.equal(posts.length,1,'legacy broad month without request identity must not create a duplicate');
  assert.match(await page.locator('#formError').innerText(),/原请求范围未保存/);
  await page.goto('http://127.0.0.1/knowledge?tool=manage-profit-settlement');await page.waitForSelector('#knowledgeDetail[data-tool="manage-profit-settlement"]');
  assert.ok((await page.locator('#knowledgeDetail').innerText()).includes('利润结算'));
  assert.deepEqual(errors,[]);console.log('PASS profit hub month, archive, historical boundary, structured coverage and exact-scope reuse');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
