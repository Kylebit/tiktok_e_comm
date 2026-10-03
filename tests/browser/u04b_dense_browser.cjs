const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');const{chromium}=require('playwright');
const base=process.argv[2],out=process.argv[3];
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.ORBIT_BROWSER_EXECUTABLE}),context=await browser.newContext({viewport:{width:1440,height:900}}),page=await context.newPage(),checks=[],errors=[],requests=[];
 await context.route('**/*',r=>new URL(r.request().url()).origin===new URL(base).origin?r.continue():r.abort());page.on('pageerror',e=>errors.push(String(e)));page.on('request',r=>requests.push({method:r.method(),url:r.url()}));
 async function check(name,fn){try{await fn();checks.push({name,ok:true});}catch(e){checks.push({name,ok:false,error:String(e)});}}
 await page.goto(base+'supply-chain/');await page.waitForSelector('.sku-summary');
 await check('four complete SKU rows and eight real images below the maintenance banner',async()=>{
  const positions=await page.locator('.sku-summary').evaluateAll(rows=>rows.map(r=>({top:r.getBoundingClientRect().top,bottom:r.getBoundingClientRect().bottom})));
  assert.ok(positions.filter(r=>r.top>=0&&r.bottom<=900).length>=4);assert.ok(await page.locator('.sku-summary img').evaluateAll(imgs=>imgs.length===8&&imgs.every(i=>i.complete&&i.naturalWidth>0)));
 });
 await page.screenshot({path:path.join(out,'desktop.png')});fs.writeFileSync(path.join(out,'calculated.json'),JSON.stringify(await page.evaluate(()=>calculated),null,2));
 await check('three clocks and both recommendations remain visible with conditional warning',async()=>{
  assert.match(await page.locator('#snapshotDate').textContent(),/库存 2026-09-05.*订单 2026-09-04.*批次 2026-09-03/);
  const row=page.locator('.sku-summary').first();assert.match(await row.textContent(),/趋势.*30日/s);assert.match(await row.textContent(),/条件测算/);assert.match(await row.textContent(),/尺寸、重量、成本待补/);
 });
 const toggle=page.locator('.sku-detail-toggle').first();
 await check('keyboard expands complete evidence with audits initially folded',async()=>{
  await toggle.focus();await page.keyboard.press('Enter');assert.equal(await toggle.getAttribute('aria-expanded'),'true');
  assert.ok(await page.locator('#sku-detail-MY-0001').isVisible());assert.equal(await page.locator('#sku-detail-MY-0001 .calc-audit').getAttribute('open'),null);
  assert.match(await page.locator('#sku-detail-MY-0001').textContent(),/TikTok.*Shopee.*MY-SYNTHETIC-BATCH/s);
 });
 await check('expanded audit retains two independent complete traces and logistics editor',async()=>{
  const detail=page.locator('#sku-detail-MY-0001');await detail.locator('.calc-audit summary').click();assert.equal(await detail.locator('.scenario-audit:visible').count(),2);
  await detail.locator('[data-action=manual-entry]').click();assert.ok(await page.locator('#manualInputDialog').isVisible());await page.locator('#cancelManualInput').click();
 });
 await page.screenshot({path:path.join(out,'expanded-desktop.png')});
 await toggle.click();
 await check('search and status filters preserve exact SKU count and empty state',async()=>{
  await page.locator('#searchInput').fill('0008');assert.equal(await page.locator('.sku-summary').count(),1);assert.equal(await page.locator('.sku-summary').getAttribute('data-sku'),'0008');
  await page.locator('#statusFilter').selectOption('HOLD');assert.equal(await page.locator('.sku-summary').count(),0);assert.ok(await page.locator('#skuEmpty').isVisible());
  await page.locator('#statusFilter').selectOption('RECENT30');assert.equal(await page.locator('.sku-summary').count(),1);await page.locator('#searchInput').fill('');
 });
 await check('summary keeps country identities and distinct rows',async()=>{
  await page.locator('nav button[data-region=SUMMARY]').click();assert.ok(await page.locator('.sku-summary').count()>8);assert.match(await page.locator('.sku-summary').first().textContent(),/MY|TH|VN|PH/);
 });
 await check('order and allocation blockers remain reachable',async()=>{
  await page.locator('nav button[data-region=VN]').click();await page.locator('#statusFilter').selectOption('all');assert.match(await page.locator('.sku-summary').first().textContent(),/订单事实待补/);
  await page.locator('.sku-detail-toggle').first().click();assert.match(await page.locator('.sku-detail-row:visible').textContent(),/PENDING_REFRESH/);
  await page.locator('nav button[data-region=TH]').click();assert.match(await page.locator('.sku-summary[data-sku="0001"]').textContent(),/分摊待核/);
 });
 await check('country navigation to batch and browser back keeps country',async()=>{
  await page.locator('nav a.page-link').click();assert.ok(page.url().includes('region=TH'));await page.waitForSelector('#batchRows tr');await page.goBack();await page.waitForSelector('.sku-summary');assert.ok((await page.locator('nav button[data-region=TH]').getAttribute('class')).includes('active'));
 });
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'mobile.png')});
 await check('narrow table scroll and keyboard detail reachability do not overflow page',async()=>{
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.locator('.sku-detail-toggle').first().focus();await page.keyboard.press('Enter');
  assert.ok(await page.locator('.sku-detail-row:visible').count()===1);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 });
 await check('formulas and evidence remain accessible on demand',async()=>{
  const panels=page.locator('.reference-panel');await panels.nth(0).locator(':scope > summary').click();assert.ok(await page.locator('#formulaText').isVisible());
  await panels.nth(1).locator(':scope > summary').click();assert.ok(await page.locator('#evidenceGrid').isVisible());
 });
 await check('no script exceptions or external and business requests',async()=>{assert.deepEqual(errors,[]);assert.ok(requests.every(r=>r.method==='GET'&&new URL(r.url).origin===new URL(base).origin));});
 fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({base,checks,errors,requests,fixture:'explicit eight synthetic SKU rows per country served by real Handler'},null,2));await browser.close();console.log(JSON.stringify(checks));process.exitCode=checks.every(c=>c.ok)?0:1;
})().catch(e=>{console.error(e);process.exit(2);});
