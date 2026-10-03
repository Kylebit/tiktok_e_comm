const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,out,failure]=process.argv.slice(2);
const result={base,failure,checks:[],businessPosts:[],foreignRequests:[],pageErrors:[]};
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:process.env.ORBIT_CHROMIUM_BIN});
  let page;
  try{
    const context=await browser.newContext({viewport:{width:1360,height:900}});
    await context.route('**/*',route=>{
      const request=route.request(),url=new URL(request.url());
      if(url.origin!==base){result.foreignRequests.push(request.url());return route.abort();}
      if(request.method()!=='GET'){result.businessPosts.push(url.pathname);return route.abort();}
      return route.continue();
    });
    page=await context.newPage();
    page.on('pageerror',error=>result.pageErrors.push(String(error)));
    const target=base+'/supply-chain/#region=SUMMARY';
    await page.goto(target,{waitUntil:'domcontentloaded'});
    await page.waitForFunction(()=>document.querySelector('#inboundUnits')?.textContent!=='—',null,{timeout:8000});
    const healthy=await page.locator('#inboundUnits').innerText();
    assert.match(healthy,/批.*件|批次在途待对账/);
    const originalRows=await page.locator('#skuRows tr').count();
    assert.ok(originalRows>0);
    result.healthy={inboundHeadline:healthy,rows:originalRows};
    const asset=failure==='data-503'?'**/data.js?*':'**/app.js?*';
    const injected=route=>failure==='data-503'
      ?route.fulfill({status:503,contentType:'application/json',body:'{"ok":false,"code":"SERVICE_BUSY"}'})
      :route.abort('failed');
    await page.route(asset,injected);
    await page.reload({waitUntil:'domcontentloaded'});
    const banner=page.locator('#supplyLoadError');
    await banner.waitFor({state:'visible',timeout:3000});
    assert.match(await banner.innerText(),/加载.*失败|未.*完整加载/);
    assert.equal(await page.locator('#inboundUnits').innerText(),'—');
    await page.screenshot({path:path.join(out,'asset-failure.png'),fullPage:true});
    result.checks.push('asset failure shows a terminal visible error without invented counts');
    await page.unroute(asset,injected);
    await Promise.all([page.waitForNavigation({waitUntil:'domcontentloaded'}),page.locator('#retrySupplyLoad').click()]);
    await page.waitForFunction(()=>document.querySelector('#inboundUnits')?.textContent!=='—',null,{timeout:8000});
    assert.equal(await page.locator('#inboundUnits').innerText(),healthy);
    assert.equal(await page.locator('#skuRows tr').count(),originalRows);
    assert.equal(await banner.isVisible(),false);
    assert.equal(new URL(page.url()).hash,'#region=SUMMARY');
    assert.deepEqual(result.businessPosts,[]);
    assert.deepEqual(result.foreignRequests,[]);
    result.checks.push('retry makes only GET requests and restores the same real source headline and rows');
    result.status='PASS';
  }catch(error){
    result.status='FAILED';result.error=String(error);
    if(page){try{await page.screenshot({path:path.join(out,'failure-state.png'),fullPage:true});}catch{}}
    throw error;
  }finally{
    fs.writeFileSync(path.join(out,'BROWSER_RECEIPT.json'),JSON.stringify(result,null,2));
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
