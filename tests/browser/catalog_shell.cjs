const {chromium}=require('C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const state=process.argv[2],out=process.argv[3]||state,ready=JSON.parse(fs.readFileSync(state+'/ready.json'));
const base='http://127.0.0.1:'+ready.port;
(async()=>{
const browser=await chromium.launch({headless:true,executablePath:'C:/Users/Windows11/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe'});
const context=await browser.newContext(),page=await context.newPage(),errors=[],blocked=[],checks=[],posts=[];
page.on('request',r=>{if(r.method()==='POST')posts.push(new URL(r.url()).pathname);});
await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():(blocked.push(r.request().url()),r.abort()));
page.on('pageerror',e=>errors.push(String(e)));
try{
 const response=await context.request.get(base+'/api/catalog/products?limit=500');assert.equal(response.status(),200);
 const data=await response.json();assert.equal(data.ok,true);assert.ok(data.total>0);assert.equal(data.items.length,data.total);
 assert.ok(data.items.some(r=>r.tiktok||r.shopee||r.identity));assert.equal(data.review_copy.mode,'LOCAL_REVIEW_COPY');
 const paged=await(await context.request.get(base+'/api/catalog/products?limit=2&offset=2')).json();assert.equal(paged.items.length,2);assert.equal(paged.total,data.total);assert.deepEqual(paged.items,data.items.slice(2,4));
 const expected=['商品目录','商品上架','供应链','利润','知识工具'];
 const targets=[['catalog','/catalog'],['product','/product-workspace'],['supply-chain','/supply-chain/'],['data','/profit'],['knowledge','/knowledge']];
 for(const width of [1440,390]){
  await page.setViewportSize({width,height:900});await page.goto(base+'/');await page.waitForURL(base+'/catalog');
  await page.waitForSelector('#tbody tr[data-key]');assert.equal(await page.locator('#tbody tr').count(),data.total);
  assert.ok(await page.locator('.cost-input').count()>0);assert.ok(await page.locator('#catalogCopyNote').isVisible());
  assert.equal(await page.locator('.cost-input').first().isDisabled(),false);
  const writeControls=page.locator('.btn-save-tk-sku,.btn-save-sp-sku,.tk-sku-push-chk,.sp-sku-push-chk,.tk-sku-fill-input,.sp-sku-fill-input');
  assert.ok(await writeControls.count()>0);
  assert.ok(await writeControls.evaluateAll(nodes=>nodes.every(n=>n.disabled)));
  await page.locator('.tk-sku-fill-input,.sp-sku-fill-input').evaluateAll(nodes=>nodes.forEach(n=>n.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))));
  await page.locator('.btn-save-tk-sku,.btn-save-sp-sku').evaluateAll(nodes=>nodes.forEach(n=>n.dispatchEvent(new MouseEvent('click',{bubbles:true}))));
  assert.deepEqual(posts,[]);
  assert.ok(!(await page.locator('body').innerText()).includes('database not found'));
  await page.screenshot({path:out+'/catalog-'+width+'.png'});
  await page.locator('.cost-input').first().scrollIntoViewIfNeeded();assert.ok(await page.locator('.cost-input').first().isVisible());
  await page.screenshot({path:out+'/catalog-cost-'+width+'.png'});
  for(const [key,url] of targets){
   if(width===390)await page.locator('#orbitMenu').click();
   await page.waitForSelector('#orbitCoreNav a');assert.deepEqual(await page.locator('#orbitCoreNav a').allTextContents(),expected);
   await page.locator('#orbitCoreNav a[data-core="'+key+'"]').click();await page.waitForURL(base+url);
   await page.waitForSelector('#orbitCoreNav a[aria-current="page"]',{state:'attached'});
   assert.equal(await page.locator('#orbitCoreNav a[aria-current="page"]').getAttribute('data-core'),key);
   assert.equal(context.pages().length,1);assert.equal(await page.locator('.operations-sidebar,header.shell,header.topbar nav.nav,.focus-grid,#domainCards,#relationshipFlow').count(),0);
   if(key==='knowledge'){await page.waitForSelector('.knowledge-row');assert.ok(await page.locator('.knowledge-row').count()>0);assert.equal(await page.locator('#entryError').isVisible(),false);assert.ok(await page.locator('#knowledgeDetailTitle').count()>0);}
   if(key==='catalog'){await page.waitForSelector('#tbody tr[data-key]');assert.equal(await page.locator('#tbody tr').count(),data.total);}
   await page.screenshot({path:out+'/'+key+'-'+width+'.png'});
   checks.push({width,key,url,active:true,same_tab:true});
  }
 }
 for(const [old,final] of [['/index.html','/catalog'],['/?view=product','/product-workspace'],['/?view=knowledge','/knowledge'],['/?view=supply-chain','/supply-chain/'],['/costs','/catalog'],['/costs.html','/catalog']]){
  const r=await context.request.get(base+old,{maxRedirects:0});assert.equal(r.status(),308);assert.equal(r.headers().location,final);
 }
 const denied=await context.request.post(base+'/api/catalog/shopee-sync-tk',{data:{}});assert.equal(denied.status(),403);
 assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);
 const raw=await(await context.request.get(base+'/catalog')).body();
 fs.writeFileSync(out+'/BROWSER.json',JSON.stringify({total:data.total,business_total:data.business_total,returned:data.items.length,cost_control_count:data.items.reduce((n,r)=>n+(r.cost_controls||[]).length,0),checks,errors,blocked,pagination:true,review_copy_write_controls_disabled:true,keyboard_and_click_posts:posts,cost_input_enabled:true,platform_post_status:403,catalog_raw_sha256:crypto.createHash('sha256').update(raw).digest('hex')},null,2));
 console.log(JSON.stringify({total:data.total,checks:checks.length,errors,blocked}));
}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
