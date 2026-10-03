const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(path.join(process.env.ORBIT_NODE_MODULES,'playwright'));
const [origin,out]=process.argv.slice(2);
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.ORBIT_BROWSER_EXECUTABLE?{executablePath:process.env.ORBIT_BROWSER_EXECUTABLE}:{channel:'chrome'})});
 const record={status:'running',external:[],errors:[],writes:[],views:[],resources:[]};
 const resourceReads=[];
 const openExample=async page=>{const details=page.locator('.knowledge-example');if(!(await details.evaluate(node=>node.open)))await details.locator('summary').click();};
 try {
  for(const width of [1440,390]) {
   const context=await browser.newContext({viewport:{width,height:900},permissions:['clipboard-read','clipboard-write']});
   await context.route('**/*',route=>{const r=route.request();if(new URL(r.url()).origin!==origin){record.external.push(r.url());return route.abort();}if(r.method()!=='GET'){record.writes.push(r.method());return route.abort();}return route.continue();});
   const page=await context.newPage();page.setDefaultTimeout(5000);page.on('pageerror',e=>record.errors.push(String(e)));
   let expectedNetworkFailure=false;
   page.on('console',m=>{if(m.type()==='error'&&!expectedNetworkFailure)record.errors.push(m.text());});
   page.on('response',r=>{if(r.status()===200&&/\/knowledge$|\/static\/knowledge_(directory\.js|guides\.(json|css))$/.test(r.url()))resourceReads.push(r.body().then(body=>record.resources.push({url:r.url(),sha256:require('node:crypto').createHash('sha256').update(body).digest('hex')})));});
   await page.goto(origin+'/knowledge');await page.locator('.knowledge-row').first().waitFor();
   const catalog=await page.evaluate(()=>fetch('/api/orbit/navigation').then(r=>r.json()));
   const entries=[...catalog.tools,...catalog.capabilities.filter(x=>x.region==='knowledge')];
   const groupOf=x=>x.stage==='PORTABLE_REVIEWED_SNAPSHOT_ENTRY'?'知识快照':x.stage==='PORTABLE_OFFLINE_ENTRY'?'独立工具':x.stage==='WORKFLOW_RUNTIME_REQUIRED'?'流程 Skill':'其他集成';
   assert.equal(await page.locator('.knowledge-row').count(),entries.length);
   assert.deepEqual(await page.locator('.knowledge-row strong').allTextContents(),entries.map(x=>x.title));
   assert.deepEqual(await page.locator('#knowledgeGroups button').evaluateAll(xs=>xs.map(x=>x.dataset.knowledgeGroup)),['',...new Set(entries.map(groupOf))]);
   assert.deepEqual(await page.locator('#orbitCoreNav a').evaluateAll(xs=>xs.map(x=>x.getAttribute('href'))),catalog.navigation.filter(x=>x.level==='primary').map(x=>x.href));
   await page.locator('[data-knowledge-key="tool:prepare-product-publication"]').click();
   await page.locator('.knowledge-guide summary').waitFor(); // Red on original one-line cards.
   assert.equal(await page.locator('.knowledge-guide').getAttribute('open'),null);
   await page.screenshot({path:path.join(out,`summary-${width}.png`)});
   for(const item of entries) {
    if(width===390)await page.getByRole('button',{name:'返回工具列表',exact:true}).click();
    await page.locator(`[data-knowledge-key="tool:${item.id}"]`).click();
    assert.equal(await page.locator('.knowledge-condition').innerText(),item.status);
    await page.locator('.knowledge-guide summary').click();
    for(const label of ['适用场景','需要提供','主要产出','依赖与授权','使用边界'])assert(await page.getByRole('heading',{name:label,exact:true}).isVisible());
    await openExample(page);
    const example=await page.locator('#knowledgeExample').inputValue();assert(example.length>60);
    await page.locator('[data-copy-target="knowledgeExample"]').click();
    assert.equal((await page.evaluate(()=>navigator.clipboard.readText())).replace(/\r\n/g,'\n'),example.replace(/\r\n/g,'\n'));
    assert.match(await page.locator('#knowledgeExampleCopyState').innerText(),/已复制/);
    await page.locator('.knowledge-technical summary').click();
    assert.equal(await page.locator('#knowledgeMethod').inputValue(),item.method);
    await page.locator('.knowledge-source > summary').click();assert(await page.locator('.knowledge-reference').count()>0);
   }
   if(width===390)await page.getByRole('button',{name:'返回工具列表',exact:true}).click();
   await page.locator('[data-knowledge-key="tool:manage-profit-settlement"]').click();
   await page.locator('.knowledge-guide summary').click();await openExample(page);
   assert.match(await page.locator('.knowledge-guide').innerText(),/不代表.*恢复/);
   await page.evaluate(()=>window.scrollTo(0,0));
   await page.screenshot({path:path.join(out,`profit-detail-${width}.png`),fullPage:true});
   await page.evaluate(()=>{navigator.clipboard.writeText=async()=>{throw Error('fixture denied');};});
   await page.locator('[data-copy-target="knowledgeExample"]').click();
   assert.match(await page.locator('#knowledgeExampleCopyState').innerText(),/Ctrl\+C/);
   assert.equal(await page.locator('#knowledgeExample').evaluate(n=>n.selectionStart===0&&n.selectionEnd===n.value.length),true);
   if(width===390)await page.getByRole('button',{name:'返回工具列表',exact:true}).click();
   await page.locator('#entrySearch').fill('过期快照');assert.equal(await page.locator('.knowledge-row').count(),1);
   await page.locator('#entrySearch').fill('');await page.locator('[data-knowledge-group="独立工具"]').click();assert.equal(await page.locator('.knowledge-row').count(),entries.filter(x=>groupOf(x)==='独立工具').length);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   const unverified=structuredClone(catalog);unverified.tool_catalog={state:'UNVERIFIED'};unverified.tools.forEach(x=>{x.stage='UNVERIFIED';x.method='';});
   await context.route('**/api/orbit/navigation',route=>route.fulfill({contentType:'application/json',body:JSON.stringify(unverified)}));
   await page.reload();await page.locator('.knowledge-notice.warning').waitFor();assert.equal(await page.locator('#knowledgeExample').count(),0);
   await context.unroute('**/api/orbit/navigation');
   expectedNetworkFailure=true;
   await context.route('**/static/knowledge_guides.json',route=>route.fulfill({status:503,body:''}));
   await page.reload();await page.locator('.knowledge-row').first().waitFor();await page.locator('#knowledgeGuideNotice').waitFor();
   assert.match(await page.locator('#knowledgeGuideNotice').innerText(),/介绍暂时不可用/);assert.equal(await page.locator('.knowledge-row').count(),entries.length);
   record.views.push({width,entryCount:entries.length,groups:[...new Set(entries.map(groupOf))],checks:'all guide sections; copy success/fallback; original method/status parity; search; unverified; missing guide; no overflow'});
   await Promise.all(resourceReads);
   await context.close();
  }
  assert.deepEqual(record.external,[]);assert.deepEqual(record.errors,[]);assert.deepEqual(record.writes,[]);record.status='passed';
 }catch(error){record.status='failed';record.error=String(error);throw error;}finally{fs.writeFileSync(path.join(out,'knowledge-guides-result.json'),JSON.stringify(record,null,2));await browser.close();}
 console.log(JSON.stringify(record));
})().catch(e=>{console.error(e);process.exitCode=1});
