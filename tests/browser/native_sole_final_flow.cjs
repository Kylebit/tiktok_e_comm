const {chromium}=require('playwright');
const assert=require('assert'),fs=require('fs');
const {createResponseBodyCapture}=require('./response_body_capture.cjs');
const [base,out,offer,mode,expectedPath]=process.argv.slice(2);
const fixture=JSON.parse(fs.readFileSync(expectedPath,'utf8'));
const expected=fixture.getter.marketplace.native_final_review,media=new Set(fixture.media);
(async()=>{
  const executablePath=process.env.ORBIT_BROWSER_EXECUTABLE||process.env.ORBIT_CHROMIUM_BIN;
  const browser=await chromium.launch({headless:true,...(executablePath?{executablePath}:{channel:'chrome'})});
  const context=await browser.newContext({viewport:{width:1440,height:900}});
  const page=await context.newPage(),errors=[],posts=[],views=[],screenshots=[],overflowChecks=[],unavailableViews=[];
  const owned=request=>new URL(request.url()).pathname==='/api/product-workspace/publication-stages';
  const bodies=createResponseBodyCapture((response,raw)=>{assert.strictEqual(response.status(),200);views.push(JSON.parse(raw));});
  page.on('pageerror',error=>errors.push(error.message));
  page.setDefaultTimeout(30000);
  let displayDrift=null;
  await context.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(!request.url().startsWith(base+'/')){
      assert(request.resourceType()==='image'&&media.has(request.url()),'No undeclared foreign request');
      return route.fulfill({status:200,contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200"><rect width="200" height="200" fill="#bea"/></svg>'});
    }
    // The same finite fixture media also travels through the real page's
    // bounded proxy route. Fulfil that closed edge here, rather than asking
    // the owned Python server to resolve example.com/fixture.example.
    if(url.pathname==='/api/proxy-image'){
      assert.strictEqual(request.method(),'GET');
      assert(media.has(url.searchParams.get('url')),'Only declared fixture proxy media');
      return route.fulfill({status:200,contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200"><rect width="200" height="200" fill="#bea"/></svg>'});
    }
    if(['/api/product-flow/preview','/api/product-flow/content-package/localized-image-review'].includes(url.pathname)){
      assert.strictEqual(request.method(),'GET');
      assert.strictEqual(url.searchParams.get('offer_id'),offer);
      assert.deepStrictEqual([...url.searchParams.keys()],['offer_id']);
      // This owned native fixture does not install the separate legacy image
      // service. Report its actual unavailability, not a fabricated image
      // result and not a request to an unrelated localhost:8766 process.
      unavailableViews.push(url.pathname);
      return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({
        ok:false,offer_id:offer,error:'OWNED_LEGACY_IMAGE_SERVICE_UNAVAILABLE'})});
    }
    if(owned(request) && displayDrift){
      const response=await route.fetch(),value=await response.json();
      assert.strictEqual(response.status(),200);
      const descriptor=value.marketplace.native_final_review;
      if(displayDrift==='candidate')descriptor.candidate_digest='foreign-candidate';
      else if(displayDrift==='offer')descriptor.offer_id='foreign-offer';
      else if(displayDrift==='saved')descriptor.approval_saved=true;
      else if(displayDrift==='targets')descriptor.targets=descriptor.targets.slice(1);
      else delete value.marketplace.native_final_review;
      return route.fulfill({response,json:value});
    }
    if(request.method()!=='GET'){
      assert.strictEqual(request.method(),'POST');
      assert(['/api/product-workspace/native-final/prepare','/api/product-workspace/native-final/decision'].includes(url.pathname),'Only original sole-review channel');
      const body=request.postDataJSON();posts.push({path:url.pathname,body});
      if(url.pathname.endsWith('/prepare')){
        assert.deepStrictEqual(body,{plan_id:expected.plan_id});
        const response=await route.fetch(),value=await response.json();
        assert.strictEqual(response.status(),200);
        assert.deepStrictEqual(value.review,expected.review);
        assert.strictEqual(value.review_digest,expected.review_digest);
        if(mode==='changed')value.review.targets=value.review.targets.slice(1);
        return route.fulfill({response,json:value});
      }
      assert.deepStrictEqual(Object.keys(body).sort(),['nonce','review_digest']);
      assert.strictEqual(body.review_digest,expected.review_digest);
    }
    return route.continue();
  });
  page.on('request',request=>{if(owned(request))bodies.requestStarted(request);});
  page.on('requestfinished',request=>{if(owned(request))bodies.requestFinished(request);});
  page.on('requestfailed',request=>{if(owned(request))bodies.requestFailed(request);});
  page.on('response',response=>{if(owned(response.request()))bodies.capture(response);});
  try{
    for(const drift of ['candidate','offer','saved','targets','missing']){
      displayDrift=drift;
      await page.goto(base+'/product-workspace?offer_id='+offer+'&round=final');
      await page.locator('#tab-release').click();
      await bodies.drain();
      await page.waitForFunction(()=>document.querySelector('#publicationFlowStatus').textContent.includes('核对本次目标'));
      assert.strictEqual(await page.locator('#publicationFlowActions button',{hasText:'批准当前最终候选'}).count(),0);
      assert.deepStrictEqual(posts,[]);
    }
    displayDrift=null;
    await page.goto(base+'/product-workspace?offer_id='+offer+'&round=final');
    await page.locator('#tab-release').click();
    const approve=page.locator('#publicationFlowActions button',{hasText:'批准当前最终候选'});
    await approve.waitFor({state:'visible'});
    await bodies.drain();assert(views.length>0);
    assert.deepStrictEqual(views.at(-1).marketplace.native_final_review.review,expected.review);
    assert.strictEqual(await page.locator('#publicationFlowReview table tbody tr').count(),expected.targets.length);
    await approve.click();
    if(mode==='changed'){
      await page.waitForFunction(()=>document.querySelector('#publicationFlowStatus')?.textContent.includes('当前点击没有批准新候选'));
      assert.strictEqual(posts.filter(row=>row.path.endsWith('/decision')).length,0);
      assert.strictEqual(await page.locator('[data-native-target-results]').count(),0);
    }else{
      await page.waitForFunction(count=>document.querySelectorAll('[data-native-target-results] tbody tr').length===count,expected.targets.length);
      await page.waitForFunction(()=>[...document.querySelectorAll('[data-native-target-results] tbody tr')].every(row=>row.textContent.includes('已提交')));
      assert.strictEqual(posts.filter(row=>row.path.endsWith('/decision')).length,1);
      assert.strictEqual(await approve.count(),0);
      for(const target of expected.targets)assert((await page.locator('[data-native-target-results]').innerText()).includes(target));
      assert(!(await page.locator('[data-native-target-results]').innerText()).includes('已完成官方回读'));
    }
    for(const width of [1440,390]){
      await page.setViewportSize({width,height:900});
      assert.strictEqual(await page.locator('#releasePlanApprovalForm').isVisible(),false);
      assert.strictEqual(await page.locator('#prepareMiaoshouButton').isDisabled(),true);
      assert.strictEqual(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      overflowChecks.push(await page.evaluate(()=>({viewport:innerWidth,scrollWidth:document.documentElement.scrollWidth})));
      const file=out+'/native-sole-final-'+mode+'-'+width+'.png';
      await page.screenshot({path:file,fullPage:true});screenshots.push(file);
    }
    await bodies.drain();assert.strictEqual(bodies.pendingCount,0);assert.strictEqual(bodies.pendingRequestCount,0);
    assert.deepStrictEqual(errors,[]);
    fs.writeFileSync(out+'/native-review-http-observations.json',JSON.stringify({posts,views},null,2));
    console.log(JSON.stringify({widths:[1440,390],errors,screenshots,overflowChecks,unavailableViews,
      decision_posts:posts.filter(row=>row.path.endsWith('/decision')).length}));
  }catch(error){
    const overflow=await page.evaluate(()=>({viewport:{width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth},
      offenders:[...document.querySelectorAll('body *')].map(el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return {
        selector:el.tagName.toLowerCase()+(el.id?'#'+el.id:'')+(typeof el.className==='string'?'.'+el.className.trim().split(/\s+/).join('.'):''),
        right:r.right,left:r.left,width:r.width,height:r.height,scrollWidth:el.scrollWidth,clientWidth:el.clientWidth,
        display:s.display,minWidth:s.minWidth,overflowX:s.overflowX,overflowWrap:s.overflowWrap,wordBreak:s.wordBreak,
        text:(el.innerText||'').slice(0,180)}}).filter(row=>row.width>0&&row.height>0&&row.right>innerWidth+1).sort((a,b)=>b.right-a.right).slice(0,35)}));
    fs.writeFileSync(out+'/native-overflow-diagnostic.json',JSON.stringify(overflow,null,2));
    fs.writeFileSync(out+'/native-sole-final-failure.html',await page.content());await page.screenshot({path:out+'/native-sole-final-failure.png',fullPage:true});throw error;
  }
  finally{try{await bodies.drain();assert.strictEqual(bodies.pendingCount,0);assert.strictEqual(bodies.pendingRequestCount,0);}finally{await browser.close();}}
})().catch(error=>{console.error(error);process.exitCode=1;});
