// Real browser against a read-only snapshot of the formal task API.
// Static files come from this isolated checkout. This test never forwards POST.
const {chromium}=require('C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..'),out=process.argv[2];
if(!out)throw Error('Evidence directory required');
fs.mkdirSync(out,{recursive:true});
const official='http://127.0.0.1:49289';
function getJson(route){return new Promise((resolve,reject)=>http.get(official+route,res=>{let text='';res.setEncoding('utf8');res.on('data',chunk=>text+=chunk);res.on('end',()=>{try{if(res.statusCode!==200)throw Error(route+' HTTP '+res.statusCode);resolve(JSON.parse(text));}catch(e){reject(e);}});}).on('error',reject));}
const server=http.createServer((req,res)=>{
  if(req.method!=='GET'){res.writeHead(405);return res.end();}
  const file=req.url==='/'?'/task_workspace.html':req.url;
  const p=path.join(root,'web',file);
  if(!p.startsWith(path.join(root,'web'))||!fs.existsSync(p)){res.writeHead(404);return res.end();}
  res.setHeader('Content-Type',p.endsWith('.js')?'text/javascript':p.endsWith('.css')?'text/css':'text/html');res.end(fs.readFileSync(p));
});
(async()=>{
  const health=await getJson('/api/health'),dashboard=await getJson('/api/orbit/tasks'),runtime=await getJson('/api/orbit/operations-runtime');
  assert.equal(health.state,'READY');assert.equal(health.identity_only,true);assert.equal(runtime.execution_mode,'web-only');assert.equal(runtime.worker_enabled,false);
  const operations=dashboard.domain_operations||[];
  const offerTask=dashboard.tasks.find(t=>t.template==='publication'&&t.scope?.offer_id==='3956742887');
  assert.ok(offerTask,'Offer 3956742887 task absent from formal snapshot');
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const base='http://127.0.0.1:'+server.address().port;
  const browser=await chromium.launch({headless:true,executablePath:'C:/Users/Windows11/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe'});
  const posts=[],errors=[];
  try{
    const page=await browser.newPage();page.on('pageerror',e=>errors.push(String(e)));
    await page.route('**/*',async route=>{
      const req=route.request(),url=new URL(req.url());
      if(url.origin!==base)return route.abort();
      if(req.method()!=='GET'){posts.push(url.pathname);return route.fulfill({status:405,json:{error:'read only'}});}
      if(url.pathname==='/api/orbit/navigation')return route.fulfill({json:{ok:true,navigation:[]}});
      if(url.pathname==='/api/orbit/operations-runtime')return route.fulfill({json:runtime});
      if(url.pathname==='/api/orbit/tasks')return route.fulfill({json:dashboard});
      if(url.pathname.startsWith('/api/orbit/tasks/'))return route.fulfill({json:await getJson(url.pathname)});
      return route.continue();
    });
    await page.goto(base);await page.waitForFunction(()=>document.querySelector('#countAll')?.textContent!=='—');
    await page.waitForSelector('#pendingChecks', {state:'attached'});
    assert.equal(await page.locator('#pendingChecks').evaluate(element=>element.open),false);
    assert.equal(await page.locator('#pendingChecksList').isVisible(),false);
    await page.locator('#pendingChecks > summary').click();
    await page.waitForSelector('#pendingChecksList');
    const queue=await page.locator('#pendingChecksList').innerText();
    for(const operation of operations)assert.ok(queue.includes(operation.operation_id),'operation not visible: '+operation.operation_id);
    const linkedOperation=operations.find(op=>op.owner_task_id===offerTask.task_id);
    assert.ok(linkedOperation,'Offer task operation not found');
    const linkedCard=page.locator('#pendingChecksList .pending-group').first().locator('.pending-item').filter({hasText:linkedOperation.operation_id});
    const linkedText=await linkedCard.innerText();
    assert.match(linkedText,/关联任务：/);
    assert.ok(linkedText.includes('当前负责人：'+(offerTask.owner||offerTask.worker||'未登记')));
    assert.ok(!linkedText.includes('当前负责人：'+offerTask.title));
    if(offerTask.last_executor)assert.ok(linkedText.includes('上次执行者：'+offerTask.last_executor+'（历史）'));
    const historical=dashboard.tasks.find(t=>t.template==='profit'&&t.external_task?.owner);
    if(historical){
      const gapCard=page.locator('#pendingChecksList .pending-group').last().locator('.pending-item').filter({hasText:historical.task_id});
      const gapText=await gapCard.innerText();
      assert.ok(gapText.includes('当前负责人：'+(historical.owner||historical.worker||'未登记')));
      assert.ok(gapText.includes('历史观测来源：'+historical.external_task.owner+'（旧记录）'));
    }
    assert.equal(await page.locator('#pendingChecksCount').innerText()>=operations.length,true);
    assert.equal(await page.locator('#countAttention').innerText(),'0');
    assert.equal(Number(await page.locator('#countCancelled').innerText()),dashboard.tasks.filter(t=>t.execution_state==='cancelled').length);
    await page.locator('[data-detail="'+offerTask.task_id+'"]').last().click();
    await page.waitForFunction(()=>document.querySelector('#detailBody')?.textContent.includes('终审候选'));
    assert.equal(await page.locator('#detailBody a[href="/product-workspace?offer_id=3956742887&round=final"]').count(),1);
    assert.equal(await page.locator('#detailBody button:has-text("批准")').count(),0);
    await page.locator('[data-close="detailDialog"]').click();
    await page.setViewportSize({width:1440,height:950});await page.screenshot({path:path.join(out,'desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'mobile.png'),fullPage:true});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    assert.deepEqual(posts,[]);assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(out,'RESULT.json'),JSON.stringify({as_of:new Date().toISOString(),formal_commit:health.commit||health.git_commit||health.commit_hash||null,operation_count:operations.length,task_count:dashboard.tasks.length,offer_task_id:offerTask.task_id,task_state:offerTask.execution_state,worker_enabled:runtime.worker_enabled,posts,errors},null,2));
    console.log('PASS live readonly task-home browser: '+operations.length+' operations, '+dashboard.tasks.length+' tasks');
  }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
