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
  await p.locator('#firstReviewWorkspace').waitFor();
  assert.equal(await p.locator('.first-review-copy-bilingual').count(),1);
  assert((await p.locator('#firstReviewCandidateGrid').innerText()).includes('冻结中文'));
  assert((await p.locator('#firstReviewCandidateGrid').innerText()).includes('家居 > 墙贴'));
  await p.locator('.first-review-price-formula summary').last().click();
  assert((await p.locator('.first-review-price-formula').last().innerText()).includes('成本除以利润系数'));
  await p.locator('.first-review-raw-audit > summary').click();
  assert((await p.locator('#firstReviewAudit').innerText()).includes('Synthetic original source'));
  await p.locator('[data-original-round="images"]').click();await p.locator('#localizedImageResults').scrollIntoViewIfNeeded();
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
  await page.locator('[data-original-round="final"]').click();
  if(process.argv[4]==='True') {
   assert((await page.locator('#releaseCandidateCopy').innerText()).includes('FINAL FROZEN TITLE'));
   assert((await page.locator('#releaseCandidateVariants').innerText()).includes('Tile 1pcs'));
   assert((await page.locator('#releaseCandidateTargets').innerText()).includes('20 MYR'));
   await page.waitForFunction(()=>document.querySelector('#releaseCandidateImages img')?.naturalWidth>0);
   assert((await page.locator('#releaseCandidateImages').innerText()).includes('shopee:MY'));
   await page.locator('#releaseCandidatePanel details').filter({has:page.locator('#releaseCandidateWriteBudget')}).locator(':scope > summary').click();
   assert((await page.locator('#releaseCandidateWriteBudget').innerText()).includes('SHOPEE'));
  } else if(process.argv[4]==='successor') {
   assert((await page.locator('#releaseCandidateBadge').innerText()).includes('待审核'));
   assert((await page.locator('#releaseCandidateChecks').innerText()).includes('15 个目标'));
   assert((await page.locator('#releaseCandidateChecks').innerText()).includes('NOT_APPROVED · 禁止执行'));
   assert((await page.locator('#releaseCandidateCopy').innerText()).includes('Huraian tempatan baharu'));
   assert((await page.locator('#releaseCandidateCopy').innerText()).includes('本次描述变更'));
   assert.equal(await page.locator('#publicationFlowActions button').count(),0);
  } else if(process.argv[4]==='blocked-final') {
   assert.equal(await page.locator('#publicationFlowActions button').count(),0);
   assert((await page.locator('#publicationFlowStatus').innerText()).includes('COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN'));
  } else if(['blocked-common','blocked-common-approved'].includes(process.argv[4])) {
   assert.equal(await page.locator('#publicationFlowActions button').count(),0);
   const stage=(await (await context.request.get(base+'/api/product-workspace/publication-stages?offer_id=123')).json());
   assert.equal(stage.common.status,process.argv[4]==='blocked-common-approved'?'READY_TO_SYNC':'APPROVAL_REQUIRED');
   assert.equal(stage.common.plan.status,process.argv[4]==='blocked-common-approved'?'APPROVED':'PENDING_APPROVAL');
   assert.equal(stage.marketplace.status,'NOT_FROZEN');
   assert.equal(stage.common.technical_admission,undefined);
   const conditions=await page.locator('#publicationFlowStatus').innerText();
   for(const missing of ['本机持续意愿尚未核实','实际历史写入数待核实','本地准备轮血缘尚未核实',
                         '账户执行权限待核实','写入覆盖范围待核实','运行安装状态待核实'])assert(conditions.includes(missing));
   assert(conditions.includes('读取这些事实不代表可以写入或发布，也不增加人工批准。'));
   assert.equal(await page.locator('#releasePlanApprovalForm').isVisible(),false);
   assert.equal(await page.locator('#legacyReleaseActionPanels').isVisible(),false);
   assert.equal(await page.locator('#approveReleasePlanButton').isDisabled(),true);
   assert.equal(await page.locator('#prepareMiaoshouButton').isDisabled(),true);
  } else assert((await page.locator('#releaseCandidateBadge').innerText()).includes('尚未形成'));
  assert.equal(await page.locator('#releaseCandidateContent').isVisible(),true);
  assert.equal(await page.locator('#originalFinalActions button').count(),0);
  for(const width of [1440,390]){
   await page.setViewportSize({width,height:900});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   for(const round of ['first','images','final']) {
    await page.locator('[data-original-round="'+round+'"]').click();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.screenshot({path:out+'/original-'+round+'-'+width+'.png',fullPage:true});
   }
  }
  await page.locator('.queue-jump').click();
  await page.locator('#offerId').fill('99003');
  await page.locator('#lookupForm button[type="submit"]').click();
  await page.locator('#pageAlert').filter({hasText:'资料未连接'}).waitFor();
  assert.equal(await page.locator('#originalPublicationReview').isVisible(),false);
  assert(!(await page.locator('body').innerText()).includes('Frozen English'));
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({errors,posts:posts.length}));
 }catch(e){await page.screenshot({path:out+'/r2-review-failure.png',fullPage:true});throw e;}
 finally{await browser.close();}
})();
