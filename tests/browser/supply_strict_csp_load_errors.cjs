"use strict";
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const [origin,out,csp]=process.argv.slice(2);
const result={status:'RUNNING',headers:[],blockedOwnedAssetCount:0,foreignRequests:[],businessPosts:[],pageErrors:[],healthyScriptViolations:[],consoleErrors:[]};
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.ORBIT_BROWSER_EXECUTABLE?{executablePath:process.env.ORBIT_BROWSER_EXECUTABLE}:{channel:'chrome'})});
  let page;
  try{
    const context=await browser.newContext({viewport:{width:1440,height:900}});
    await context.route('**/*',route=>{
      const request=route.request();
      if(new URL(request.url()).origin!==origin){result.foreignRequests.push(request.url());return route.abort();}
      if(request.method()!=='GET'){result.businessPosts.push(request.url());return route.abort();}
      return route.continue();
    });
    page=await context.newPage();
    await page.addInitScript(()=>{
      window.__supplyScriptViolations=[];
      document.addEventListener('securitypolicyviolation',event=>{
        if(event.effectiveDirective==='script-src-elem'||event.effectiveDirective==='script-src')
          window.__supplyScriptViolations.push({directive:event.effectiveDirective,blockedURI:event.blockedURI,originalPolicy:event.originalPolicy});
      });
    });
    page.on('pageerror',error=>result.pageErrors.push(String(error)));
    page.on('console',message=>{if(message.type()==='error')result.consoleErrors.push({text:message.text(),location:message.location()});});
    const document=await page.goto(origin+'/supply-chain/#region=MY',{waitUntil:'networkidle'});
    result.headers.push(document.headers()['content-security-policy']);
    assert.equal(result.headers.at(-1),csp);
    await page.waitForFunction(()=>document.querySelector('#skuRows')?.textContent.includes('0001'),null,{timeout:8000});
    assert.equal(await page.evaluate(()=>!!window.SUPPLY_CHAIN_AUDITED_STATE||!!window.SUPPLY_CHAIN_CAPTURE_STATE),false);
    assert.equal(await page.locator('#supplyLoadError').isVisible(),false);
    const healthy=await page.locator('#skuRows').innerText();
    assert.ok(healthy.includes('0001'));
    result.healthyScriptViolations=await page.evaluate(()=>window.__supplyScriptViolations);
    const failedAsset=origin+'/supply-chain/data.js?*';
    const abortOwned=route=>{result.blockedOwnedAssetCount++;return route.abort('failed');};
    await page.route(failedAsset,abortOwned);
    const broken=await page.reload({waitUntil:'networkidle'});
    result.headers.push(broken.headers()['content-security-policy']);
    assert.equal(result.headers.at(-1),csp);
    result.failedState=await page.evaluate(()=>({banner:document.querySelector('#supplyLoadError')?.outerHTML,
      fallbackType:typeof window.supplyChainLoadFailed,scriptViolations:window.__supplyScriptViolations,
      auditedState:window.SUPPLY_CHAIN_AUDITED_STATE||null}));
    assert.equal(result.blockedOwnedAssetCount,1);
    await page.locator('#supplyLoadError').waitFor({state:'visible',timeout:3000});
    assert.match(await page.locator('#supplyLoadError').innerText(),/加载.*失败|未.*完整加载/);
    result.visibleTerminalFailure=true;
    await page.screenshot({path:path.join(out,'strict-csp-asset-failure.png'),fullPage:true});
    await page.unroute(failedAsset,abortOwned);
    const recovered=await Promise.all([page.waitForNavigation({waitUntil:'networkidle'}),page.locator('#retrySupplyLoad').click()]);
    result.headers.push(recovered[0].headers()['content-security-policy']);
    assert.equal(result.headers.at(-1),csp);
    await page.waitForFunction(()=>document.querySelector('#skuRows')?.textContent.includes('0001'),null,{timeout:8000});
    assert.equal(await page.locator('#skuRows').innerText(),healthy);
    assert.equal(await page.locator('#supplyLoadError').isVisible(),false);
    assert.equal(new URL(page.url()).hash,'#region=MY');
    assert.deepEqual(result.healthyScriptViolations,[]);
    assert.deepEqual(result.businessPosts,[]);assert.deepEqual(result.foreignRequests,[]);
    result.recoveredSameSyntheticRows=true;result.status='PASS';
  }catch(error){
    result.status='FAILED';result.error={name:error.name,message:error.message};
    if(page){result.terminalDOM=await page.locator('#supplyLoadError').evaluate(element=>element.outerHTML);await page.screenshot({path:path.join(out,'strict-csp-failure.png'),fullPage:true});}
    throw error;
  }finally{
    fs.writeFileSync(path.join(out,'strict-csp-browser-result.json'),JSON.stringify(result,null,2));
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
