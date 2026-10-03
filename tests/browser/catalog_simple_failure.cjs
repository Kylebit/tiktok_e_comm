const {chromium}=require('C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs'),assert=require('node:assert/strict');
const state=process.argv[2],ready=JSON.parse(fs.readFileSync(state+'/ready.json')),base='http://127.0.0.1:'+ready.port;
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'C:/Users/Windows11/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe'}),results=[];
 try{
 for(const width of [1440,390])for(const scenario of ['mode-abort','mode-500','mode-malformed','mode-not-ok','products-missing','products-conflict','nav-abort','nav-500','nav-malformed']){
  const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage(),posts=[],errors=[];let repair=false;
  page.on('request',r=>{if(r.method()==='POST')posts.push(new URL(r.url()).pathname);});page.on('pageerror',e=>errors.push(String(e)));
  await context.route('**/*',async route=>{
   const url=new URL(route.request().url());if(url.origin!==base)return route.abort();
   const target=scenario.startsWith('nav-')?'/api/orbit/navigation':scenario.startsWith('products-')?'/api/catalog/skus':'/api/catalog/data-mode';
   if(repair||url.pathname!==target)return route.continue();
   if(scenario.endsWith('abort'))return route.abort();
   if(scenario.endsWith('500'))return route.fulfill({status:500,contentType:'application/json',body:'{"ok":false}'});
   if(scenario.endsWith('malformed'))return route.fulfill({status:200,contentType:'application/json',body:'{}'});
   if(scenario==='mode-not-ok')return route.fulfill({status:200,contentType:'application/json',body:'{"ok":false,"review_copy":null}'});
   const response=await route.fetch(),data=await response.json();
   if(scenario==='products-missing')delete data.review_copy;else data.review_copy=null;
   return route.fulfill({response,json:data});
  });
  await page.goto(base+'/catalog');await page.waitForSelector('#orbitCoreNav a',{state:'attached'});
  assert.equal(await page.locator('#orbitCoreNav a').count(),5);
  assert.equal(await page.locator('#orbitCoreNav a[aria-current="page"]').getAttribute('data-core'),'catalog');
  assert.ok(await page.locator('#orbitCoreNav a').evaluateAll(nodes=>nodes.every(n=>!n.target)));
  if(scenario.startsWith('nav-')){
   await page.waitForSelector('#orbitNavStatus button');await page.waitForSelector('tr[data-entity]');
   assert.equal(await page.locator('#tbody tr').count(),50);
  }else{
   await page.waitForFunction(()=>document.querySelector('#catalogModeError span').textContent.includes('失败')||document.querySelector('#catalogModeError span').textContent.includes('冲突')||document.querySelector('#catalogModeError span').textContent.includes('无效'));
   assert.equal(await page.locator('.catalog-sync-governance').isVisible(),false);
   assert.ok(await page.locator('.catalog-page input,.catalog-page select,.catalog-page button:not(#catalogModeRetry)').evaluateAll(nodes=>nodes.every(n=>n.disabled)));
   assert.equal(await page.locator('tr[data-entity]').count(),0);
  }
  assert.deepEqual(posts,[]);repair=true;
  if(scenario.startsWith('nav-')){await page.locator('#orbitNavStatus button').click();await page.waitForFunction(()=>document.querySelector('#orbitNavStatus').textContent==='');}
  else await page.locator('#catalogModeRetry').click();
  await page.waitForSelector('tr[data-entity]');assert.equal(await page.locator('#tbody tr').count(),50);
  assert.equal(await page.locator('.catalog-sync-governance').isVisible(),false);assert.equal(await page.locator('.cost-input').first().isDisabled(),false);
  assert.deepEqual(posts,[]);assert.deepEqual(errors,[]);
  results.push({width,scenario,links:5,posts:0,recovered_rows:50});await context.close();
 }
 fs.writeFileSync(state+'/SIMPLE_FAILURE_BROWSER.json',JSON.stringify({results},null,2));console.log(JSON.stringify({passed:results.length}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
