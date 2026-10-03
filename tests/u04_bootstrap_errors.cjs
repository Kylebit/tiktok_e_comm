const fs=require('node:fs'),http=require('node:http'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {chromium}=require('C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const [source,out,mode]=process.argv.slice(2),version='a'.repeat(64),errors=[],requests=[];
const scripts={'data.js':'window.syntheticData=true;','inbound-plan.js':'window.syntheticPlan=true;',
 'app.js':mode==='throw'?'throw new Error("ROOT_SYNTHETIC_INITIALIZATION_FAILURE");':mode==='reject'?'Promise.reject(new Error("ROOT_SYNTHETIC_REJECTION"));':'window.syntheticApp=true;',
 'later.js':'window.syntheticLater=true;'};
const entries=Object.entries(scripts).map(([name,body])=>({src:'/supply-chain/'+name,integrity:'sha256-'+crypto.createHash('sha256').update(body).digest('base64')}));
const bootstrap=fs.readFileSync(source),storage='{"synthetic":"unchanged bytes"}';
const server=http.createServer((req,res)=>{
 const name=req.url.split('/').pop();requests.push(name);
 if(name==='') {res.setHeader('Content-Type','text/html; charset=utf-8');res.end(`<html><body>SYNTHETIC DISPLAY<script src="/supply-chain/bootstrap.js" data-captured-version="${version}" data-captured-scripts='${JSON.stringify(entries)}'></script></body></html>`);}
 else if(name==='bootstrap.js'){res.setHeader('Content-Type','application/javascript');res.end(bootstrap);}
 else if(name in scripts){res.setHeader('Content-Type','application/javascript');res.end(scripts[name]);}
 else {res.statusCode=404;res.end();}
});
(async()=>{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch({headless:true,...(process.env.ORBIT_BROWSER_EXECUTABLE?{executablePath:process.env.ORBIT_BROWSER_EXECUTABLE}:{channel:'chrome'})});
 try {
  const context=await browser.newContext();await context.addInitScript(value=>localStorage.setItem('synthetic-overlay',value),storage);
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await context.route('**/*',route=>new URL(route.request().url()).origin!==origin?route.abort():mode==='abort'&&route.request().url().endsWith('/inbound-plan.js')?route.abort():route.continue());
  await page.goto(origin+'/supply-chain/');await page.waitForFunction(()=>['COMPLETE','UNAVAILABLE'].includes(window.SUPPLY_CHAIN_CAPTURE_STATE?.status));
  await page.waitForTimeout(50);
  const observed=await page.evaluate(()=>({status:window.SUPPLY_CHAIN_CAPTURE_STATE.status,hidden:document.documentElement.hidden,text:document.body.innerText,data:!!window.syntheticData,plan:!!window.syntheticPlan,later:!!window.syntheticLater,storage:localStorage.getItem('synthetic-overlay')}));
  await page.evaluate(()=>setTimeout(()=>{throw new Error('UNRELATED_LATER_ERROR');},0));await page.waitForTimeout(50);
  const afterLate=await page.evaluate(()=>window.SUPPLY_CHAIN_CAPTURE_STATE.status);
  fs.writeFileSync(out,JSON.stringify({mode,source_sha256:crypto.createHash('sha256').update(bootstrap).digest('hex'),observed,afterLate,errors,requests},null,2));
  assert.equal(observed.storage,storage);assert.equal(observed.hidden,false);assert.ok(errors.includes('UNRELATED_LATER_ERROR'));assert.equal(afterLate,observed.status);
  if(mode==='normal'){assert.equal(observed.status,'COMPLETE');assert.equal(observed.later,true);}
  else {assert.equal(observed.status,'UNAVAILABLE');assert.equal(observed.later,false);assert.ok(!requests.includes('later.js'));assert.ok(observed.text.includes('暂不可用'));}
  if(mode==='throw'||mode==='reject'){assert.ok(observed.data&&observed.plan);assert.ok(errors.some(x=>x.includes(mode==='throw'?'ROOT_SYNTHETIC_INITIALIZATION_FAILURE':'ROOT_SYNTHETIC_REJECTION')));}
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>server.close());
