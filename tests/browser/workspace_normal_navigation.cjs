const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
(async()=>{
 const executablePath=process.env.ORBIT_BROWSER_EXECUTABLE||process.env.ORBIT_CHROMIUM_BIN;
 const browser=await chromium.launch({headless:true,...(executablePath?{executablePath}:{channel:'chrome'})});
 const [base,out]=process.argv.slice(2),posts=[],errors=[],scenarios=[];
 for(const scenario of ['clean','retained']){
  const context=await browser.newContext({viewport:scenario==='clean'?{width:1440,height:1000}:{width:390,height:844}});
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST')posts.push(r.url());});
  async function nav(name){if(await page.locator('#orbitMenu').isVisible())await page.locator('#orbitMenu').click();await page.getByRole('link',{name,exact:true}).click();}
  async function historyOffer(){if(await page.locator('#productHistory').isHidden())await page.locator('#openProductHistory').click();const offer=page.locator('[data-history-offer="123"]');await offer.waitFor();return offer;}
  async function ready(){await page.waitForFunction(()=>document.querySelector('#factsEditSellerSku').value==='0001'&&document.querySelector('#productDetail').getAttribute('aria-busy')==='false');}
  await page.goto(base+'/');await nav('商品上架');
  await historyOffer();
  assert(!new URL(page.url()).searchParams.has('offer_id'));
  assert(await page.locator('#noProductSelected').isVisible());
  if(scenario==='retained'){
   await page.locator('#offerId').fill('99003');await page.getByRole('button',{name:'加入并读取',exact:true}).click();
   await page.waitForFunction(()=>document.querySelector('#productTitle').textContent==='未能读取该商品');
    await nav('商品目录');
    await nav('商品上架');
    await historyOffer();
   assert((await page.locator('#queueGrid').innerText()).includes('99003'));
   assert(!new URL(page.url()).searchParams.has('offer_id'));
  }
  await (await historyOffer()).click();
  await ready();
  assert.equal(new URL(page.url()).searchParams.get('offer_id'),'123');
  assert((await page.locator('#round1Category').innerText()).includes('42'));
  assert((await page.locator('#currentReviewSummary').innerText()).includes('2 个目标'));
  assert.equal(await page.locator('#factsEditSellerSku').inputValue(),'0001');
  assert(await page.locator('#originalPublicationReview').isVisible());
  assert.equal(await page.locator('[data-original-round]').count(),3);
  await page.reload();await ready();
  await page.goBack();await page.locator('#noProductSelected').waitFor({state:'visible'});
  await page.goForward();await ready();
  if(scenario==='retained'){
   await page.locator('#queueDisclosure > summary').click();
   await page.locator('[data-action="switch"][data-key="99003"]').click();
   await page.waitForFunction(()=>document.querySelector('#productTitle').textContent==='未能读取该商品');
   assert.equal(new URL(page.url()).searchParams.get('offer_id'),'99003');
   assert.equal(await page.locator('#currentReviewSummary').innerText(),'');
   await (await historyOffer()).click();
   await ready();
  }
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth+1);assert(!overflow,'page overflow');
  await page.screenshot({path:path.join(out,scenario+'.png'),fullPage:true});scenarios.push(scenario);
  await context.close();
 }
 await browser.close();console.log(JSON.stringify({posts,errors,scenarios}));
})().catch(e=>{console.error(e);process.exit(1);});
