const {chromium}=require('playwright');
const fs=require('fs'),path=require('path'),assert=require('assert'),crypto=require('crypto');
(async()=>{
  const output=fs.realpathSync(process.argv[2]),scenario=JSON.parse(fs.readFileSync(path.join(output,'scenario.json')));
  const {base,offer,width,mode}=scenario, requests=[],resources=[],errors=[],denied=[],responses=[],consoleErrors=[];
  const home=path.join(output,'home');fs.mkdirSync(home,{recursive:true});
  const browser=await chromium.launch({headless:true,
    executablePath:'C:/Users/Windows11/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe',
    env:{...process.env,USERPROFILE:home,LOCALAPPDATA:path.join(home,'Local'),APPDATA:path.join(home,'Roaming')},
    args:['--disable-background-networking','--disable-component-update','--no-first-run']});
  const context=await browser.newContext({viewport:{width,height:950},serviceWorkers:'block'});
  await context.route('**/*',route=>{
    const url=route.request().url();
    if(url.startsWith(base+'/')||url.startsWith('data:')||url.startsWith('blob:'))return route.continue();
    denied.push(url);return route.abort();
  });
  const page=await context.newPage();page.on('pageerror',e=>errors.push(String(e)));
  page.on('console',event=>{if(event.type()==='error')consoleErrors.push({text:event.text(),location:event.location()});});
  page.on('request',r=>requests.push({url:r.url(),method:r.method(),body:r.postData()}));
  const pending=[];
  page.on('response',r=>pending.push((async()=>{
    if(r.url().includes('/static/')||r.url().includes('/product-workspace?')){
      const body=await r.body();resources.push({url:r.url(),status:r.status(),sha256:crypto.createHash('sha256').update(body).digest('hex')});
    }
    if(r.url().includes('/round1-category/')||r.url().endsWith('/approve')||r.url().includes('/publication-stages?')){
      if(mode==='malformed' && r.url().endsWith('/options'))responses.push({url:r.url(),status:r.status(),intentionalMalformedBody:await r.text()});
      else responses.push({url:r.url(),status:r.status(),value:await r.json()});
    }
  })().catch(e=>errors.push(String(e)))));
  async function finish(){
    await Promise.all(pending);assert.deepStrictEqual(errors,[]);assert.deepStrictEqual(denied,[]);
    const expectedFailure=['lost-options','lost-capture','lost-approval','not-started'].includes(mode) ? /net::ERR_FAILED/ : mode==='unknown409' ? /409/ : null;
    const expectedR3Prerequisite = row => /409/.test(row.text) && responses.some(response=>
      response.url===row.location.url && new URL(response.url).origin===base
      && new URL(response.url).pathname==='/api/product-workspace/publication-stages'
      && response.status===409 && response.value?.schema_version==='publication-stages/v1'
      && ['FIRST_ROUND_REQUIRED','SECOND_ROUND_REQUIRED'].includes(response.value.stage)
      && response.value.missing_documents?.length>0 && response.value.external_writes_performed?.length===0);
    assert(consoleErrors.every(row=>expectedR3Prerequisite(row) || expectedFailure && expectedFailure.test(row.text)),JSON.stringify(consoleErrors));
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    // Reconciliation scenarios assert the browser state and response ledger above.
    // A full-page screenshot for every fault case repeatedly taxes Chromium on
    // Windows and is not consumed by these assertions or the release gate.
    if(!mode)await page.screenshot({path:path.join(output,`journey-${width}.png`),fullPage:true});
    fs.writeFileSync(path.join(output,'BROWSER_RESULT.json'),JSON.stringify({passed:true,width,mode,requests,responses,resources,errors,denied,consoleErrors,expectedConsoleFailure:expectedFailure?.source||null},null,2));
  }
  let releaseLate,lateReady;
  const lateStarted=new Promise(resolve=>{lateReady=resolve;});
  if(['lost-options','not-started','late','legacy','malformed'].includes(mode)){
    await page.route('**/round1-category/options',async route=>{
      if(mode==='not-started')return route.abort();
      const response=await route.fetch();
      if(mode==='lost-options')return route.abort();
      if(mode==='malformed')return route.fulfill({status:200,contentType:'application/json',body:'{not-json'});
      if(mode==='late'){lateReady();await new Promise(resolve=>{releaseLate=resolve;});return route.fulfill({response});}
      const value=await response.json();value.progress={purpose:'OPTIONS',attempted:null,completed:null,stage:'LEGACY_UNMEASURED'};
      return route.fulfill({response,json:value});
    });
  }
  if(mode==='lost-capture')await page.route('**/round1-category/capture',async route=>{await route.fetch();return route.abort();});
  if(mode==='lost-approval')await page.route('**/product-workspace/approve',async route=>{await route.fetch();return route.abort();});
  try{
    await page.goto(base+'/product-workspace?offer_id='+offer);
    const panel=page.locator('#round1Category');await panel.waitFor();
    assert.strictEqual(await panel.locator('select').inputValue(),'');
    assert.strictEqual(requests.filter(r=>r.method==='POST').length,0);
    await panel.locator('select').selectOption('MY');
    if(['history','invalid-history'].includes(mode)){
      const reuse=panel.getByRole('button',{name:/复用历史凭据/});await reuse.waitFor();
      if(mode==='invalid-history')assert(await reuse.isDisabled());
      else{
        assert(await panel.getByRole('button',{name:'读取类目候选',exact:true}).isDisabled());
        await reuse.click();await panel.getByRole('button',{name:'准备完整首轮审核',exact:true}).waitFor();
        assert(await panel.getByRole('button',{name:'读取类目候选',exact:true}).isDisabled());
        assert.strictEqual(requests.filter(r=>r.method==='POST').length,1);
        assert(requests.find(r=>r.method==='POST').url.endsWith('/resolve'));
      }
      await finish();return;
    }
    const options=panel.getByRole('button',{name:'读取类目候选',exact:true});await options.waitFor();
    await page.waitForFunction(()=>!Array.from(document.querySelectorAll('#round1Category button')).find(b=>b.textContent==='读取类目候选').disabled);
    await options.dblclick();
    if(mode==='late'){
      await lateStarted;
      await page.locator('#queueDisclosure > summary').click();
      await page.locator('#offerId').fill('3828811809');await page.locator('#lookupForm').getByRole('button',{name:'加入并读取'}).click();
      await page.locator('#productTitle').filter({hasText:'Second synthetic product'}).waitFor();
      releaseLate();
      await page.waitForFunction(()=>JSON.parse(localStorage.getItem('orbit-r1-category-operations-v1')||'[]').some(op=>op.scope.offer_id==='3828811808'&&op.result?.status==='SUCCEEDED'));
      assert.strictEqual(await panel.locator('input[name="r1-category-choice"]').count(),0);
      assert.strictEqual(await panel.locator('select').inputValue(),'');
      await finish();return;
    }
    if(['lost-options','not-started','unknown','failed','malformed','unknown409'].includes(mode)){
      await page.waitForFunction(()=>['UNKNOWN','FAILED'].includes(document.querySelector('#round1Category [data-r1-status]')?.dataset.r1Status));
      if(mode==='failed'){
        assert((await panel.textContent()).includes('CATEGORY_CANDIDATE_TECHNICAL_LIMIT'));await finish();return;
      }
      await page.reload();await panel.waitFor();await panel.locator('select').selectOption('MY');
      if(['lost-options','malformed'].includes(mode)){
        await panel.locator('input[name="r1-category-choice"]').first().waitFor();
        assert.strictEqual(await panel.locator('input[name="r1-category-choice"]:checked').count(),0);
      }else{
        await page.waitForFunction(s=>document.querySelector('#round1Category [data-r1-status]')?.dataset.r1Status===s,mode==='not-started'?'NOT_STARTED':'UNKNOWN');
        assert(await panel.getByRole('button',{name:'读取类目候选',exact:true}).isDisabled());
        if(mode==='unknown')assert((await panel.textContent()).includes('尝试 3 / 返回 2'));
      }
      assert.strictEqual(requests.filter(r=>r.method==='POST').length,1);await finish();return;
    }
    await panel.locator('input[name="r1-category-choice"]').first().waitFor();
    assert.strictEqual(await panel.locator('input[name="r1-category-choice"]:checked').count(),0);
    assert.strictEqual(await panel.locator('input[name="r1-category-choice"]').count(),2);
    if(mode==='legacy'){assert((await panel.textContent()).includes('尝试 未知 / 返回 未知'));await finish();return;}
    if(mode==='labels'){
      assert((await panel.textContent()).includes('<img'));
      assert.strictEqual(await panel.locator('img').count(),0);
      assert.strictEqual(await page.evaluate(()=>window.fixtureInjected),undefined);await finish();return;
    }
    await panel.locator('input[name="r1-category-choice"]').first().check();
    await panel.getByRole('group',{name:'Single *',exact:true}).getByRole('radio').check();
    const multi=panel.getByRole('group',{name:'Multi *',exact:true}).getByRole('checkbox');
    await multi.nth(0).check();await multi.nth(1).check();
    assert(await page.locator('#r1Capture').isDisabled());
    await panel.getByRole('textbox',{name:'Text',exact:true}).fill('Explicit browser text');
    if(mode==='selection'){
      await panel.locator('input[name="r1-category-choice"]').nth(1).check();
      await panel.locator('input[name="r1-category-choice"]').first().check();
      assert.strictEqual(await panel.getByRole('textbox',{name:'Text',exact:true}).inputValue(),'');
      assert.strictEqual(await panel.locator('fieldset input:checked').count(),1);
      assert(await page.locator('#r1Capture').isDisabled());
      await page.locator('#factsEditTitle').fill('Unsaved changed title');
      assert.strictEqual(await panel.locator('input[name="r1-category-choice"]').count(),0);
      assert(await panel.getByRole('button',{name:'读取类目候选',exact:true}).isDisabled());
      assert(await page.locator('#approvalButton').isDisabled());await finish();return;
    }
    await page.locator('#r1Capture').click();
    if(mode==='lost-capture'){
      await page.waitForFunction(()=>document.querySelector('#round1Category [data-r1-status]')?.dataset.r1Status==='UNKNOWN');
      await page.reload();await panel.waitFor();await panel.locator('select').selectOption('MY');
      await panel.getByRole('button',{name:'准备完整首轮审核',exact:true}).waitFor();
      assert.strictEqual(requests.filter(r=>r.method==='POST').length,2);await finish();return;
    }
    await panel.getByRole('button',{name:'准备完整首轮审核',exact:true}).click();
    await panel.getByText('查看本次完整审核内容',{exact:true}).click();
    assert((await panel.locator('pre').textContent()).includes('Explicit browser text'));
    await page.screenshot({path:path.join(output,`prepared-${width}.png`),fullPage:true});
    // The current R1 layout places its explicit approval in the category panel.
    const approve=panel.getByRole('button',{name:'批准当前首轮事实并冻结快照'});
    assert(await approve.isEnabled());
    await approve.click();
    if(mode==='lost-approval'){
      await page.waitForFunction(()=>document.querySelector('#round1Category [data-r1-status]')?.dataset.r1Status==='UNKNOWN');
      assert.strictEqual(await panel.getByRole('button',{name:'批准当前首轮事实并冻结快照'}).count(),0);
      assert(await page.locator('#approvalButton').isDisabled());
      await page.reload();await panel.waitFor();await panel.locator('select').selectOption('MY');
    }
    await page.waitForFunction(()=>['FROZEN','UNKNOWN','BLOCKED'].includes(document.querySelector('#round1Category [data-r1-status]')?.dataset.r1Status));
    assert.strictEqual(await panel.locator('[data-r1-status]').getAttribute('data-r1-status'),'FROZEN',await panel.textContent());
    await page.locator('#tab-facts').click();
    assert((await page.locator('#factsEditRevision').textContent()).includes('8'));
    assert(await page.locator('#factsEditTitle').isDisabled());
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:path.join(output,`frozen-${width}.png`),fullPage:true});
    const posts=requests.filter(r=>r.method==='POST');
    assert.strictEqual(posts.filter(r=>r.url.endsWith('/options')).length,1);
    assert.strictEqual(posts.filter(r=>r.url.endsWith('/capture')).length,1);
    assert.strictEqual(posts.filter(r=>r.url.endsWith('/approve')).length,1);
    assert.strictEqual(posts.length,4);
    assert.strictEqual(responses.find(r=>r.url.endsWith('/options')).value.progress.completed,5);
    assert.strictEqual(responses.find(r=>r.url.endsWith('/capture')).value.progress.completed,2);
    await page.reload();await panel.waitFor();
    assert.strictEqual(await panel.locator('select').inputValue(),'');
    await panel.locator('select').selectOption('MY');
    await page.waitForFunction(()=>document.querySelector('#round1Category [data-r1-status]')?.dataset.r1Status==='FROZEN');
    assert.strictEqual(requests.filter(r=>r.method==='POST').length,4);
    await finish();
  }catch(error){
    try{await page.screenshot({path:path.join(output,'failure.png'),timeout:3000});}catch{}
    const finalDom=await page.locator('#round1Category').textContent();
    const localRecords=await page.evaluate(()=>localStorage.getItem('orbit-r1-category-operations-v1'));
    fs.writeFileSync(path.join(output,'BROWSER_RESULT.json'),JSON.stringify({passed:false,error:String(error),finalDom,localRecords,requests,responses,resources,errors,denied,consoleErrors},null,2));throw error;
  }finally{await context.close();await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
