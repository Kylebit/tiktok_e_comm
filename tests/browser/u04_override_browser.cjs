const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {chromium}=require('playwright');
const root=path.resolve(process.argv[2]),out=path.resolve(process.argv[3]);fs.mkdirSync(out,{recursive:false});
const dashboard=path.join(root,'domains/supply_chain_operations/dashboard');
const key='supply-chain-inbound-batch-timing-v3';
const batch={batchId:'TEST-BATCH-001',createdAt:'2026-09-01T00:00:00',estimatedAnchorAt:'2026-09-05T00:00:00',
 estimatedSellableDate:'2026-09-12',estimatedSellableConfirmedAt:'2026-09-05T12:00:00Z',totalUnits:3,transportDays:7,
 skuQuantities:{'0001':2,'0002':1},source:'synthetic complete batch detail'};
const data={snapshotDate:'2026-09-05',orderDemandCapturedAt:'2026-09-04T10:00:00Z',config:{TH:{warehouse:'TEST-WAREHOUSE'}}};
const plan={capturedAt:'2026-09-05T11:00:00Z',regions:{TH:{allocationPolicy:'EXACT_BATCH_SKU_REQUIRED',batches:[batch]}}};
const requests=[];let servers=[];
async function start(){const server=http.createServer((req,res)=>{
 requests.push({method:req.method,url:req.url});if(req.method!=='GET'){res.writeHead(403);return res.end('read only');}
 const name=new URL(req.url,'http://local').pathname.split('/').pop()||'inbound-batches.html';
 if(name==='data.js'){res.setHeader('Content-Type','text/javascript');return res.end('window.SUPPLY_CHAIN_DATA='+JSON.stringify(data)+';');}
 if(name==='inbound-plan.js'){res.setHeader('Content-Type','text/javascript');return res.end('window.SUPPLY_CHAIN_INBOUND_PLAN='+JSON.stringify(plan)+';');}
 if(!['inbound-batches.html','inbound-batches.js','inbound-timeline.js','styles.css'].includes(name)){res.writeHead(404);return res.end();}
 res.setHeader('Content-Type',name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':'text/html');res.end(fs.readFileSync(path.join(dashboard,name)));
});await new Promise(r=>server.listen(0,'127.0.0.1',r));servers.push(server);return 'http://127.0.0.1:'+server.address().port;}
(async()=>{
 const origins=[await start(),await start()];const browser=await chromium.launch({headless:true,executablePath:process.env.ORBIT_BROWSER_EXECUTABLE});
 const context=await browser.newContext({viewport:{width:1440,height:1000}});const failures=[],checks=[],errors=[];
 await context.route('**/*',route=>origins.includes(new URL(route.request().url()).origin)?route.continue():route.abort());
 async function check(name,fn){try{await fn();checks.push({name,ok:true});}catch(error){checks.push({name,ok:false,error:String(error)});failures.push(name);}}
 const page=await context.newPage();page.on('pageerror',e=>errors.push(String(e)));
 await page.goto(origins[0]+'/inbound-batches.html');
 const stale={[`TH:${batch.batchId}`]:{anchorAt:'2026-09-02T09:00',estimatedSellableDate:'2026-09-09',
 basePlanConfirmedAt:'2026-09-03T00:00:00Z',updatedAt:'2026-09-04T00:00:00Z',sourceNote:'old synthetic confirmation'}};
 await page.evaluate(({key,stale})=>localStorage.setItem(key,JSON.stringify(stale)),{key,stale});await page.reload();
 await page.screenshot({path:path.join(out,'stale.png'),fullPage:true});
 await check('stale row and summary agree',async()=>{
   assert.equal(await page.locator('#confirmedCount').textContent(),'0 批');assert.equal(await page.locator('#pendingCount').textContent(),'1 批');
   assert.match(await page.locator('#batchRows').textContent(),/尚未入库/);
 });
 assert.deepEqual(await page.evaluate(key=>JSON.parse(localStorage.getItem(key)),key),stale);
 const quota=await context.newPage();quota.on('pageerror',e=>errors.push(String(e)));
 await quota.goto(origins[1]+'/inbound-batches.html');
 await quota.evaluate(key=>{const set=Storage.prototype.setItem;Storage.prototype.setItem=function(k,v){if(k===key || k==='supply-chain-inbound-batch-timing-v4')throw new DOMException('synthetic quota','QuotaExceededError');return set.call(this,k,v);};},key);
 await quota.locator('[name=anchorAt]').fill('2026-09-06T09:00');await quota.locator('[name=estimatedSellableDate]').fill('2026-09-13');
 await quota.locator('[name=sourceNote]').fill('synthetic operator evidence');await quota.locator('[data-action=save]').click();
 await check('save failure retains input and persisted state',async()=>{
  assert.match(await quota.locator('#pageMessage').textContent(),/尚未保存/);
  assert.equal(await quota.locator('[name=sourceNote]').inputValue(),'synthetic operator evidence');assert.equal(await quota.evaluate(key=>localStorage.getItem(key),key),null);
 });
 await quota.locator('nav [data-region=TH]').click();
 await quota.screenshot({path:path.join(out,'failed-save.png'),fullPage:true});
 await check('failed save cannot become confirmed after actual rerender',async()=>{
  assert.equal(await quota.locator('#confirmedCount').textContent(),'0 批');assert.equal(await quota.locator('#pendingCount').textContent(),'1 批');
  assert.ok(!(await quota.locator('#batchRows').textContent()).includes('已人工确认实际入库'));
 });
 await check('no page errors or writes',async()=>{assert.deepEqual(errors,[]);assert.ok(requests.every(r=>r.method==='GET'));});
 if(process.argv[4]==='migration') {
 const currentKey='supply-chain-inbound-batch-timing-v4';
 const valid={[`TH:${batch.batchId}`]:{anchorAt:'2026-09-06T09:00',estimatedSellableDate:'2026-09-13',
  basePlanConfirmedAt:batch.estimatedSellableConfirmedAt,updatedAt:'2026-09-06T10:00:00Z',sourceNote:'exact synthetic warehouse event'}};
 await page.evaluate(({key,valid})=>localStorage.setItem(key,JSON.stringify(valid)),{key,valid});await page.reload();
 await page.locator('.override-transfer summary').click();await page.locator('#exportOverrides').click();
 await page.waitForFunction(()=>document.querySelector('#exportText').value.length>0);
 const exported=await page.locator('#exportText').inputValue();fs.writeFileSync(path.join(out,'exported.json'),exported);
 const target=await context.newPage();target.on('pageerror',e=>errors.push(String(e)));await target.goto(origins[1]+'/inbound-batches.html');
 const preserved={'TH:LOCAL-ONLY':{sourceNote:'unrelated original destination storage'}};
 await target.evaluate(({key,preserved})=>localStorage.setItem(key,JSON.stringify(preserved)),{key,preserved});await target.reload();
 await target.locator('.override-transfer summary').click();
 async function previewInput(value){await target.locator('#importText').fill(value);await target.locator('#previewImport').click();await target.waitForFunction(()=>!document.querySelector('#previewImport').disabled);}
 const originalDestination=await target.evaluate(key=>localStorage.getItem(key),key);
 await previewInput(exported);
 await check('actual export and import preview bind exact source without writes',async()=>{
   assert.match(await target.locator('#importRows').textContent(),/APPLICABLE/);assert.equal(await target.evaluate(key=>localStorage.getItem(key),currentKey),null);
   const b=JSON.parse(exported);assert.equal(b.entries.length,1);assert.deepEqual(b.entries[0].binding.skuQuantities,batch.skuQuantities);assert.equal(b.source.origin,origins[0]);
 });
 await target.locator('#applyImport').click();
 await check('two-origin import persists and old stores survive',async()=>{
   assert.match(await target.locator('#transferMessage').textContent(),/已导入 1 项/);assert.equal(await target.locator('#confirmedCount').textContent(),'1 批');
   assert.equal(await target.evaluate(key=>localStorage.getItem(key),key),originalDestination);
   assert.deepEqual(await page.evaluate(key=>JSON.parse(localStorage.getItem(key)),key),valid);
 });
 await target.screenshot({path:path.join(out,'imported-desktop.png'),fullPage:true});
 const firstWrite=await target.evaluate(key=>localStorage.getItem(key),currentKey);
 await previewInput(exported);
 await check('duplicate import is a no-write state',async()=>{assert.match(await target.locator('#importRows').textContent(),/DUPLICATE/);assert.equal(await target.locator('#applyImport').isDisabled(),true);assert.equal(await target.evaluate(key=>localStorage.getItem(key),currentKey),firstWrite);});
 await target.locator('#undoImport').click();
 await check('undo restores destination before import without touching legacy',async()=>{
   assert.equal(await target.locator('#confirmedCount').textContent(),'0 批');assert.deepEqual(await target.evaluate(key=>JSON.parse(localStorage.getItem(key)).values,currentKey),preserved);
   assert.equal(await target.evaluate(key=>localStorage.getItem(key),key),originalDestination);
 });
 await target.locator('[name=anchorAt]').fill('2026-09-06T08:00');await target.locator('[name=estimatedSellableDate]').fill('2026-09-14');
 await target.locator('[name=sourceNote]').fill('destination separate confirmation');await target.locator('[data-action=save]').click();
 const beforeConflict=await target.evaluate(key=>localStorage.getItem(key),currentKey);await previewInput(exported);
 await target.locator('#applyImport').click();
 await check('conflict defaults to keeping destination',async()=>{
   assert.match(await target.locator('#importRows').textContent(),/CONFLICT/);assert.equal(await target.locator('[data-import-index]').isChecked(),false);
   assert.equal(await target.evaluate(key=>localStorage.getItem(key),currentKey),beforeConflict);
 });
 await target.locator('[data-import-index]').check();await target.locator('#applyImport').click();
 await check('explicit conflict replacement is reversible',async()=>{
   assert.equal(await target.locator('[name=estimatedSellableDate]').inputValue(),'2026-09-13');
   await target.locator('#undoImport').click();assert.equal(await target.locator('[name=estimatedSellableDate]').inputValue(),'2026-09-14');
 });
 const stable=await target.evaluate(key=>localStorage.getItem(key),currentKey);
 const canonical=v=>JSON.stringify(Array.isArray(v)?v.map(x=>JSON.parse(canonical(x))):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,JSON.parse(canonical(v[k]))])):v);
 const hash=v=>crypto.createHash('sha256').update(canonical(v)).digest('hex');
 for(const [name,mutate,expected] of [
  ['wrong exact batch',b=>{b.entries[0].binding.batchId='OTHER-COMPLETE-BATCH';},'IDENTITY_MISMATCH'],
  ['changed exact SKU allocation',b=>{b.entries[0].binding.skuQuantities={'0001':1,'0002':2};},'STALE_PLAN'],
  ['old source confirmation version',b=>{b.entries[0].binding.estimatedSellableConfirmedAt='2026-09-03T00:00:00Z';},'STALE_PLAN'],
 ]){
   const b=JSON.parse(exported);mutate(b);b.entries[0].sourceBatchDigest=hash(b.entries[0].binding);delete b.digest;b.digest=hash(b);
   await previewInput(JSON.stringify(b));await check(name+' rejected without persistence',async()=>{
    assert.match(await target.locator('#importRows').textContent(),new RegExp(expected));assert.equal(await target.locator('#applyImport').isDisabled(),true);
    assert.equal(await target.evaluate(key=>localStorage.getItem(key),currentKey),stable);
   });
 }
 const tampered=JSON.parse(exported);tampered.entries[0].confirmation.sourceNote='modified without digest';await previewInput(JSON.stringify(tampered));
 await check('bad envelope digest cannot import',async()=>{assert.match(await target.locator('#transferMessage').textContent(),/摘要不一致/);assert.equal(await target.locator('#applyImport').isDisabled(),true);});
 await previewInput(exported);
 const concurrent=await context.newPage();await concurrent.goto(origins[1]+'/inbound-batches.html');
 await concurrent.locator('[name=anchorAt]').fill('2026-09-06T08:30');await concurrent.locator('[name=estimatedSellableDate]').fill('2026-09-15');
 await concurrent.locator('[name=sourceNote]').fill('concurrent explicit confirmation');await concurrent.locator('[data-action=save]').click();
 await target.waitForFunction(()=>document.querySelector('#applyImport').disabled);
 await check('another real tab invalidates preview and retains input',async()=>{
  assert.match(await target.locator('#transferMessage').textContent(),/其他页面已修改/);assert.equal(await target.locator('#importText').inputValue(),exported);
  assert.equal(await target.evaluate(key=>JSON.parse(localStorage.getItem(key)).values['TH:TEST-BATCH-001'].estimatedSellableDate,currentKey),'2026-09-15');
 });
 await target.setViewportSize({width:390,height:844});await target.screenshot({path:path.join(out,'migration-mobile.png'),fullPage:true});
 await check('narrow screen preserves operable controls without page overflow',async()=>{
  assert.ok(await target.locator('#previewImport').isVisible());assert.ok(await target.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 });
 await check('final no browser errors or network writes',async()=>{assert.deepEqual(errors,[]);assert.ok(requests.every(r=>r.method==='GET'));});
 }
 fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({layer:'actual isolated Chromium, two synthetic origins, no user browser storage',origins,checks,errors,requests,failures},null,2));
 await browser.close();await Promise.all(servers.map(s=>new Promise(r=>s.close(r))));console.log(JSON.stringify({checks:checks.length,failures,out}));process.exitCode=failures.length?1:0;
})().catch(error=>{console.error(error);process.exit(2);});
