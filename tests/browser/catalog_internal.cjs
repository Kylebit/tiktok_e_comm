const fs=require('node:fs'),assert=require('node:assert/strict'),{chromium}=require('C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const state=process.argv[2],ready=JSON.parse(fs.readFileSync(state+'/ready.json')),base='http://127.0.0.1:'+ready.port;
(async()=>{const browser=await chromium.launch({headless:true,executablePath:'C:/Users/Windows11/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe'}),context=await browser.newContext(),page=await context.newPage(),posts=[],errors=[],checks=[];
await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():r.abort());page.on('request',r=>{if(r.method()==='POST')posts.push(r.url());});page.on('pageerror',e=>errors.push(String(e)));
try{
const d=await(await context.request.get(base+'/api/catalog/skus?q=0001')).json();assert.equal(d.ok,true);assert.equal(d.total,1);const item=d.items[0];assert.equal(item.sku,'0001');assert.equal(item.member_count,7);assert.ok(item.image_candidates.length>1);
const first=item.image_candidates[0].key;await page.route('**/api/catalog/image?key='+first,r=>r.fulfill({status:502,contentType:'application/json',body:'{"ok":false}'}));
for(const width of [1440,390]){await page.setViewportSize({width,height:900});await page.goto(base+'/catalog');await page.waitForSelector('tr[data-entity]');
assert.deepEqual(await page.locator('thead th').allTextContents(),['主图','SKU','商品名称','规格','成本价（¥）','重量（g）']);
await page.locator('#skuQ').fill('0001');await page.locator('#catalogFilters button[type=submit]').click();await page.waitForFunction(()=>document.querySelector('#listMeta').textContent.startsWith('共 1 '));
assert.equal(await page.locator('tr[data-entity]').count(),1);assert.equal(await page.locator('.cost-input').count(),1);assert.equal(await page.locator('.cost-input').inputValue(),'13.5');
assert.ok(!(await page.locator('body').innerText()).includes('独立记录'));
await page.waitForFunction(()=>{const i=document.querySelector('.product-image');return i&&i.complete&&i.naturalWidth>0;},{},{timeout:45000});
const image=await page.locator('.product-image').evaluate(i=>({key:new URL(i.src).searchParams.get('key'),width:i.naturalWidth,height:i.naturalHeight}));assert.notEqual(image.key,first);assert.ok(item.image_candidates.some(c=>c.key===image.key));
await page.screenshot({path:state+'/internal-0001-'+width+'.png'});
await page.locator('#skuQ').fill('770001');await page.locator('#catalogFilters button[type=submit]').click();await page.waitForFunction(()=>document.querySelector('#listMeta').textContent.startsWith('共 1 '));assert.equal(await page.locator('tr[data-entity]').count(),1);
checks.push({width,sku:'0001',rows:1,cost_inputs:1,image,first_image_failure_fell_back:true});}
assert.deepEqual(posts,[]);assert.deepEqual(errors,[]);fs.writeFileSync(state+'/INTERNAL_BROWSER.json',JSON.stringify({checks,posts,errors,members:item.member_count,sku:item.sku,aliases:item.aliases},null,2));console.log(JSON.stringify({checks:checks.length,members:item.member_count}));
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
