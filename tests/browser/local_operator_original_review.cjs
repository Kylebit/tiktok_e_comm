const {chromium}=require('playwright');
const assert=require('assert'),fs=require('fs');
const base=process.argv[2],carrierFile=process.argv[3],expectedFile=process.argv[4],out=process.argv[5];
const expected=JSON.parse(fs.readFileSync(expectedFile,'utf8'));
const dropDecisionResponse=process.argv[6]==='drop-response';
const normalize=s=>String(s).replace(/\s+/g,' ').trim();
(async()=>{
 const executablePath=process.env.ORBIT_BROWSER_EXECUTABLE;
 assert(executablePath,'Explicit browser executable required');
 const browser=await chromium.launch({executablePath,headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1080}});
 const page=await context.newPage(),posts=[],errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const imageUrls=new Set(expected.manifest.image_sets.flatMap(set=>set.images.map(i=>i.url)));
 const allowedMedia=new Set([...imageUrls,...expected.source_media_urls]);
 const fixtureImage='<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400"><rect width="400" height="400" fill="#d9bca0"/><text x="35" y="210" font-size="24">SYNTHETIC IMAGE</text></svg>';
 let held=null,holdDecision=true;
 context.on('page',p=>p.on('pageerror',e=>errors.push(e.message)));
 await context.route('**/*',async route=>{
  const request=route.request(),url=request.url();
  if(!url.startsWith(base)){
   assert(allowedMedia.has(url),'No unregistered external request');
   return route.fulfill({status:200,contentType:'image/svg+xml',body:fixtureImage});
  }
  if(request.method()==='POST'){
   assert(url.includes('/local-operator/'),'No legacy approval or provider endpoint');
   const body=request.postDataJSON();
   assert(!('approved_by' in body)&&!('user_approved' in body)&&!('targets' in body));
   posts.push({path:new URL(url).pathname,body});
   if(url.endsWith('/decision')&&holdDecision){held=route;holdDecision=false;return;}
  }
  return route.continue();
 });
 async function inspect(p){
  await p.locator('#originalPublicationReview').waitFor();
  await p.locator('[data-original-round="final"]').click();
  await p.locator('#releaseCandidateCopy .release-review-copy-card').first().waitFor();
  const copy=normalize(await p.locator('#releaseCandidateCopy').innerText());
  for(const group of expected.manifest.copy_sets){
   assert(copy.includes(normalize(group.title)),'Complete source title shown');
   assert(copy.includes(normalize(group.description)),'Complete source description shown');
  }
  const variants=await p.locator('#releaseCandidateVariants').innerText();
  for(const variant of expected.manifest.variants)assert(variants.includes(variant.model_sku),'Every actual SKU shown');
  const targetRows=await p.locator('#releaseCandidateTargets tbody tr').allInnerTexts();
  assert.equal(targetRows.length,expected.manifest.targets.length);
  expected.manifest.targets.forEach((target,index)=>{
   assert(targetRows[index].includes(target.target_label),'Complete ordered target scope');
   for(const price of target.prices){
    assert(targetRows[index].includes(price.model_sku));
    assert(targetRows[index].includes(String(price.amount)+' '+price.currency),'Actual frozen price shown');
   }
  });
  const imageCount=expected.manifest.image_sets.reduce((n,set)=>n+set.images.length,0);
  assert.equal(await p.locator('#releaseCandidateImages img').count(),imageCount);
  const imageGroups=p.locator('#releaseCandidateImages .release-review-image-set');
  assert.equal(await imageGroups.count(),expected.manifest.image_sets.length);
  for(let index=0;index<expected.manifest.image_sets.length;index++){
   const group=imageGroups.nth(index),set=expected.manifest.image_sets[index];
   const header=await group.locator('header').innerText();
   for(const target of set.target_labels)assert(header.includes(target),'Exact brand target group');
   const images=await group.locator('img').evaluateAll(nodes=>nodes.map(i=>({src:i.src,alt:i.alt})));
   assert.deepEqual(images,set.images.map(i=>({src:i.url,alt:String(i.role||i.position)})),'Complete frozen image order/role');
  }
  await p.waitForFunction(n=>document.querySelectorAll('#releaseCandidateImages img').length===n
    &&[...document.querySelectorAll('#releaseCandidateImages img')].every(i=>i.naturalWidth>0),imageCount);
  await p.screenshot({path:out+'/complete-original-review.png',fullPage:true});
  return p.locator('#publicationFlowActions button').filter({hasText:'批准当前最终候选'});
 }
 try{
  const nonce=fs.readFileSync(carrierFile,'utf8');
  await page.goto(base+'/local-operator-init#'+nonce);
  await page.waitForURL(/\/product-workspace\?offer_id=.+&plan_id=.+/);
  const button=await inspect(page);
  const stages=await page.evaluate(async()=>await(await fetch('/api/product-workspace/publication-stages?offer_id='+new URL(location.href).searchParams.get('offer_id'))).json());
  fs.writeFileSync(out+'/actual-original-stage.json',JSON.stringify(stages,null,2));
  // The real compiler enum remains READY_FOR_FINAL_REVIEW; no fixture rewrite.
  assert.equal(expected.compiler_status,'READY_FOR_FINAL_REVIEW');
  await button.waitFor({timeout:5000});
  const stale=await context.newPage();await stale.goto(page.url());
  const staleButton=await inspect(stale);await staleButton.waitFor({timeout:5000});
  const decisionResponse=dropDecisionResponse?null:page.waitForResponse(response=>response.url().endsWith('/local-operator/decision'));
  await button.click();
  for(let i=0;i<200&&!held;i++)await new Promise(r=>setTimeout(r,10));
  assert(held,'Actual one decision request reached backend');
  assert(await page.locator('#publicationFlowActions button').first().isDisabled());
  await page.locator('#publicationFlowActions button').first().evaluate(b=>{b.click();b.click();});
  assert.equal(posts.filter(p=>p.path.endsWith('/decision')).length,1);
  if(dropDecisionResponse){
   const persistedResponse=await held.fetch();
   assert.equal(persistedResponse.status(),200,'Backend completed the durable decision before response loss');
   await held.abort('failed');held=null;
   await page.waitForFunction(()=>document.querySelector('#publicationFlowStatus').textContent.includes('当前审核版本与草稿保留'));
  }else{
   await held.continue();held=null;
   assert.equal((await decisionResponse).status(),200);
  }
  await page.reload();await inspect(page);
  await page.waitForFunction(()=>document.querySelector('#publicationFlowActions').textContent.includes('批准已保存'));
  assert.equal(await page.locator('#publicationFlowActions button').count(),0);
  // A stale tab's repeat click must reuse the durable decision and fake write.
  await staleButton.click();
  await stale.waitForFunction(()=>document.querySelector('#publicationFlowActions').textContent.includes('批准已保存'));
  assert.equal(posts.filter(p=>p.path.endsWith('/decision')).length,2);
  assert.equal(await stale.locator('#publicationFlowActions button').count(),0);
  await stale.screenshot({path:out+'/same-candidate-replayed.png',fullPage:true});
  assert.deepEqual(errors,[]);
  const facts={full_original_dom:true,all_skus:expected.manifest.variants.length,
    all_ordered_targets:expected.manifest.targets.map(t=>t.target_label),
    actual_compiler_status:expected.compiler_status,busy_duplicate_suppressed:true,
    same_candidate_replayed:true,legacy_or_provider_posts:0,decision_requests:2,
    response_lost_after_persistence:dropDecisionResponse,
    execution_authority:false};
  fs.writeFileSync(out+'/original-browser-facts.json',JSON.stringify(facts,null,2));
  console.log(JSON.stringify(facts));
 }catch(error){if(held)await held.abort();fs.writeFileSync(out+'/actual-original-body.txt',await page.locator('body').innerText());await page.screenshot({path:out+'/original-private-failure.png',fullPage:true});throw error;}
 finally{await browser.close();}
})();
