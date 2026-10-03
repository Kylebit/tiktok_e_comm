const {chromium} = require('playwright');
const assert = require('assert'), fs = require('fs');
const {createResponseBodyCapture} = require('./response_body_capture.cjs');
const [base,out,offer,mode,expectedPath] = process.argv.slice(2);
const fixture=JSON.parse(fs.readFileSync(expectedPath,'utf8')), expected=fixture.getter;
const allowedMedia=new Set(fixture.test_only_media_urls);
const fixtureImage='<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400"><rect width="400" height="400" fill="#d9bca0"/></svg>';
(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:900}});
  const page=await context.newPage(),errors=[],posts=[],captured=[],screenshots=[],requested=[];
  const owned=request=>new URL(request.url()).pathname==='/api/product-workspace/publication-stages';
  const bodies=createResponseBodyCapture((response,raw)=>{
    assert.strictEqual(response.status(),200);
    captured.push(JSON.parse(raw.toString('utf8')));
  });
  page.setDefaultTimeout(30000);
  page.on('pageerror',error=>errors.push(error.message));
  await context.route('**/*',async route=>{
    const request=route.request();
    if(request.method()!=='GET'){
      posts.push({method:request.method(),url:request.url()});
      await route.abort();return;
    }
    if(!request.url().startsWith(base+'/')){
      assert(request.resourceType()==='image' && allowedMedia.has(request.url()),'No undeclared foreign request');
      return route.fulfill({status:200,contentType:'image/svg+xml',body:fixtureImage});
    }
    return route.continue();
  });
  page.on('request',request=>{
    if(owned(request)){
      const url=new URL(request.url());
      assert.strictEqual(url.searchParams.get('offer_id'),offer);
      assert.strictEqual(url.searchParams.has('plan_id'),false,'Real UI must use offer-only selection');
      requested.push(request.url());bodies.requestStarted(request);
    }
  });
  page.on('requestfinished',request=>{if(owned(request))bodies.requestFinished(request);});
  page.on('requestfailed',request=>{if(owned(request))bodies.requestFailed(request);});
  page.on('response',response=>{if(owned(response.request()))bodies.capture(response);});
  try{
    await page.goto(base+'/product-workspace?offer_id='+offer+'&round=final');
    assert.strictEqual(await page.locator('#originalPublicationReview').isVisible(),false);
    await page.locator('#tab-release').click();
    await page.locator('#pane-release').waitFor({state:'visible'});
    await page.waitForFunction(summary=>document.querySelector('#nextStepTitle')?.textContent===summary,expected.common.user_status.summary);
    await bodies.drain();
    assert(captured.length>0);
    for(const row of captured){
      assert.strictEqual(row.common.status,expected.common.status);
      assert.deepStrictEqual(row.common.user_status,expected.common.user_status);
      assert.deepStrictEqual(row.common.retained_baseline_facts,expected.common.retained_baseline_facts);
      assert.deepStrictEqual(row.marketplace.plan,expected.marketplace.plan);
      assert.strictEqual(row.marketplace.execution_authority,false);
      assert.strictEqual(row.marketplace.final_review_available,false);
      assert(!['nonce','approval_saved','review_digest'].some(key=>Object.hasOwn(row.marketplace,key)));
      assert.deepStrictEqual(row.external_writes_performed,[]);
    }
    const detail=page.locator('#publicationFlowReview details').filter({has:page.locator('summary',{hasText:'当前检查原因与阶段身份 · 完整详情'})});
    assert.strictEqual(await detail.count(),1);
    assert.strictEqual(await detail.evaluate(element=>element.open),false);
    await detail.locator('summary').click();
    const evidence=JSON.parse(await detail.locator('pre').textContent());
    assert.deepStrictEqual(evidence.common_technical_details,expected.common.technical_details);
    assert.strictEqual(evidence.final_review_available,false);
    if(mode==='retained'){
      const facts=evidence.common_retained_baseline;
      assert.strictEqual(facts.current_signed_readback,'UNKNOWN');
      assert.strictEqual(facts.edit_endpoint_permission,'UNKNOWN');
      assert.strictEqual(facts.schema_installation_receipt,'UNKNOWN');
      assert.strictEqual(facts.service_write_path_coverage,'UNKNOWN');
      assert.strictEqual(facts.execution_authority,false);
    }
    await detail.locator('summary').click();
    for(const width of [1440,390]){
      await page.setViewportSize({width,height:900});
      const title=await page.locator('#nextStepTitle').innerText();
      assert.strictEqual(title,expected.common.user_status.summary);
      const status=await page.locator('#publicationFlowStatus').innerText();
      if(mode==='retained')assert(status.includes(expected.common.user_status.candidate_summary));
      else{
        assert.strictEqual(Object.hasOwn(expected.common.user_status,'candidate_summary'),false);
        assert(!status.includes('完整候选资料已恢复'));
      }
      for(const item of expected.common.user_status.pending_items)assert(status.includes(item));
      assert(!/READ|EDIT|schema|APPROVAL_REQUIRED/.test(status),'Main status uses business wording');
      const review=await page.locator('#publicationFlowReview').innerText();
      const ledger=await page.locator('#publicationFlowLedger').innerText();
      const selected=await page.locator('#publicationTargetGrid input[name="publication_target"]:checked').evaluateAll(inputs=>inputs.map(input=>input.value));
      const preparationRange=(selected.includes('miaoshou:COMMON')?'COMMON＋':'')+selected.filter(label=>label!=='miaoshou:COMMON').length+' 个平台目标';
      assert(review.includes('当前准备范围：'+preparationRange));
      const overview=await page.locator('#currentReviewSummary').innerText();
      assert(overview.includes('当前准备范围'));
      assert(overview.includes(preparationRange));
      const legacyPlan=await page.locator('#releasePlanSummary').innerText();
      assert(!/个店铺/.test(legacyPlan));
      assert(!overview.includes(selected.length+' 个目标'));
      assert((await page.locator('#publicationScopeNote').innerText()).includes('当前准备范围：'+preparationRange));
      if(mode==='retained'){
        const frozenCount=expected.marketplace.plan.targets.filter(label=>label!=='miaoshou:COMMON').length;
        assert(review.includes('冻结候选范围：'+frozenCount+' 个平台目标'));
        assert(review.includes('原准备事实已冻结'));
        assert(review.includes('COMMON 完成记录已核对'));
        assert(ledger.includes('COMMON 已完成'));
        assert.strictEqual(await page.locator('#publicationFlowReview table tbody tr').count(),expected.marketplace.manifest.targets.length);
        for(const row of expected.marketplace.manifest.targets)assert(review.includes(row.target_label));
        assert.strictEqual(await page.locator('#publicationFlowReview [data-flow-image]').count(),expected.marketplace.manifest.image_sets.reduce((count,set)=>count+set.images.length,0));
        assert(!/该计划尚未批准|当前事实待批准|本地来源尚未核实/.test(review));
      }else{
        assert.strictEqual(mode,'unknown');
        assert(review.includes('准备来源待核对'));
        assert(ledger.includes('COMMON 准备条件待核对'));
        assert.strictEqual(await page.locator('#publicationFlowReview table').count(),0);
        assert.strictEqual(await page.locator('#publicationFlowReview [data-flow-image]').count(),0);
        assert(!Object.hasOwn(expected.marketplace,'candidate') && !Object.hasOwn(expected.marketplace,'manifest'));
      }
      assert.strictEqual(await detail.evaluate(element=>element.open),false);
      assert.strictEqual(await page.locator('#publicationFlowActions button').count(),0);
      assert.strictEqual(await page.locator('#originalFinalActions button').count(),0);
      assert.strictEqual(await page.locator('#releasePlanApprovalForm').isVisible(),false);
      assert.strictEqual(await page.locator('#legacyReleaseActionPanels').isVisible(),false);
      assert.strictEqual(await page.locator('#approveReleasePlanButton').isDisabled(),true);
      assert.strictEqual(await page.locator('#prepareMiaoshouButton').isDisabled(),true);
      assert.strictEqual(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      const file=out+'/native-common-baseline-flow-'+mode+'-'+width+'.png';
      await page.screenshot({path:file,fullPage:true});screenshots.push(file);
    }
    await bodies.drain();
    assert.strictEqual(bodies.pendingCount,0);assert.strictEqual(bodies.pendingRequestCount,0);
    assert.deepStrictEqual(errors,[]);assert.deepStrictEqual(posts,[]);
    fs.writeFileSync(out+'/actual-baseline-stage-responses.json',JSON.stringify(captured,null,2));
    console.log(JSON.stringify({retained:mode==='retained',errors,posts,screenshots,offer_only_requests:requested.length,
      viewport_widths:[1440,390],authority:'RETAINED_LOCAL_FACTS_NO_EXECUTION_AUTHORITY'}));
  }catch(error){
    fs.writeFileSync(out+'/baseline-flow-failure-dom.html',await page.content());
    await page.screenshot({path:out+'/baseline-flow-failure.png',fullPage:true});throw error;
  }finally{
    try{await bodies.drain();assert.strictEqual(bodies.pendingCount,0);assert.strictEqual(bodies.pendingRequestCount,0);}
    finally{await browser.close();}
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
