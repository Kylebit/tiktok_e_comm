'use strict';
// Real installed Chrome, real candidate HTTP handler. Generated data only.
const {chromium}=require(process.env.ORBIT_PLAYWRIGHT || 'C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('fs'); const path=require('path');
const base=new URL(process.argv[2]); const out=process.argv[3]; const stageMode=process.argv[4];
if(base.hostname!=='127.0.0.1'||['8765','8766'].includes(base.port)||!out) throw Error('Own fixture URL and output required');
fs.mkdirSync(out,{recursive:true});
const results=[], requests=[], simulatedMutations=[], blocked=[], errors=[];
const check=(ok,name,detail)=>{results.push({name,ok:!!ok,detail}); if(!ok) throw Error(name+' '+JSON.stringify(detail));};
const text=async(p,s)=>p.locator(s).innerText();
const ledgerText=async(p)=>p.locator('#releaseRunLedger').textContent();
const pause=ms=>new Promise(r=>setTimeout(r,ms));
const queueKey='orbit.productWorkspace.releaseQueue.v1';
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
 await context.route('**/*',route=>{
  const req=route.request(), url=new URL(req.url());
  if(['data:','blob:'].includes(url.protocol)) return route.continue();
  if(url.origin!==base.origin){blocked.push(req.url());return route.abort();}
  requests.push({method:req.method(),url:req.url()}); return route.continue();
 });
 const page=await context.newPage(); page.on('pageerror',e=>errors.push(e.message));
 let acceptDialog=true;
 page.on('dialog',d=>acceptDialog?d.accept():d.dismiss());
 const open=async(id)=>{
  await page.goto(new URL('/product-workspace'+(id?'?offer_id='+id:''),base).href);
  if(id) await page.waitForFunction(()=>!document.querySelector('#refreshAllButton').disabled);
  await pause(120);
 };
 const revealQueue=async()=>{await page.locator('#queueDisclosure').evaluate(node=>{node.open=true;});};
 const select=async(id)=>{await revealQueue();await page.locator('[data-action="switch"][data-key="'+id+'"]').click();
  await page.waitForFunction(id=>document.querySelector('#productIdentity').textContent.includes('Offer '+id),id);};
 try {
  await open();
  check(!new URL(page.url()).searchParams.has('offer_id'),'empty URL stays unselected');
  check(await page.locator('#productDetail').isHidden(),'empty detail hidden');
  check(await page.locator('.queue-batch-bar').isHidden(),'zero selection hides bulk actions');
  check(!requests.some(x=>x.url.includes('/dashboard')||x.method==='POST'),'empty start does not read business or POST');
  await page.evaluate(k=>localStorage.setItem(k,JSON.stringify(['99001','99002','99003','99004','99005'].map(offer_id=>({offer_id})))),queueKey);
  await open();
  check((await page.locator('#queueGrid tbody tr').count())===5,'old queue retained without automatic selection');
  check(!requests.some(x=>x.url.includes('/dashboard')),'stored queue does not auto-hydrate');
  await page.locator('#refreshAllButton').click();
  await page.waitForFunction(()=>document.querySelector('#queueMessage').textContent.includes('刷新完成'));
  check((await text(page,'#queueMessage')).includes('1 件商品读取失败'),'batch read preserves partial failure');
  check(await page.locator('#productDetail').isHidden(),'batch read does not select a product');
  const row2=await text(page,'tr[data-key="99002"]');
  check(row2.includes('0.00 CNY')&&row2.includes('large: — CNY')&&row2.includes('图片 未知'),'zero cost and missing cost/image count distinct',row2);
  check((await text(page,'tr[data-key="99003"]')).includes('已回读'),'real store target readback shown');
  check((await text(page,'#queueGrid')).includes('待核对'),'inventory unknown with supply link');
  await select('99001');
  check(new URL(page.url()).searchParams.get('offer_id')==='99001','explicit row selection updates exact URL');
  check((await text(page,'#nextStepActionButton'))==='查看阶段与账本','approval navigation does not claim unavailable approval',await text(page,'#nextStepActionButton'));
  check(await page.locator('#generateTitleDraftButton').isDisabled(),'paid title generation explicitly unconnected');
  await page.locator('#tab-targets').click();
  check((await text(page,'#selectAllTargetsButton'))==='全选 3 个可用目标','target count uses available scope');
  check((await page.locator('input[name="publication_target"]').count())===3,'no targets invented');
  check((await text(page,'#selectedChannelPriceGrid')).includes('25.9 MYR')
    &&(await text(page,'#selectedChannelPriceGrid')).includes('尚无可用价格'),'currency and missing target prices are explicit',await text(page,'#selectedChannelPriceGrid'));
  await page.locator('#selectAllTargetsButton').click();
  check((await page.locator('input[name="publication_target"]:checked').count())===3,'select all stays in server scope');
  await page.locator('#tab-images').click();
  await page.locator('#embeddedSourceImageGrid').scrollIntoViewIfNeeded();
  await page.waitForFunction(()=>document.querySelector('#embeddedSourceImageGrid').textContent.includes('来源图加载失败'));
  check(await page.locator('#embeddedSourceImageGrid img').evaluateAll(a=>a.some(i=>i.complete&&i.naturalWidth>0)),'real source image success visible');
  check((await text(page,'#embeddedSourceImageGrid')).includes('原选择仍保留'),'image failure explicit and choice retained');
  await page.locator('input[data-embedded-image-index="0"][value="remove"]').check();
  await page.locator('#tab-facts').click(); await page.locator('#refreshButton').click();
  await page.waitForFunction(()=>!document.querySelector('#refreshAllButton').disabled);
  await page.locator('#tab-images').click();
  check(await page.locator('input[data-embedded-image-index="0"][value="remove"]').isChecked(),'image draft survives same-product read refresh');
  await revealQueue();await page.locator('[data-action="switch"][data-key="99002"]').click();
  check(new URL(page.url()).searchParams.get('offer_id')==='99001','unsaved image choice blocks switch without POST');
  await open('99001');
  await page.locator('#factsEditTitle').fill('TEST unsaved title');
  await revealQueue();await page.locator('[data-action="switch"][data-key="99002"]').click();
  check(await page.locator('#factsEditTitle').inputValue()==='TEST unsaved title','unsaved facts kept during attempted switch');
  check(new URL(page.url()).searchParams.get('offer_id')==='99001','unsaved facts keep exact identity');
  await page.locator('#nextStepActionButton').click();
  check(!requests.some(x=>x.method==='POST'),'navigation does not save a draft implicitly');
  await open('99004');
  check((await text(page,'#sellerSku')).includes('读取失败'),'503 exits SKU loading');
  check(await page.locator('.embedded-image-save-loading').evaluateAll(nodes=>nodes.every(node=>node.hidden||getComputedStyle(node).display==='none')),'503 exits image save loading');
  check(!(await page.locator('#refreshButton').isDisabled()),'503 offers retry');
  const beforeRetry=requests.filter(x=>x.url.includes('/dashboard')).length;
  await page.locator('#refreshButton').click(); await pause(150);
  check(requests.filter(x=>x.url.includes('/dashboard')).length===beforeRetry+1,'503 retry uses one read');
  await open();
  await revealQueue();await page.locator('[data-action="switch"][data-key="99005"]').click();
  await select('99002'); await pause(700);
  check((await text(page,'#productTitle')).includes('Soft linen'),'slow A cannot overwrite selected B');
  check(new URL(page.url()).searchParams.get('offer_id')==='99002','slow A cannot overwrite B URL');
  await revealQueue();await page.locator('#queueSearch').fill('99003');
  await page.locator('#selectFilteredButton').click();
  await page.locator('#queueSearch').fill('99001');
  check((await text(page,'#queueSelectionCount')).includes('筛选外 1 项'),'hidden selection scope explicit');
  check(await page.locator('.queue-batch-bar').isVisible(),'selection reveals bulk actions');
  const beforeSelected=requests.length;
  await page.locator('#refreshSelectedButton').click();
  await page.waitForFunction(()=>document.querySelector('#queueMessage').textContent.includes('共更新 1 件'));
  check(requests.slice(beforeSelected).filter(x=>x.url.includes('/dashboard')).every(x=>x.url.includes('99003')),'selected refresh reads only selected scope');
  const downloadPromise=page.waitForEvent('download');
  await page.locator('#exportSelectedButton').click(); const download=await downloadPromise;
  const exported=JSON.parse(fs.readFileSync(await download.path(),'utf8'));
  check(exported.items.length===1&&exported.items[0].offer_id==='99003'&&!JSON.stringify(exported).includes('confirmation_token'),'export carries exact identity without authority');
  await page.locator('#removeSelectedButton').click();
  check(await page.locator('.queue-batch-bar').isHidden(),'remove clears selection');
  check(await page.locator('#undoQueueRemovalButton').isVisible(),'undo remains accessible at zero selection');
  await page.locator('#undoQueueRemovalButton').click();
  await page.locator('#queueSearch').fill('');
  check((await page.locator('#queueGrid tbody tr').count())===5,'undo restores original queue');
  check(!requests.some(x=>x.method==='POST'),'all read/select/filter/export/remove actions made zero POST');
     await open('99003'); await page.locator('#tab-release').click();
   const beforeFinalPublish=requests.length;
   await page.locator('#ozonReleaseButton').dispatchEvent('click');
   await pause(100);
   check(!requests.slice(beforeFinalPublish).some(x=>x.method==='POST'),
     'old Ozon action remains closed after later dashboard navigation');
   check(blocked.length===0,'no attempted external or foreign loopback request',blocked);
   check(errors.length===0,'no browser JavaScript exceptions',errors);
  check(await page.locator('#legacyReleaseDetails').count()===0,'run ledger is not hidden behind a stale legacy disclosure');
  check(await page.locator('#legacyReleaseRunLedger').isVisible(),'real ledger visible alongside current platform controls');
  check((await page.locator('#releaseRunLedger .run-target').count())===3,'three independent target details retained');
  const ledger=await ledgerText(page);
  check(ledger.includes('回读成功')&&ledger.includes('对账')&&ledger.includes('等待执行'),'success unknown result and pending remain distinct',ledger);
   await page.locator('#releaseRunLedger details').first().locator('summary').click();
   check((await ledgerText(page)).includes('TEST-tiktok-99003'),'official readback evidence accessible');
   check(await page.locator('#releasePrimaryActionButton').isDisabled(),'completed TikTok cannot be submitted again');
   check(await page.locator('#shopeeGlobalReleaseButton').isDisabled(),'unknown Shopee outcome cannot be submitted again');
   await page.waitForFunction(()=>!document.querySelector('#nextStepActionButton').disabled);
   check(await page.locator('#ozonReleaseButton').isDisabled(),'R3 stage response disables legacy Ozon consumer');
   check((await page.locator('#publicationFlowActions button').count())===0,'unavailable or malformed stage cannot supply R3 actions');
   check(!(await page.locator('#publicationFlowReview').textContent()).includes('fixture-untrusted'),
     'untrusted response cannot surface a forged market plan');
   check(!await page.locator('#collectboxActionButton').isDisabled(),
     'synthetic stale collectbox projection retains start_allowed before forced click');
   check((await page.locator('#publicationFlowStatus').textContent()).length>10,'read-only R3 reconciliation reason visible');
   const dispatchLegacy=async(container,attributes)=>page.evaluate(({container,attributes})=>{
     const button=document.createElement('button');button.type='button';
     for(const [name,value] of Object.entries(attributes))button.setAttribute(name,value);
     document.querySelector(container).append(button);
     const event=new MouseEvent('click',{bubbles:true,cancelable:true});
     button.dispatchEvent(event);
     return event.defaultPrevented;
   },{container,attributes});
   const beforeReadOnly=requests.length;
   check(!await dispatchLegacy('#releaseRunLedger',{'data-target-scoped-action':'preview','data-target-label':'shopee:MY'}),
     'R3 retains target-scoped GET preview control');
   await pause(120);
   check(requests.slice(beforeReadOnly).some(x=>x.method==='GET'&&x.url.includes('target-scoped-action-preview')),
     'target-scoped preview still sends a GET');
   check(!await dispatchLegacy('#releaseRunLedger',{'data-price-repair-action':'preview','data-target-label':'shopee:PH'}),
     'R3 retains Shopee price preview control');
   const beforeGlobalPreview=requests.length;
   check(!await dispatchLegacy('#oneClickExecutionGroups',{'class':'shopee-global-plan-preview-retry'}),
     'R3 retains Shopee Global GET retry control');
   await pause(120);
   check(requests.slice(beforeGlobalPreview).some(x=>x.method==='GET'&&x.url.includes('shopee-global-plan-preview')),
     'Shopee Global retry still sends a GET');
   const beforeRecoveryPreview=requests.length;
   check(!await dispatchLegacy('#releasePlanRecovery',{'class':'shopee-global-plan-preview-retry'}),
     'R3 retains recovery-panel Shopee Global GET retry');
   await pause(120);
   check(requests.slice(beforeRecoveryPreview).some(x=>x.method==='GET'&&x.url.includes('shopee-global-plan-preview')),
     'recovery-panel retry still sends a GET');
   check(!await dispatchLegacy('#oneClickExecutionGroups',{'class':'shopee-global-auth-restore'}),
     'R3 retains local authorization guidance');
   check(!await dispatchLegacy('#oneClickExecutionGroups',{'data-oneclick-target':'shopee:MY'}),
     'R3 retains local target focus');
   check(await dispatchLegacy('#releaseRunLedger',{'data-price-repair-action':'reconcile','data-target-label':'shopee:PH'}),
     'price repair reconciliation remains blocked because it can POST');
   check(await dispatchLegacy('#oneClickExecutionGroups',{'data-unknown-write':'yes'}),
     'unknown legacy button remains fail-closed');
   const oldControls=['#releasePrimaryActionButton','#shopeeGlobalReleaseButton','#ozonReleaseButton',
     '#publishAllButton','#collectboxActionButton','#commonOverwriteButton','#prepareMiaoshouButton',
     '#approveReleasePlanButton'];
   const beforeForced=requests.length;
   for(const selector of oldControls) await page.locator(selector).dispatchEvent('click');
   await page.locator('#releaseRunLedger button,#oneClickExecutionGroups button,#releasePlanRecovery button').evaluateAll(nodes=>{
     for(const node of nodes) node.dispatchEvent(new MouseEvent('click',{bubbles:true}));
   });
   await page.evaluate(()=>{
     const ledger=document.querySelector('#releaseRunLedger');
     const form=document.createElement('form');form.className='manual-verification-form';
     form.innerHTML='<input name="marketplace_product_id" value="TEST"><input type="checkbox" name="all_checks_confirmed" checked><button type="submit">submit</button>';
     ledger.append(form);form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
   });
   const flowButtons=page.locator('#publicationFlowActions button');
   if(await flowButtons.count()) await flowButtons.first().dispatchEvent('click');
   await pause(120);
   check(!requests.slice(beforeForced).some(x=>x.method==='POST'),'forced legacy, approval, COMMON and R3 actions make zero POST',requests.slice(beforeForced));
   check(!requests.some(x=>x.method==='POST'),'whole read-only browser journey makes zero POST');
   await page.screenshot({path:path.join(out,'r3-stage-'+stageMode+'.png'),fullPage:true});
  for(const width of [1440,1280,390]) {
   await page.setViewportSize({width,height:width===390?844:1000}); await page.evaluate(()=>scrollTo(0,0));
   check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'page has no horizontal overflow '+width);
   await page.screenshot({path:path.join(out,'release-'+width+'.png'),fullPage:true});
  }
  await page.locator('#orbitMenu').click();
  check(await page.locator('#orbitCoreNav').isVisible(),'mobile menu computed visible');
  await page.locator('#orbitCoreNav a').filter({hasText:'供应'}).click();
  check(new URL(page.url()).pathname==='/supply-chain/','mobile menu reaches supply');
  await page.goto(new URL('/?view=product&offer_id=99002',base).href);
  await page.waitForURL('**/product-workspace?offer_id=99002');
  check(true,'legacy product route forwards exact identity');
  await page.setViewportSize({width:1440,height:1000});
  await page.goto(base.href); await pause(150);
  check(await page.locator('#orbitCoreNav a[href="/product-workspace"]').count()===1,'main navigation directly opens queue');
  await page.locator('#refreshTasks').click();
  await page.waitForFunction(()=>document.querySelector('#pageError').textContent.includes('读取失败'));
  check(true,'home task list reports genuine read failure');
  await page.screenshot({path:path.join(out,'home-1440.png'),fullPage:true});
  // Server-generated dashboard remains the source; these explicit browser-only
  // mutations exercise display rejection without touching the database.
   const source1=await (await context.request.get(new URL('/api/product-workspace/dashboard?offer_id=99001',base).href)).json();
   const source3=await (await context.request.get(new URL('/api/product-workspace/dashboard?offer_id=99003',base).href)).json();
   const imageSource=await (await context.request.get(new URL('/api/product-flow/preview?offer_id=99001',base).href)).json();
   let imageOverride=structuredClone(imageSource);
   imageOverride.offer_id='99999';
   await page.route('**/api/product-flow/preview?*',route=>imageOverride
     ?route.fulfill({status:200,json:imageOverride}):route.fallback());
   await open('99001'); await page.locator('#tab-images').click();
   check((await text(page,'#embeddedImageReviewMessage')).includes('图片响应身份'),'wrong product image preview rejected before adopting saved decisions');
   check(await page.locator('#saveEmbeddedImageReviewButton').isDisabled()
     &&await page.locator('input[data-embedded-image-index]').count()===0,'wrong image identity cannot be edited or saved');
   delete imageOverride.offer_id;
   await open('99001'); await page.locator('#tab-images').click();
   check((await text(page,'#embeddedImageReviewMessage')).includes('图片响应身份'),'missing image identity is not assumed current');
   imageOverride=null;
   let override=null, forceMissing=false;
   await page.route('**/api/product-workspace/dashboard?*',route=>{
    if(forceMissing) return route.fulfill({status:404,json:{ok:false,error:'TEST missing local evidence'}});
    if(override) return route.fulfill({status:200,json:override});
     return route.fallback();
   });
   override=structuredClone(source3);
   override.release_v1.run.plan_id='TEST-historical-plan';
   await open('99003'); await page.locator('#tab-release').click();
   check(await page.locator('#releasePrimaryActionButton').isDisabled()
     &&await page.locator('#shopeeGlobalReleaseButton').isDisabled(),'R3 stage still blocks old publish despite other-plan history');
   check((await ledgerText(page)).includes('属于其他发布计划'),'other plan history labeled explicitly');
   override=structuredClone(source3);
   override.release_v1.plan.targets.push('tiktok:TEST_PENDING');
   override.release_v1.run.targets.push({target_label:'tiktok:TEST_PENDING',status:'PENDING',run_id:override.release_v1.run.run_id});
   await open('99003'); await page.locator('#tab-release').click();
   check(await page.locator('#releasePrimaryActionButton').isDisabled()
     &&(await page.locator('#releasePrimaryActionMessage').textContent()).includes('整个平台不能重发'),'mixed same-platform scope is blocked without silently trimming targets');
   override=structuredClone(source3);
   delete override.release_v1.run.plan_id;
   await open('99003'); await page.locator('#tab-release').click();
   check(await page.locator('#ozonReleaseButton').isDisabled(),'unattributed run cannot be assumed safe');
   override=structuredClone(source3);
  override.release_v1.plan=null; override.release_v1.plan_approved=false; override.release_v1.historical=true;
  delete override.release_v1.run.targets[0].attempts;
  await open('99003'); await page.locator('#tab-release').click();
  check((await ledgerText(page)).includes('历史执行记录，当前计划未就绪'),'historical run survives missing current plan');
  check((await ledgerText(page)).includes('尝试次数未记录'),'missing attempts never becomes zero');
  check(await page.locator('#releasePrimaryActionPanel').isHidden(),'historical success does not grant current publish authority');
  override=structuredClone(source1);
  override.product.offer_id='99999';
  await open('99001');
  check((await text(page,'#queueMessage')).includes('身份'),'response with wrong offer rejected');
  override=structuredClone(source1);
  override.workflow_next_action={schema_version:'product-workflow-next-action/v1',code:'TEST_REFRESH',
    kind:'refresh',actionable:true,terminal:false,label:'重新检查当前商品',detail:'TEST read only',control_id:''};
  await open('99001'); forceMissing=true;
  await page.locator('#refreshButton').click();
  await page.waitForFunction(()=>document.querySelector('#queueMessage').textContent.includes('读取失败'));
  check(!requests.some(x=>x.method==='POST'),'explicit refresh 404 does not implicitly collect');
  forceMissing=false; override=null;
  await open('99001');
  await revealQueue();
  await page.locator('#refreshAllButton').click();
  await page.waitForFunction(()=>document.querySelector('#queueMessage').textContent.includes('刷新完成'));
  for(const width of [1440,1280,390]) {
    await page.setViewportSize({width,height:width===390?844:1000});
    for(const tab of ['facts','images','targets','release']) {
      await page.locator('#tab-'+tab).click(); await page.evaluate(()=>scrollTo(0,0));
      check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'four-tab overflow '+width+' '+tab);
      if(tab==='release') {
        const style=await page.locator('#releasePlanSummary strong').first().evaluate(e=>({color:getComputedStyle(e).color,h:e.parentElement.getBoundingClientRect().height}));
        check(style.color!=='rgb(255, 255, 255)'&&style.h<84,'release summary contrast and density '+width,style);
      }
      await page.screenshot({path:path.join(out,tab+'-'+width+'.png'),fullPage:true});
      if(tab==='facts') await page.screenshot({path:path.join(out,'first-screen-'+width+'.png')});
    }
  }
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('#tab-facts').click();
  await page.locator('#tab-facts').focus(); await page.keyboard.press('ArrowRight');
  check(await page.locator('#tab-images').getAttribute('aria-selected')==='true','keyboard tab navigation');
  await page.locator('#tab-facts').click();
  await page.locator('#factsEditTitle').fill('TEST conflict draft');
  await page.locator('input[data-sku-key="large"][data-commercial-field="cost_cny"]').fill('12');
  let saveBody=null;
   await page.route('**/api/product-workspace/facts',route=>{
     saveBody=route.request().postDataJSON();
     simulatedMutations.push({url:route.request().url(),body:saveBody,response_status:409});
    return route.fulfill({status:409,json:{ok:false,error:'TEST revision conflict'}});
  });
  await page.locator('#factsEditSaveButton').click();
  await page.waitForFunction(()=>document.querySelector('#factsEditMessage').textContent.includes('保存被拒绝'));
  check(saveBody.offer_id==='99001'&&saveBody.expected_revision===7,'explicit save binds current offer/revision');
  check(await page.locator('#factsEditTitle').inputValue()==='TEST conflict draft','revision conflict preserves unsaved facts');
  check(!await page.locator('#factsEditSaveButton').isDisabled(),'revision conflict exits loading');
  await page.locator('input[data-sku-key="small"][data-commercial-field="cost_cny"]').fill('-2');
  acceptDialog=false; await page.locator('#discardProductDraftButton').click();
  check(await page.locator('#factsEditTitle').inputValue()==='TEST conflict draft','cancel discard retains invalid facts draft');
  acceptDialog=true; await page.locator('#discardProductDraftButton').click();
  check(await page.locator('#factsEditTitle').inputValue()===source1.product.title,'confirm discard restores loaded facts');
  check(await page.locator('input[data-sku-key="small"][data-commercial-field="cost_cny"]').inputValue()==='8.5','discard restores loaded numeric fact');
  check(await page.locator('#discardProductDraftButton').isHidden(),'discard clears facts dirty state');
  await select('99002');
  check(new URL(page.url()).searchParams.get('offer_id')==='99002','discard allows switching');
  await select('99001'); await page.locator('#tab-images').click();
  await page.locator('input[data-embedded-image-index="0"][value="remove"]').check();
  acceptDialog=false; await page.locator('#discardProductDraftButton').click();
  check(await page.locator('input[data-embedded-image-index="0"][value="remove"]').isChecked(),'cancel discard retains image decisions');
  acceptDialog=true; await page.locator('#discardProductDraftButton').click();
  check(await page.locator('input[data-embedded-image-index="0"][value="keep"]').isChecked(),'confirm discard restores loaded image decisions');
  await revealQueue();await page.locator('[data-action="remove"][data-key="99001"]').click();
  check(await page.locator('#productDetail').isHidden(),'discard permits removing current product');
  check(!requests.some(x=>x.method==='POST'),'discard never sends an HTTP mutation');
  await open();
  let collectBody=null;
   await page.route('**/api/product-workspace/collect',route=>{
     collectBody=route.request().postDataJSON();
     simulatedMutations.push({url:route.request().url(),body:collectBody,response_status:200});
    return route.fulfill({status:200,json:source1});
  });
  await page.locator('#offerId').fill('99006');
  await page.locator('#collectProductButton').click();
  await page.waitForFunction(()=>document.querySelector('#queueMessage').textContent.includes('读取失败'));
  check(collectBody.offer_id==='99006','explicit collect keeps exact requested identity');
   check((await text(page,'#queueMessage')).includes('采集返回的商品身份'),'explicit collect rejects conflicting response identity');
    await open('99003'); await page.locator('#tab-release').click();
 } catch(error) {results.push({name:'uncaught',ok:false,detail:error.stack}); await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});}
 finally {
   fs.writeFileSync(path.join(out,'browser-result.json'),JSON.stringify({renderer:'installed Chrome headless',base:base.href,results,requests,simulatedMutations,blocked,errors},null,2));
  await browser.close();
  console.log(JSON.stringify({checks:results.length,failed:results.filter(x=>!x.ok)}));
  if(results.some(x=>!x.ok)) process.exitCode=1;
 }
})();
