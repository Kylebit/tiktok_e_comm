const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{chromium}=require('playwright');
const out=process.argv[2],fixture=JSON.parse(fs.readFileSync(path.join(out,'preview.json'))),base=new URL(fixture.url),fault=process.env.ORBIT_FEEDBACK_FAULT||'';
const checks={},errors=[],blocked=[],mutations=[],receipts=[];
const faultMap={
 weekly_loading:['profit_center.js',"invalidate('正在生成复核')","invalidate('')"],
 weekly_failure:['profit_center.js',"message('errorBanner',e.message||'读取失败')","message('errorBanner','')"],
 weekly_restore:['profit_center.js',"finally{if(run===serial){abort=null;$('reviewButton').disabled=!draft","finally{if(run===serial){abort=null;$('reviewButton').disabled=true"],
 sku_loading:['profit_sku.js',"$('skuStatus').textContent='正在核验所选本地证据…'","$('skuStatus').textContent=''"],
 sku_failure:['profit_sku.js',"$('skuStatus').textContent='读取失败，旧估算已失效：'+e.message","$('skuStatus').textContent=''"],
 sku_restore:['profit_sku.js',"finally{if(thisRun===run){abort=null;$('skuRefresh').disabled=false","finally{if(thisRun===run){abort=null;$('skuRefresh').disabled=true"],
 weekly_aria:['/profit','id="errorBanner" class="error" role="alert"','id="errorBanner" class="error"'],
 sku_aria:['/profit','id="skuStatus" role="status" aria-live="polite"','id="skuStatus"']
};
const hash=s=>crypto.createHash('sha256').update(s).digest('hex');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.ORBIT_BROWSER_EXECUTABLE});
 const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage();
 await context.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url());if(url.origin!==base.origin){blocked.push(url.href);return route.abort();}
  if(req.method()==='POST')await new Promise(r=>setTimeout(r,350));
  const m=faultMap[fault];if(m&&url.pathname.endsWith(m[0])){const r=await route.fetch(),before=await r.text();if(!before.includes(m[1]))throw Error('Missing fault anchor '+fault);const after=before.replace(m[1],m[2]);mutations.push({fault,url:url.href,before_sha256:hash(before),after_sha256:hash(after),before,after});return route.fulfill({response:r,body:after});}
  return route.continue();
 });
 page.on('pageerror',e=>errors.push(String(e)));
 const text=id=>page.locator('#'+id).textContent();
 const check=(name,value)=>checks[name]=!!value;
 async function profile(name){await page.locator('#configure').click();await page.locator('#profileFile').setInputFiles(fixture.profiles[name]);await page.waitForFunction(()=>document.querySelector('#profilePreview').textContent.includes('schema_version'));await page.locator('[data-close=profileDialog]').click();}
 async function responseAfter(action){const pending=page.waitForResponse(r=>r.url().endsWith('/captured-review')&&r.request().method()==='POST');await action();return pending;}
 async function settle(r){receipts.push({status:r.status(),body:await r.json()});await page.waitForTimeout(80);}
 try{
  await page.goto(fixture.url);await profile('slow');
  const weekly=responseAfter(()=>page.locator('#reviewButton').click());
  await page.waitForTimeout(100);
  check('weekly_loading',(await text('statusLabel')).includes('正在生成')&&await page.locator('#reviewButton').isDisabled()&&await page.locator('#cancelReview').isVisible());
  check('weekly_status_region',await page.locator('#statusLabel').evaluate(e=>!!e.closest('[role=status][aria-live=polite]')));
  await page.screenshot({path:path.join(out,'feedback-weekly-loading.png')});await settle(await weekly);
  check('weekly_success',(await text('metricProfit'))!=='—'&&(await text('metricRows')).startsWith('24 /')&&await page.locator('#errorBanner').isHidden());
  check('weekly_restored',!await page.locator('#reviewButton').isDisabled()&&await page.locator('#cancelReview').isHidden());
  await profile('missing');const failure=await responseAfter(()=>page.locator('#reviewButton').click());await settle(failure);
  check('weekly_failure',failure.status()===400&&(await text('statusLabel')).includes('失败')&&await page.locator('#errorBanner').isVisible()&&(await text('errorBanner')).includes('captured_input_check_failed')&&(await text('metricProfit'))==='—');
  check('weekly_error_region',await page.locator('#errorBanner').getAttribute('role')==='alert');
  check('weekly_failure_restored',!await page.locator('#reviewButton').isDisabled()&&await page.locator('#cancelReview').isHidden());
  await page.screenshot({path:path.join(out,'feedback-weekly-failure.png')});
  await profile('sku-ready');const sku=responseAfter(()=>page.locator('#skuEvidence').click());await page.waitForTimeout(100);
  check('sku_loading',(await text('skuStatus')).includes('正在核验')&&await page.locator('#skuRefresh').isDisabled()&&await page.locator('#skuBody>*').count()===0);
  check('sku_status_region',await page.locator('#skuStatus').getAttribute('role')==='status'&&await page.locator('#skuStatus').getAttribute('aria-live')==='polite');
  await page.screenshot({path:path.join(out,'feedback-sku-loading.png')});await settle(await sku);
  const selected=await responseAfter(()=>page.locator('#skuChoice').selectOption('0'));await settle(selected);
  check('sku_success',(await text('skuStatus')).includes('证据已读取')&&await page.locator('#skuEstimate').count()===1);
  check('sku_restored',!await page.locator('#skuRefresh').isDisabled());
  const source=JSON.parse(fs.readFileSync(fixture.profiles['sku-ready'])).sku_evidence_path;
  if(!path.resolve(source).startsWith(path.join(out,'inputs')+path.sep))throw Error('Own synthetic source required');
  const original=fs.readFileSync(source);fs.writeFileSync(source,'{}');
  let missing;try{missing=await responseAfter(()=>page.locator('#skuChoice').selectOption('0'));await settle(missing);}finally{fs.writeFileSync(source,original);}
  check('sku_failure',missing.status()===400&&(await text('skuStatus')).includes('读取失败')&&(await text('skuStatus')).includes('旧估算已失效')&&await page.locator('#skuBody>*').count()===0);
  check('sku_failure_restored',!await page.locator('#skuRefresh').isDisabled());
  await page.screenshot({path:path.join(out,'feedback-sku-failure.png')});
 }catch(e){errors.push(String(e));}
 fs.writeFileSync(path.join(out,'feedback.json'),JSON.stringify({fault,checks,errors,blocked,mutations,receipts},null,2));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(2);});
