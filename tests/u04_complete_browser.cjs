const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {chromium}=require('C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const [origin,out,one,widthArg='1440']=process.argv.slice(2),width=Number(widthArg);
const delay=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.ORBIT_BROWSER_EXECUTABLE?{executablePath:process.env.ORBIT_BROWSER_EXECUTABLE}:{channel:'chrome'})});
 try{
  const context=await browser.newContext({viewport:{width,height:width===390?844:900}}),page=await context.newPage(),errors=[],responses=[],pending=[];
  await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
  page.on('pageerror',error=>errors.push(error.message));
  page.on('response',response=>{
   if(response.url().includes('/captured/'))pending.push(response.body().then(body=>responses.push({url:response.url(),status:response.status(),sha256:crypto.createHash('sha256').update(body).digest('hex')})));
  });
  await page.goto(origin+'/supply-chain/');
  await page.waitForFunction(()=>window.SUPPLY_CHAIN_CAPTURE_STATE?.status==='COMPLETE');
  assert.equal(await page.evaluate(()=>window.SUPPLY_CHAIN_CAPTURE_STATE.version),one);
  const storageKey=await page.evaluate(()=>window.SUPPLY_CHAIN_OVERRIDES.KEY);
  const stored=await page.evaluate(()=>{
   const api=window.SUPPLY_CHAIN_OVERRIDES,data=window.SUPPLY_CHAIN_DATA,plan=window.SUPPLY_CHAIN_INBOUND_PLAN;
   const batch={...plan.regions.MY.batches[0],region:'MY'},key='MY:'+batch.batchId;
   api.write(localStorage,api.read(localStorage),{[key]:{anchorAt:'2026-09-06T09:00',estimatedSellableDate:'2026-09-13',sourceNote:'synthetic saved overlay',updatedAt:'2026-09-06T10:00:00Z',binding:api.basis(batch,data,plan)}});
   return localStorage.getItem(api.KEY);
  });
  let held=false;
  await page.route('**/captured/*/inbound-plan.js',async route=>{
   if(!held){held=true;fs.writeFileSync(path.join(out,'between-resources.flag'),'held');const end=Date.now()+20000;while(!fs.existsSync(path.join(out,'advance.json'))){assert.ok(Date.now()<end,'advance timeout');await delay(25);}}
   await route.continue();
  });
  await page.reload();await page.waitForFunction(()=>window.SUPPLY_CHAIN_CAPTURE_STATE?.status==='COMPLETE');
  const state=await page.evaluate(()=>({version:window.SUPPLY_CHAIN_CAPTURE_STATE.version,available:window.SUPPLY_CHAIN_DATA.countries.MY[0].inventory.available,anchor:window.SUPPLY_CHAIN_INBOUND_PLAN.regions.MY.batches[0].estimatedAnchorAt,stored:localStorage.getItem(window.SUPPLY_CHAIN_OVERRIDES.KEY)}));
  assert.equal(state.version,one);assert.equal(state.available,5);assert.equal(state.anchor,'2026-09-05T00:00:00+00:00');assert.equal(state.stored,stored);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'supply overflow');
  await page.screenshot({path:path.join(out,'browser-interleaved-old.png'),fullPage:true});
  const two=JSON.parse(fs.readFileSync(path.join(out,'advance.json'))).version;
  await page.goto(origin+'/supply-chain/inbound-batches.html');await page.waitForFunction(()=>window.SUPPLY_CHAIN_CAPTURE_STATE?.status==='COMPLETE');
  const newer=await page.evaluate(()=>({version:window.SUPPLY_CHAIN_CAPTURE_STATE.version,available:window.SUPPLY_CHAIN_DATA.countries.MY[0].inventory.available,anchor:window.SUPPLY_CHAIN_INBOUND_PLAN.regions.MY.batches[0].estimatedAnchorAt,stored:localStorage.getItem(window.SUPPLY_CHAIN_OVERRIDES.KEY),text:document.body.innerText}));
  assert.equal(newer.version,two);assert.equal(newer.available,6);assert.equal(newer.anchor,'2026-09-06T00:00:00+00:00');assert.equal(newer.stored,stored);assert.ok(newer.text.includes('MY-SYNTHETIC-BATCH'));
  await page.screenshot({path:path.join(out,'browser-new-inbound.png'),fullPage:true});
  await Promise.all(pending);assert.deepEqual(errors,[]);
  await page.route('**/captured/*/inbound-plan.js',route=>route.abort());
  await page.goto(origin+'/supply-chain/');await page.waitForFunction(()=>window.SUPPLY_CHAIN_CAPTURE_STATE?.status==='UNAVAILABLE');
  assert.equal(await page.evaluate(key=>localStorage.getItem(key),storageKey),stored);
  assert.ok((await page.locator('body').innerText()).includes('暂不可用'));
  await page.screenshot({path:path.join(out,'browser-unavailable.png')});
  fs.writeFileSync(path.join(out,'browser-result.json'),JSON.stringify({width,interleavedVersion:state.version,newVersion:newer.version,overlayPreserved:true,resourceFailureUnavailable:true,pageErrors:errors,responses,overlayBytes:stored},null,2));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
