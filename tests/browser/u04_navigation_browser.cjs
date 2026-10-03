const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const base=process.argv[2],out=path.resolve(process.argv[3]);fs.mkdirSync(out);
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.ORBIT_BROWSER_EXECUTABLE});
 const context=await browser.newContext({viewport:{width:1440,height:1000}});const page=await context.newPage(),checks=[],errors=[],requests=[];
 page.on('pageerror',e=>errors.push(String(e)));page.on('request',r=>requests.push({method:r.method(),url:r.url()}));
 await context.route('**/*',route=>new URL(route.request().url()).origin===new URL(base).origin?route.continue():route.abort());
 async function check(name,fn){try{await fn();checks.push({name,ok:true});}catch(error){checks.push({name,ok:false,error:String(error)});}}
 await page.goto(base);await page.locator('#orbitNav a[href="/supply-chain/"]').click();await page.waitForSelector('#skuRows tr');
 await check('actual unified navigation opens real supply ledger directly',async()=>{
   assert.equal(new URL(page.url()).pathname,'/supply-chain/');assert.equal(await page.locator('iframe').count(),0);
   assert.equal(await page.locator('nav button[data-region=MY]').getAttribute('class'),'country-tab active');
 });
 await check('three independent clocks and stale gate are visible',async()=>{
  const text=await page.locator('#snapshotDate').textContent();assert.match(text,/库存 2026-09-05/);assert.match(text,/订单 2026-09-04/);assert.match(text,/批次 2026-09-03/);
  assert.match(await page.locator('#evidenceGrid').textContent(),/BLOCKED_STALE_INVENTORY/);
 });
 await check('real calculation keeps both demand scenarios and missing logistics row',async()=>{
  const value=await page.evaluate(()=>({trend:calculated[0].demandScenarios.trend,recent30:calculated[0].demandScenarios.recent30,
    sku:calculated[0].sku,inventory:calculated[0].inventory}));fs.writeFileSync(path.join(out,'calculated.json'),JSON.stringify(value,null,2));
  assert.equal(value.sku,'0001');assert.notEqual(value.trend.recommended,value.recent30.recommended);
  assert.match(await page.locator('#skuRows').textContent(),/尺寸待补充/);assert.equal(await page.locator('.scenario-audit').count(),2);
 });
 await page.screenshot({path:path.join(out,'ledger-desktop.png'),fullPage:true});
 await page.locator('button[data-region=VN]').click();
 await check('missing order pages stays explicitly pending',async()=>{assert.match(await page.locator('#skuRows').textContent(),/PENDING_REFRESH/);});
 await page.locator('button[data-region=PH]').click();
 await check('ambiguous exact inventory identity remains blocked',async()=>{assert.match(await page.locator('#evidenceGrid').textContent(),/合成身份歧义/);});
 await page.locator('button[data-region=TH]').click();
 await check('incomplete batch allocation is not counted as supply',async()=>{assert.equal(await page.evaluate(()=>calculated[0].countedInbound),0);assert.match(await page.locator('#skuRows').textContent(),/批次 SKU 分摊未对平/);});
 await page.locator('nav a.page-link').click();await page.waitForSelector('#batchRows tr');
 await check('ledger to real batch editor carries selected country',async()=>{assert.equal(new URL(page.url()).pathname,'/supply-chain/inbound-batches.html');assert.ok((await page.locator('nav button[data-region=TH]').getAttribute('class')).includes('active'));});
 await page.goBack();await page.waitForSelector('#skuRows tr');
 await check('browser back preserves the selected country',async()=>{assert.ok((await page.locator('nav button[data-region=TH]').getAttribute('class')).includes('active'));});
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'ledger-mobile.png'),fullPage:true});
 await check('narrow ledger remains within viewport with scrollable data',async()=>{assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert.equal(await page.locator('#skuRows tr').count(),1);});
 await check('no script exceptions or external/business requests',async()=>{assert.deepEqual(errors,[]);assert.ok(requests.every(r=>r.method==='GET'&&new URL(r.url).origin===new URL(base).origin));});
 fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({layer:'actual Chromium + real Handler/registry and actual HTML/JS, synthetic data files only',base,checks,errors,requests},null,2));
 await browser.close();console.log(JSON.stringify({checks:checks.length,failed:checks.filter(x=>!x.ok),out}));process.exitCode=checks.some(x=>!x.ok)?1:0;
})().catch(error=>{console.error(error);process.exit(2);});
