const {chromium}=require('playwright'), assert=require('assert');
const base=process.argv[2], out=process.argv[3];
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1000}});
 const page=await context.newPage(), errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',route=>{
  assert(route.request().url().startsWith(base),'No external request');
  assert.equal(route.request().method(),'GET','Read-only page');
  return route.continue();
 });
 try {
  await page.goto(base+'/profit-partial-review');
  const basis=page.locator('#historicalReportBasis');
  assert.equal(await basis.getAttribute('open'),null);
  const frame=page.frameLocator('iframe');
  await frame.locator('tbody tr').last().waitFor();
  assert.equal(await frame.locator('tbody tr').count(),428);
  const initial=await frame.locator('.cards').innerText();
  await basis.locator(':scope > summary').click();
  assert((await basis.innerText()).includes('没有明细渲染器'));
  assert((await basis.innerText()).includes('0.2218 CNY / THB'));
  assert((await basis.innerText()).includes('观察时间不是成本生效时间'));
  await basis.locator('.basis-checksums > summary').click();
  assert.equal(await basis.locator('code').count(),6);
  assert.equal(await basis.locator('a,iframe,input,button').count(),0);
  assert.equal(await frame.locator('.cards').innerText(),initial);
  assert.equal(await frame.locator('tbody tr').count(),428);
  for(const width of [1440,390]){
   await page.setViewportSize({width,height:1000});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   await page.screenshot({path:out+'/history-basis-'+width+'.png',fullPage:true});
  }
  await basis.locator(':scope > summary').click();
  assert.equal(await basis.getAttribute('open'),null);
  await page.locator('iframe').scrollIntoViewIfNeeded();
  await page.screenshot({path:out+'/unchanged-detail-390.png'});
  await frame.locator('[data-role="date-start"]').fill('2026-07-31');
  await frame.locator('[data-role="date-end"]').fill('2026-07-31');
  assert((await frame.locator('[data-role="summary"]').innerText()).includes('214 个订单，226 个商品行'));
  assert.equal(await frame.locator('.cards').innerText(),initial);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({requests_are_read_only:true,errors}));
 } finally {await browser.close();}
})();
