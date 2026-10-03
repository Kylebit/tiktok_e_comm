const {chromium}=require('C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs'),assert=require('node:assert/strict');
const state=process.argv[2],ready=JSON.parse(fs.readFileSync(state+'/ready.json')),base='http://127.0.0.1:'+ready.port;
(async()=>{const browser=await chromium.launch({headless:true,executablePath:'C:/Users/Windows11/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe'});
const context=await browser.newContext(),page=await context.newPage(),errors=[],images=[],posts=[],decoded=[];
await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():r.abort());
page.on('pageerror',e=>errors.push(String(e)));page.on('request',r=>{if(r.method()==='POST')posts.push(new URL(r.url()).pathname);});
page.on('response',r=>{if(new URL(r.url()).pathname==='/api/catalog/image')images.push({http:r.status(),mime:r.headers()['content-type']});});
try{
 const response=await context.request.get(base+'/api/catalog/skus');assert.equal(response.status(),200);const data=await response.json();assert.equal(data.ok,true);assert.ok(data.total>0);
 for(const width of [1440,390]){
  await page.setViewportSize({width,height:900});await page.goto(base+'/catalog');await page.waitForSelector('tr[data-entity]');
  assert.deepEqual(await page.locator('thead th').allTextContents(),['主图','SKU','商品名称','规格','成本价（¥）','重量（g）']);
  assert.equal(await page.locator('tr[data-entity]').count(),50);assert.equal(await page.locator('.cost-input').count(),50);
  assert.equal(await page.locator('.inline-sku-fill,.catalog-sync-governance,.tk-sku-push-chk,.btn-open-tk-sku').count(),0);
  await page.waitForFunction(()=>[...document.querySelectorAll('.product-image')].some(i=>i.complete&&i.naturalWidth>0),{},{timeout:45000});
  await page.waitForFunction(()=>[...document.querySelectorAll('.product-image')].filter(i=>i.getBoundingClientRect().top<innerHeight&&i.getBoundingClientRect().bottom>0).every(i=>i.complete&&i.naturalWidth>0),{},{timeout:45000});
  decoded.push(...await page.locator('.product-image').evaluateAll(nodes=>nodes.filter(i=>i.complete&&i.naturalWidth>0).map(i=>({key:new URL(i.src).searchParams.get('key'),width:i.naturalWidth,height:i.naturalHeight}))));
  await page.screenshot({path:state+'/simple-'+width+'.png'});
  await page.locator('#nextPage').click();await page.waitForFunction(()=>document.querySelector('#pageNumber').textContent.startsWith('2 /'));
  assert.equal(await page.locator('tr[data-entity]').count(),50);
  await page.locator('#skuQ').fill('660018');await page.locator('#catalogFilters button[type=submit]').click();await page.waitForFunction(()=>!document.querySelector('#listMeta').textContent.includes('正在'));
  await page.waitForFunction(()=>document.querySelector('#listMeta').textContent.includes('共 1 '));
  assert.equal(await page.locator('.cost-input').count(),1);assert.equal(await page.locator('.cost-input').inputValue(),'');assert.ok((await page.locator('.cost-conflict').innerText()).includes('5.5'));
  await page.screenshot({path:state+'/conflict-'+width+'.png'});
 }
 assert.ok(images.some(r=>r.http===200&&/^image\/(jpeg|png|webp|avif)/.test(r.mime)));
 const imageResults=images.slice();
 await page.route('**/api/catalog/image?*',route=>route.fulfill({status:502,contentType:'application/json',body:'{"ok":false}'}));
 await page.reload();await page.waitForSelector('.image-failure');assert.ok((await page.locator('.image-failure').first().innerText()).includes('原图暂不可用'));
 assert.deepEqual(errors,[]);assert.deepEqual(posts,[]);
 const result={total:data.total,coverage:data.coverage,headers:6,cost_inputs_per_row:1,images:imageResults,decoded,errors,posts,pagination:true,search:true,conflict_not_selected:true,injected_image_failure_visible:true};
 fs.writeFileSync(state+'/SIMPLE_BROWSER.json',JSON.stringify(result,null,2));console.log(JSON.stringify({total:data.total,coverage:data.coverage,real_images:images.filter(r=>r.http===200).length,failed_images:images.filter(r=>r.http!==200).length}));
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
