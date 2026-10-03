const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const {chromium}=require(path.join(process.env.ORBIT_NODE_MODULES || 'C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules','playwright'));
const out=process.argv[2], preview=JSON.parse(fs.readFileSync(path.join(out,process.argv[3]||'preview.json'),'utf8'));
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.ORBIT_BROWSER_EXECUTABLE?{executablePath:process.env.ORBIT_BROWSER_EXECUTABLE}:{channel:'chrome'})}),origin=new URL(preview.url).origin;
 const record={preview,views:[],checks:[],requests:[],external:[],errors:[],resources:[]};
 const resources=[];
 try {
  for(const [width,height] of [[1440,900],[390,844]]) {
   const context=await browser.newContext({viewport:{width,height},permissions:['clipboard-read','clipboard-write']});
   await context.route('**/*',route=>{if(new URL(route.request().url()).origin!==origin){record.external.push(route.request().url());return route.abort();}return route.continue();});
   const page=await context.newPage();page.on('pageerror',e=>record.errors.push(String(e)));page.on('request',r=>record.requests.push({method:r.method(),url:r.url()}));
   page.on('response',response=>{if(response.status()===200 && /\/(?:\?view=|static\/orbit_product\.|static\/operations\.css)/.test(response.url())) resources.push(response.body().then(body=>record.resources.push({url:response.url(),bytes:body.length,sha256:require('crypto').createHash('sha256').update(body).digest('hex')})));});
   await page.goto(origin+'/knowledge');await page.locator('.knowledge-row').first().waitFor();
   const health=await page.evaluate(()=>fetch('/api/health').then(r=>r.json()));
   const catalog=await page.evaluate(()=>fetch('/api/orbit/navigation').then(r=>r.json()));
   const entries=[...catalog.tools,...catalog.capabilities.filter(x=>x.region==='knowledge')];
   assert.equal(await page.locator('.knowledge-row').count(),entries.length);
   assert.deepEqual(await page.locator('.knowledge-row strong').allTextContents(),entries.map(x=>x.title));
   record.views.push({width,height,health,visible:await page.locator('.knowledge-row').evaluateAll(nodes=>nodes.filter(n=>{const r=n.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight}).map(n=>n.dataset.knowledgeKey)),overflow:await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)});
   assert.equal(record.views.at(-1).overflow,false);
   await page.screenshot({path:path.join(out,`after-${width}.png`)});
   // Every retained method and status is the backend value, never a new front-end readiness verdict.
   for(const item of entries){
    const key=(catalog.tools.includes(item)?'tool:':'capability:')+item.id;
    const row=page.locator(`[data-knowledge-key="${key}"]`);await row.click();
    assert.equal(await page.locator('#knowledgeDetailTitle').innerText(),item.title);
    assert.equal(await page.locator('.knowledge-condition').innerText(),item.status);
    if(item.method)assert.equal(await page.locator('#knowledgeMethod').inputValue(),item.method);
    if(width===390)await page.getByRole('button',{name:'返回工具列表',exact:true}).click();
   }
   await page.locator('#entrySearch').fill('商品事实');
   const row=page.locator('[data-knowledge-key="tool:prepare-product-publication"]');
   assert(await row.isVisible());await row.focus();await page.keyboard.press('Enter');
   assert.equal(await page.locator('#knowledgeDetailTitle').evaluate(n=>n===document.activeElement),true);
   assert.equal(await page.locator('#knowledgeDetail').getAttribute('data-tool'),'prepare-product-publication');
   if(width===390){await page.getByRole('button',{name:'返回工具列表',exact:true}).click();assert.equal(await row.evaluate(n=>n===document.activeElement),true);}
   await page.locator('#entrySearch').fill('');await page.locator('[data-knowledge-group="独立工具"]').click();assert.equal(await page.locator('.knowledge-row').count(),catalog.tools.filter(x=>x.stage==='PORTABLE_OFFLINE_ENTRY').length);
   await page.locator('[data-knowledge-key="tool:tikhub"]').click();await page.locator('.knowledge-technical summary').click();await page.locator('[data-copy-target="knowledgeMethod"]').click();
   // Windows clipboard APIs return CRLF; normalize only the OS newline conversion.
   assert.equal((await page.evaluate(()=>navigator.clipboard.readText())).replace(/\r\n/g,'\n'),catalog.tools.find(x=>x.id==='tikhub').method.replace(/\r\n/g,'\n'));
   assert.match(await page.locator('#knowledgeCopyState').innerText(),/已复制/);
   if(width===390)await page.getByRole('button',{name:'返回工具列表',exact:true}).click();
   await page.locator('[data-knowledge-group="知识快照"]').click();await page.locator('.knowledge-row').click();
   assert.match(await page.locator('#knowledgeMethod').inputValue(),/expected-version sha256:PIN/);
   await page.screenshot({path:path.join(out,`knowledge-detail-${width}.png`)});
   await page.evaluate(()=>{navigator.clipboard.writeText=async()=>{throw new Error('fixture clipboard denied');};});
   await page.locator('.knowledge-technical summary').click();await page.locator('[data-copy-target="knowledgeMethod"]').click();assert.match(await page.locator('#knowledgeCopyState').innerText(),/Ctrl\+C/);
   const selection=await page.locator('#knowledgeMethod').evaluate(n=>({start:n.selectionStart,end:n.selectionEnd,length:n.value.length,focused:n===document.activeElement}));assert.equal(selection.start,0);assert.equal(selection.end,selection.length);assert.equal(selection.focused,true);
   if(width===390)await page.getByRole('button',{name:'返回工具列表',exact:true}).click();
   await page.locator('[data-knowledge-group=""]').click();
   const blockedEntry=entries.find(x=>x.missing_files?.length);
   if(blockedEntry){await page.locator('#knowledgeStatus').selectOption(blockedEntry.status);await page.locator(`[data-knowledge-key="tool:${blockedEntry.id}"]`).click();assert.match(await page.locator('.knowledge-blocker').innerText(),/缺少/);}
   else {assert.equal(entries.filter(x=>x.status==='缺少运行依赖').length,0);await page.locator('#knowledgeStatus').selectOption(entries[0].status);assert(await page.locator('.knowledge-row').count()>0);}
   if(width===390&&blockedEntry)await page.getByRole('button',{name:'返回工具列表',exact:true}).click();
   await page.locator('#entrySearch').fill('无此工具 fixture 123');assert.equal(await page.locator('.knowledge-row').count(),0);assert(await page.locator('#knowledgeReset').isVisible());await page.locator('#knowledgeReset').click();assert.equal(await page.locator('.knowledge-row').count(),entries.length);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   record.checks.push({width,scenarios:['all-items-method-and-status-parity','product-skill-search','keyboard-selection-and-return','portable-group','clipboard-success','knowledge-pin-method','clipboard-failure-manual-selection','missing-dependency-status-filter','empty-reset','no-overflow']});
   assert.equal(await page.locator('#orbitCoreNav a[href="/product-workspace"]').count(),1);
   // Faults are injected in the browser; the actual source files remain unchanged.
   const unverified=structuredClone(catalog);unverified.tool_catalog={state:'UNVERIFIED'};unverified.tools.forEach(x=>{x.status='工具包未核验';x.stage='UNVERIFIED';x.method='';});
   await context.route('**/api/orbit/navigation',route=>route.fulfill({contentType:'application/json',body:JSON.stringify(unverified)}));
   await page.goto(origin+'/knowledge');await page.locator('.knowledge-notice.warning').waitFor();await page.locator('.knowledge-row').first().click();assert.equal(await page.locator('#knowledgeMethod').count(),0);assert.match(await page.locator('#knowledgeDetail').innerText(),/暂无已核验方法/);
   await context.unroute('**/api/orbit/navigation');await context.route('**/api/orbit/navigation',route=>route.fulfill({status:503,contentType:'application/json',body:'{}'}));
   await page.goto(origin+'/knowledge');await page.getByRole('alert').waitFor();assert.match(await page.locator('#entryError').innerText(),/工具目录读取失败/);assert.equal(await page.locator('.knowledge-row').count(),0);
   assert(await page.locator('#entryError').evaluate(n=>{const r=n.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight;}));
   await page.screenshot({path:path.join(out,`directory-error-${width}.png`)});
   await context.unroute('**/api/orbit/navigation');await context.route('**/api/orbit/navigation',route=>route.fulfill({contentType:'application/json',body:JSON.stringify({capabilities:[],regions:[]})}));await page.reload();await page.getByRole('alert').waitFor();
   record.checks.push({width,scenarios:['five-core-navigation','unverified-source-no-method','directory-http-error','incomplete-payload-error']});
   await Promise.all(resources);await context.close();
  }
  assert.deepEqual(record.errors,[]);assert.deepEqual(record.external,[]);assert.equal(record.requests.filter(x=>x.method!=='GET').length,0);record.status='passed';
 }catch(error){record.status='failed';record.error=String(error);throw error;}finally{fs.writeFileSync(path.join(out,'knowledge-browser-result.json'),JSON.stringify(record,null,2));await browser.close();}
 console.log(JSON.stringify({status:record.status,views:record.views.map(v=>({width:v.width,visible:v.visible,health:v.health.state})),checks:record.checks.length}));
})().catch(e=>{console.error(e);process.exitCode=1});
