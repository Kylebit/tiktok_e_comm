const {chromium}=require('playwright');
const assert=require('assert');
const fs=require('fs');
const base=process.argv[2],file=process.argv[3],out=process.argv[4];
(async()=>{
 const executable=process.env.ORBIT_BROWSER_EXECUTABLE;
 assert(executable,'Explicit browser executable required');
 const browser=await chromium.launch({executablePath:executable,headless:true});
 const context=await browser.newContext();
 const page=await context.newPage();
 const nonce=fs.readFileSync(file,'utf8');
 const requests=[],responses=[];
 page.on('request',r=>{assert(r.url().startsWith(base),'No external request');requests.push(r.url());});
 page.on('response',r=>responses.push(r.url()));
 try{
  // Instrument the actual bootstrap request: fragment must already be gone.
  await page.route('**/local-operator/bootstrap',async route=>{
   assert.equal(await page.evaluate(()=>location.hash),'');
   return route.continue();
  });
  await page.goto(base+'/local-operator-init#'+nonce);
  await page.waitForURL(/\/product-workspace\?offer_id=.+&plan_id=.+/);
  const cookies=await context.cookies();
  const op=cookies.find(c=>c.name.startsWith('orbit_operator_'));
  const csrf=cookies.find(c=>c.name.startsWith('orbit_csrf_'));
  assert(op&&csrf&&op.httpOnly&&!csrf.httpOnly);
  assert.equal(op.sameSite,'Strict');assert.equal(csrf.sameSite,'Strict');
  assert(!(await page.evaluate(()=>document.cookie)).includes(op.value));
  for(const url of requests){assert(!url.includes(op.value)&&!url.includes(csrf.value));}
  const status=await page.evaluate(async()=>{
   const context=await(await fetch('/api/product-workspace/local-operator/context')).json();
   const token=document.cookie.split('; ').find(c=>c.startsWith(context.csrf_cookie_name+'=')).split('=')[1];
   const headers={'Content-Type':'application/json','X-Orbit-CSRF-Token':token};
   const result=await fetch('/api/product-workspace/local-operator/prepare',{method:'POST',headers,body:JSON.stringify({reservation_id:'private-reservation'})});
   const prepared=await result.json();
   const body=JSON.stringify({nonce:prepared.nonce,review_digest:prepared.review_digest});
   const first=await(await fetch('/api/product-workspace/local-operator/decision',{method:'POST',headers,body})).json();
   const replay=await(await fetch('/api/product-workspace/local-operator/decision',{method:'POST',headers,body})).json();
   return {status:result.status,first,replay};
  });
  assert.equal(status.status,200);assert.equal(status.first.decision_id,status.replay.decision_id);
  assert.equal(status.first.execution_authority,false);
  const repeat=await page.evaluate(async n=>(await fetch('/api/product-workspace/local-operator/bootstrap',{
   method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({handoff:n})})).status,nonce);
  assert.equal(repeat,403);
  await page.screenshot({path:out+'/local-bootstrap.png',fullPage:true});
  console.log(JSON.stringify({fragment_cleared_before_post:true,operator_cookie_http_only:true,
   separate_script_readable_csrf:true,target_preserved:true,replay_same_decision:true,
   second_handoff_consume_status:repeat,execution_authority:false}));
 }finally{await browser.close();}
})();
