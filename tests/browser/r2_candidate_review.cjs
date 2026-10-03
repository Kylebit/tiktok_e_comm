const {chromium}=require('playwright');
const assert=require('assert'), fs=require('fs');
const base=process.argv[2],out=process.argv[3];
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1080}});
 const page=await context.newPage(),errors=[],posts=[];
 let holding=null;
 context.on('page',p=>p.on('pageerror',e=>errors.push(e.message)));
 page.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',async route=>{
  const r=route.request();assert(r.url().startsWith(base),'No external browser request');
  if(r.method()==='POST'){
   assert(r.url().endsWith('/api/product-workspace/r2-candidate/decision'));
   posts.push(r.postDataJSON());
   if(holding){holding.route=route;return;}
  }return route.continue();
 });
 async function load(p){
  await p.goto(base+'/product-workspace?offer_id=123');
  await p.waitForFunction(()=>document.querySelectorAll('img[data-r2-image]').length===1&&document.querySelector('img[data-r2-image]').naturalWidth>0);
  await p.locator('#tab-images').click();await p.locator('#localizedImageResults').scrollIntoViewIfNeeded();
 }
 try{
  await load(page);assert.equal(posts.length,0);assert(await page.locator('#saveR2CandidateReviewButton').isDisabled());
  await page.locator('input[data-r2-choice="0"][value="keep"]').check();
  const before=await (await context.request.get(base+'/api/product-workspace/r2-candidate/review?offer_id=123')).json();
  assert.equal(before.review.keep_count,0);assert.equal(posts.length,0);
  const stale=await context.newPage();await load(stale);
  await stale.locator('input[data-r2-choice="0"][value="remove"]').check();
  holding={};await page.locator('#saveR2CandidateReviewButton').click();
  for(let i=0;i<100&&!holding.route;i++)await new Promise(r=>setTimeout(r,25));
  assert(holding.route);assert(await page.locator('#saveR2CandidateReviewButton').isDisabled());
  await page.locator('#saveR2CandidateReviewButton').evaluate(b=>{b.click();b.click();});assert.equal(posts.length,1);
  await holding.route.continue();holding=null;
  await page.locator('#localizedImageResultsMessage').filter({hasText:'图片选择已保存'}).waitFor();
  await load(page);assert(await page.locator('input[data-r2-choice="0"][value="keep"]').isChecked());
  await stale.locator('#saveR2CandidateReviewButton').click();
  await stale.locator('#localizedImageResultsMessage').filter({hasText:'版本已变化'}).waitFor();
  assert(await stale.locator('input[data-r2-choice="0"][value="remove"]').isChecked());
  const after=await (await context.request.get(base+'/api/product-workspace/r2-candidate/review?offer_id=123')).json();
  assert.equal(after.review.revision,1);assert.equal(after.review.keep_count,1);
  assert.equal(after.review.publication_authorized,false);assert.equal(after.review.r2_consumer.status,'BLOCKED');
  for(const width of [1440,390]){
   await page.setViewportSize({width,height:900});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   await page.screenshot({path:out+'/r2-review-'+width+'.png',fullPage:true});
  }
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({errors,posts:posts.length}));
 }catch(e){await page.screenshot({path:out+'/r2-review-failure.png',fullPage:true});throw e;}
 finally{await browser.close();}
})();
