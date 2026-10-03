const {chromium}=require('playwright');
const assert=require('assert'),fs=require('fs'),{execFileSync}=require('child_process');
const [base,carrier,expectedFile,out,python,controller]=process.argv.slice(2);
const expected=JSON.parse(fs.readFileSync(expectedFile,'utf8'));
const normalize=s=>String(s).replace(/\s+/g,' ').trim();
(async()=>{
 assert(process.env.ORBIT_BROWSER_EXECUTABLE,'Explicit Chromium required');
 const browser=await chromium.launch({executablePath:process.env.ORBIT_BROWSER_EXECUTABLE,headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1080}});
 const page=await context.newPage(),posts=[],errors=[];
 const imageUrls=expected.manifest.image_sets.flatMap(set=>set.images.map(i=>i.url));
 const media=new Set([...imageUrls,...expected.source_media_urls]);
 const image='<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400"><rect width="400" height="400" fill="#d9bca0"/></svg>';
 context.on('page',p=>p.on('pageerror',e=>errors.push(e.message)));
 page.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',async route=>{
  const request=route.request(),url=request.url();
  if(!url.startsWith(base)){
   assert(media.has(url),'Unregistered external request forbidden');
   return route.fulfill({status:200,contentType:'image/svg+xml',body:image});
  }
  if(request.method()==='POST'){
   assert(url.includes('/local-operator/'),'No legacy or provider POST');
   posts.push({path:new URL(url).pathname,body:request.postDataJSON()});
  }
  return route.continue();
 });
 const native=mode=>execFileSync(python,[controller,expected.private_root,carrier,String(expected.port),
  expected.reservation_id,expected.plan_id,expected.clock_file,mode],{encoding:'utf8',timeout:15000});
 async function inspect(p){
  await p.locator('#originalPublicationReview').waitFor();
  await p.locator('[data-original-round="final"]').click();
  await p.locator('#releaseCandidateCopy .release-review-copy-card').first().waitFor();
  const copy=normalize(await p.locator('#releaseCandidateCopy').innerText());
  for(const group of expected.manifest.copy_sets){assert(copy.includes(normalize(group.title)));assert(copy.includes(normalize(group.description)));}
  const variants=await p.locator('#releaseCandidateVariants').innerText();
  for(const row of expected.manifest.variants)assert(variants.includes(row.model_sku));
  const rows=await p.locator('#releaseCandidateTargets tbody tr').allInnerTexts();
  assert.equal(rows.length,expected.manifest.targets.length);
  expected.manifest.targets.forEach((target,index)=>{
   assert(rows[index].includes(target.target_label));
   for(const price of target.prices){assert(rows[index].includes(price.model_sku));assert(rows[index].includes(String(price.amount)+' '+price.currency));}
  });
  assert.deepEqual(await p.locator('#releaseCandidateImages img').evaluateAll(nodes=>nodes.map(i=>i.src)),imageUrls);
  await p.waitForFunction(n=>document.querySelectorAll('#releaseCandidateImages img').length===n
   &&[...document.querySelectorAll('#releaseCandidateImages img')].every(i=>i.naturalWidth>0),imageUrls.length);
 }
 try{
  await page.goto(base+'/local-operator-init#'+fs.readFileSync(carrier,'utf8'));
  await page.waitForURL(/\/product-workspace\?offer_id=.+&plan_id=.+/);
  await inspect(page);
  const original=page.url();
  native('expire');
  await page.locator('#publicationFlowActions button').filter({hasText:'批准当前最终候选'}).click();
  const notice=page.locator('[data-local-session-recovery="required"]');
  await notice.waitFor();
  assert((await notice.innerText()).includes('本机会话已到期'));
  assert((await notice.innerText()).includes('不增加一次审核'));
  await notice.locator('summary').click();
  const command=await notice.locator('pre').innerText();
  assert(command.includes('private_local_browser_bootstrap.py'));
  assert(command.includes(expected.reservation_id)&&command.includes(expected.plan_id));
  assert(!command.includes(fs.readFileSync(carrier,'utf8')),'Carrier absent from recovery guidance');
  assert.equal(posts.filter(p=>p.path.endsWith('/decision')).length,0);
  await page.screenshot({path:out+'/session-expired-original-review.png',fullPage:true});
  const before=posts.length;
  await notice.locator('button').click();
  await page.waitForFunction(()=>document.querySelector('[data-local-session-recovery] button')?.disabled===false);
  assert.equal(posts.length,before,'Readonly refresh issues no prepare/decision/bootstrap POST');
  native('reconnect');
  const init=await context.newPage();
  await init.goto(base+'/local-operator-init#'+fs.readFileSync(carrier,'utf8'));
  await init.waitForURL(/\/product-workspace\?offer_id=.+&plan_id=.+/);
  assert.equal(init.url(),original,'Native launcher restores original Offer/Plan navigation');
  await inspect(init);
  const afterNative=posts.length;
  await notice.locator('button').click();
  await notice.waitFor({state:'detached'});
  assert.equal(posts.length,afterNative,'Checking fresh same-owner identity remains GET-only');
  await page.locator('#publicationFlowActions button').filter({hasText:'批准当前最终候选'}).click();
  await page.waitForFunction(()=>document.querySelector('#publicationFlowActions').textContent.includes('批准已保存'));
  assert.equal(posts.filter(p=>p.path.endsWith('/decision')).length,1);
  native('expire');
  const afterApproval=posts.length;
  await page.reload();await inspect(page);
  await page.waitForFunction(()=>document.querySelector('#publicationFlowActions').textContent.includes('批准已保存'));
  assert.equal(await page.locator('#publicationFlowActions button').count(),0,'Expired identity does not delete approval or ask again');
  assert.equal(posts.length,afterApproval,'Reading saved decision has no POST');
  assert.deepEqual(errors,[]);
  await page.screenshot({path:out+'/session-restored-single-decision.png',fullPage:true});
  const facts={full_original_dom:true,same_owner_reconnected:true,readonly_refresh_posts:0,decision_requests:1,execution_authority:false};
  fs.writeFileSync(out+'/session-recovery-browser-facts.json',JSON.stringify(facts,null,2));
  console.log(JSON.stringify(facts));
 }catch(error){fs.writeFileSync(out+'/session-recovery-body.txt',await page.locator('body').innerText());await page.screenshot({path:out+'/session-recovery-failure.png',fullPage:true});throw error;}
 finally{await browser.close();}
})();
