const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {chromium}=require('C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const {createResponseBodyCapture}=require('./browser/response_body_capture.cjs');
const [origin,out,version]=process.argv.slice(2);
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.ORBIT_BROWSER_EXECUTABLE?{executablePath:process.env.ORBIT_BROWSER_EXECUTABLE}:{channel:'chrome'})});
  let page,errors=[],responses=[],countries=[],consoleErrors=[],requestFailures=[],httpFailures=[],terminalFailure=null,cleanupBodyFailure=null,lifecycle=[];
  const prefix=origin+'/supply-chain/audited/'+version+'/';
  const mark=(event,request)=>lifecycle.push({event,url:request?.url()||page?.url()||null,timeNs:process.hrtime.bigint().toString(),pendingBodies:bodyCapture.pendingCount,pendingRequests:bodyCapture.pendingRequestCount});
  const bodyCapture=createResponseBodyCapture((response,raw)=>{
    const prefix=origin+'/supply-chain/audited/'+version+'/';
    responses.push({name:response.url().slice(prefix.length),status:response.status(),sha256:crypto.createHash('sha256').update(raw).digest('hex')});
  });
  try {
    const context=await browser.newContext({viewport:{width:1440,height:900}});page=await context.newPage();
    await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
    page.on('pageerror',error=>errors.push(error.message));
    page.on('console',message=>{if(message.type()==='error')consoleErrors.push(message.text());});
    page.on('request',request=>{if(request.url().startsWith(prefix)){bodyCapture.requestStarted(request);mark('request-start',request);}});
    page.on('requestfinished',request=>{if(request.url().startsWith(prefix)){bodyCapture.requestFinished(request);mark('request-finished',request);}});
    page.on('requestfailed',request=>{
      requestFailures.push({url:request.url(),failure:request.failure()});
      if(request.url().startsWith(prefix)){bodyCapture.requestFailed(request);mark('request-failed',request);}
    });
    page.on('response',response=>{
      if(response.status()>=400)httpFailures.push({url:response.url(),status:response.status()});
      const prefix=origin+'/supply-chain/audited/'+version+'/';
      if(response.url().startsWith(prefix)){mark('response',response.request());bodyCapture.capture(response);}
    });
    await page.goto(origin+'/supply-chain/');
    const ready=async()=>{
      await page.waitForFunction(()=>['READY_MANUAL_DISPLAY','UNAVAILABLE'].includes(window.SUPPLY_CHAIN_AUDITED_STATE?.status));
      assert.equal(await page.evaluate(()=>window.SUPPLY_CHAIN_AUDITED_STATE.status),'READY_MANUAL_DISPLAY',JSON.stringify({errors,consoleErrors,requestFailures,httpFailures}));
    };
    await ready();
    const checkImages=async()=>{
      await page.waitForFunction(()=>[...document.querySelectorAll('#skuRows img')].length>0 && [...document.querySelectorAll('#skuRows img')].every(image=>image.complete && image.naturalWidth>0));
      assert.ok(await page.locator('#skuRows img').evaluateAll((images,prefix)=>images.every(image=>new URL(image.src).pathname.startsWith(prefix)),'/supply-chain/audited/'+version+'/assets/'));
    };
    for(const country of ['MY','TH','VN','PH']){
      await page.locator('.country-tab[data-region="'+country+'"]').click();
      await checkImages();countries.push(country);
      assert.ok((await page.locator('#skuRows').innerText()).includes('0001'));
      assert.ok((await page.locator('#warehouseLabel').innerText()).includes(country));
    }
    await page.locator('.country-tab[data-region="SUMMARY"]').click();
    assert.ok((await page.locator('#manualSnapshotEvidence').innerText()).includes('结算沿用旧事实'));
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:path.join(out,'manual-four-country.png'),fullPage:true});
    mark('reload-barrier-start');await bodyCapture.drain();mark('reload-barrier-complete');
    await page.reload();await ready();
    await checkImages();
    const state=await page.evaluate(()=>({version:window.SUPPLY_CHAIN_AUDITED_STATE.version,executionAuthority:window.SUPPLY_CHAIN_AUDITED_STATE.executionAuthority,captureStatePresent:!!window.SUPPLY_CHAIN_CAPTURE_STATE}));
    assert.equal(state.version,version);assert.equal(state.executionAuthority,false);assert.equal(state.captureStatePresent,false);
    mark('inbound-barrier-start');await bodyCapture.drain();mark('inbound-barrier-complete');
    await page.goto(origin+'/supply-chain/inbound-batches.html');
    await ready();
    assert.ok(await page.locator('#manualSnapshotEvidence').isVisible());
    await page.screenshot({path:path.join(out,'manual-inbound.png'),fullPage:true});
    await bodyCapture.drain();assert.deepEqual(errors,[]);
    assert.equal(bodyCapture.attemptedCount,responses.length);
    assert.equal(bodyCapture.pendingCount,0);
    assert.equal(bodyCapture.pendingRequestCount,0);
    fs.writeFileSync(path.join(out,'browser-result.json'),JSON.stringify({...state,fourCountries:countries,allImagesRendered:true,allImagesVersionBound:true,refreshSameVersion:true,pageErrors:errors,responses},null,2));
  } catch(error){terminalFailure={name:error.name,message:error.message};throw error;
  } finally {
    if(page){
      let state=null,body=null;
      try{state=await page.evaluate(()=>window.SUPPLY_CHAIN_AUDITED_STATE||null);body=await page.locator('body').innerText();}catch(error){body=String(error);}
      try{mark('cleanup-barrier-start');await bodyCapture.drain();mark('cleanup-barrier-complete');}catch(error){cleanupBodyFailure=error;if(!terminalFailure)terminalFailure={name:error.name,message:error.message};}
      fs.writeFileSync(path.join(out,'browser-diagnostic.json'),JSON.stringify({state,body,pageErrors:errors,consoleErrors,requestFailures,httpFailures,responses,attemptedResponseCount:bodyCapture.attemptedCount,pendingResponseCount:bodyCapture.pendingCount,pendingRequestCount:bodyCapture.pendingRequestCount,trackedRequestCount:bodyCapture.trackedRequestCount,lifecycle,bodyCaptureFailures:bodyCapture.failures.map(row=>({url:row.url,name:row.error.name,message:row.error.message})),fourCountries:countries,terminalFailure},null,2));
      if(terminalFailure)await page.screenshot({path:path.join(out,'browser-failure.png'),fullPage:true}).catch(()=>{});
    }
    await browser.close();
    if(cleanupBodyFailure)throw cleanupBodyFailure;
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
