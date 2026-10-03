const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto'),{chromium}=require('playwright');
const base=process.argv[2],out=process.argv[3];
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.ORBIT_BROWSER_EXECUTABLE});
 const context=await browser.newContext({viewport:{width:1440,height:900}}),page=await context.newPage();
 const errors=[],consoleErrors=[],responses=[],blocked=[];
 await context.route('**/*',route=>{if(new URL(route.request().url()).origin===new URL(base).origin)return route.continue();blocked.push(route.request().url());return route.abort();});
 page.on('pageerror',e=>errors.push(String(e)));page.on('console',m=>{if(m.type()==='error')consoleErrors.push(m.text());});
 page.on('response',r=>responses.push({url:r.url(),status:r.status()}));
 try{
  const response=await page.goto(base+'/costs');assert.equal(new URL(page.url()).pathname,'/catalog');assert.equal(response.status(),200);
  await page.waitForSelector('tr[data-entity]');
  assert.equal(await page.locator('a[href="/costs"],a[href="/costs.html"]').count(),0);
  const buttons=page.locator('.cost-save');assert.equal(await buttons.count(),1);
  const button=buttons.filter({hasNotText:'never-used'}).first();
  const directory=await(await context.request.get(base+'/api/catalog/skus')).json();
  const index=directory.items.findIndex(r=>r.cost.sources[0].identity.shop_key==='101');
  assert.ok(index>=0);const control=buttons.nth(index),input=control.locator('..').locator('.cost-input');
  assert.equal(await input.inputValue(),'8');await input.fill('31');
  const saved=page.waitForResponse(r=>r.url().endsWith('/api/catalog/cost')&&r.request().method()==='POST');
  await control.click();const savedResponse=await saved;assert.equal(savedResponse.status(),200);assert.equal((await savedResponse.json()).version,2);
  await page.reload();await page.waitForSelector('tr[data-entity]');
  const values=Object.fromEntries(await Promise.all(directory.items.map(async r=>[r.cost.sources[0].identity.shop_key,await page.locator('tr[data-entity="'+r.entity_key+'"]').locator('input').inputValue()])));
  assert.deepEqual(values,{'101':'31'});
  await page.screenshot({path:path.join(out,'catalog-1440.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  const mobile=page.locator('.cost-input').first();await mobile.scrollIntoViewIfNeeded();
  assert.equal(await mobile.isVisible(),true);
  await page.screenshot({path:path.join(out,'catalog-390.png'),fullPage:false});
  const legacy=await context.request.get(base+'/costs.html',{maxRedirects:0});assert.equal(legacy.status(),308);assert.equal(legacy.headers().location,'/catalog');
  const raw=await (await context.request.get(base+'/catalog')).body();
  assert.deepEqual(errors,[]);assert.deepEqual(consoleErrors,[]);assert.deepEqual(blocked,[]);
  fs.writeFileSync(path.join(out,'CATALOG_BROWSER.json'),JSON.stringify({values,errors,consoleErrors,responses,blocked,catalogRawSha256:crypto.createHash('sha256').update(raw).digest('hex')},null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
