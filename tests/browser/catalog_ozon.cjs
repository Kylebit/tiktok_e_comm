const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto'),{chromium}=require('playwright');
const base=process.argv[2],out=process.argv[3];
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.ORBIT_BROWSER_EXECUTABLE});
 const context=await browser.newContext({viewport:{width:1440,height:900}}),page=await context.newPage();
 const errors=[],consoleErrors=[],blocked=[];
 await context.route('**/*',route=>{if(new URL(route.request().url()).origin===new URL(base).origin)return route.continue();blocked.push(route.request().url());return route.abort();});
 page.on('pageerror',e=>errors.push(String(e)));page.on('console',m=>{if(m.type()==='error')consoleErrors.push(m.text());});
 try{
  assert.equal((await page.goto(base+'/catalog')).status(),200);
  await page.waitForSelector('tr[data-entity]');
  const buttons=page.locator('.cost-save');assert.equal(await buttons.count(),1);
  const directory=await(await context.request.get(base+'/api/catalog/skus')).json();
  const legacy=await(await context.request.get(base+'/api/catalog/products')).json();
  const identities=legacy.items.filter(r=>r.identity).map(r=>r.identity);
  assert.ok(identities.every(i=>i.platform==='ozon'&&i.identity_kind==='product_offer'&&!('variant_id' in i)));
  assert.deepEqual(identities.map(i=>i.shop_key).sort(),['101','102']);
  assert.equal(await page.locator('thead th').count(),6);
  const control=buttons.first();
  await control.locator('..').locator('.cost-input').fill('31');
  const saved=page.waitForResponse(r=>r.url().endsWith('/api/catalog/cost')&&r.request().method()==='POST');
  await control.click();assert.equal((await saved).status(),200);
  await page.reload();await page.waitForSelector('tr[data-entity]');
  const values=Object.fromEntries(await Promise.all(directory.items.map(async r=>[r.cost.sources[0].identity.shop_key,await page.locator('tr[data-entity="'+r.entity_key+'"]').locator('input').inputValue()])));
  assert.deepEqual(values,{'101':'31'});
  await page.screenshot({path:path.join(out,'ozon-1440.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.locator('.cost-input').first().scrollIntoViewIfNeeded();
  assert.ok(await page.locator('.cost-input').first().isVisible());
  await page.screenshot({path:path.join(out,'ozon-390.png')});
  const raw=await (await context.request.get(base+'/catalog')).body();
  assert.deepEqual(errors,[]);assert.deepEqual(consoleErrors,[]);assert.deepEqual(blocked,[]);
  fs.writeFileSync(path.join(out,'OZON_BROWSER.json'),JSON.stringify({identities,values,errors,consoleErrors,blocked,catalogRawSha256:crypto.createHash('sha256').update(raw).digest('hex')},null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
