// Actual task-bound receipt HTTP from a temporary Python fixture. Never a live service.
const {chromium}=require('C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,taskId,output,mode]=process.argv.slice(2);
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base)||!/^TASK-\d{8}-\d+$/.test(taskId)||!output)throw Error('exact local fixture arguments required');
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:'C:/Users/Windows11/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe'});
  const errors=[],requests=[],blocked=[];
  try{
    // Fresh context has no registrations. The receipt's opaque CSP sandbox and
    // default-src 'none' block scripts/workers; Playwright's service-worker
    // blocker injects a Navigator probe that itself fails in this sandbox.
    const context=await browser.newContext();
    await context.route('**/*',route=>{
      const request=route.request(),url=new URL(request.url());
      requests.push({method:request.method(),path:url.pathname});
      if(url.origin!==base||request.method()!=='GET'){blocked.push(request.url());return route.abort();}
      if(url.pathname==='/favicon.ico')return route.fulfill({status:204});
      return route.continue();
    });
    const page=await context.newPage();
    page.on('pageerror',error=>errors.push(String(error)));
    page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
    for(const width of [1440,390]){
      await page.setViewportSize({width,height:950});
      const response=await page.goto(base+'/api/orbit/tasks/'+taskId+'/delisting-receipt',{waitUntil:'networkidle'});
      assert.equal(response.status(),200);
      assert.equal(await page.locator('h1').innerText(),'下架任务回执');
      const text=await page.locator('body').innerText();
      assert.ok(text.includes(taskId));assert.ok(text.includes('未查询平台'));
      assert.ok(!/SECRET-|LEASE-CANARY|credential\.json/.test(text));
      assert.equal(await page.locator('form,script,button').count(),0);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,'no page-level horizontal overflow');
      if(mode==='complete')assert.ok(text.includes('回执完整，任务已完成')&&text.includes('2 / 2'));
      if(mode==='partial')assert.ok(text.includes('部分确认')&&text.includes('1 / 2'));
      if(mode==='missing')assert.ok(text.includes('REFERENCE_MISSING')&&text.includes('回执证据缺口'));
      if(mode==='unknown')assert.ok(text.includes('回读待核对')&&text.includes('0 / 2'));
      await page.screenshot({path:path.join(output,'receipt-'+mode+'-'+width+'.png'),fullPage:true});
      if(width===390){
        const table=page.locator('.table');
        assert.equal(await table.evaluate(el=>el.scrollWidth>el.clientWidth),true);
        await table.evaluate(el=>el.scrollLeft=el.scrollWidth);
        await page.screenshot({path:path.join(output,'receipt-'+mode+'-'+width+'-status-columns.png'),fullPage:true});
      }
    }
    assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);
    assert.ok(requests.every(r=>r.method==='GET'));
    fs.writeFileSync(path.join(output,'BROWSER_RESULT.json'),JSON.stringify({mode,requests,blocked,errors,viewports:[1440,390]},null,2));
    console.log('PASS actual synthetic receipt HTTP, desktop/mobile and zero mutation: '+mode);
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
