// Closed DTO browser projection. No task POST, financial read or provider.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const root=path.resolve(__dirname,'../..'),out=process.argv[2];
if(!out||!process.env.ORBIT_BROWSER_EXECUTABLE)throw Error('Fixed browser and evidence directory required');
fs.mkdirSync(out,{recursive:true});
const release={environment:'stable',code_version:'a'.repeat(40),manifest_digest:'b'.repeat(64)};
const profitScope='EXPLICIT_NEW_POST_PROFIT_READONLY';
const task=(id,extra={})=>({task_id:id,title:id,template:'profit',execution_state:'queued',
  scope:{month:'2026-08',shops:['owned-shop']},version:release,executor_connected:false,steps:[],...extra});
const items=[task('owned-profit',{executor_connected:true,preparation_scope:profitScope}),
  task('reused-old-month'),task('unknown-profit',{execution_state:'failed',current_step:'coverage',
    executor_connected:true,preparation_scope:profitScope,profit_unknown_readback:{status:'UNKNOWN',
      session_readback:'NOT_VERIFIED',automatic_resume_allowed:false}}),
  task('missing-inputs',{execution_state:'waiting_user',current_step:'coverage',executor_connected:true,
    preparation_scope:profitScope,required_action:{kind:'input',label:'补充利润资料',reason:'缺少完整月份结算导出与实际广告来源'}}),
  task('completed-profit',{execution_state:'completed',result_url:'/artifacts/owned-profit/index.html'})];
let runtime={environment:release.environment,version:release.code_version,manifest_digest:release.manifest_digest,
  capabilities:{profit:{connected:true,scope:profitScope},delisting:{connected:false}},
  new_task_preparation:{scope:'EXPLICIT_NEW_POST_PUBLICATION_ONLY',running:true,state:'polling',
    profit_scope:profitScope,profit_executor_connected:true,historical_task_scan:false}};
let unavailable=false;
const posts=[],errors=[];
const server=http.createServer((req,res)=>{
  const pathname=new URL(req.url,'http://127.0.0.1').pathname;
  const file=path.resolve(root,'web',pathname==='/'?'task_workspace.html':'.'+pathname);
  if(!file.startsWith(path.join(root,'web')+path.sep)||!fs.existsSync(file)){res.writeHead(404);res.end();return;}
  res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html');
  res.end(fs.readFileSync(file));
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const base='http://127.0.0.1:'+server.address().port;
  const browser=await chromium.launch({headless:true,executablePath:process.env.ORBIT_BROWSER_EXECUTABLE});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    page.on('pageerror',error=>errors.push(String(error)));
    await page.route('**/*',route=>{
      const req=route.request(),url=new URL(req.url());
      if(url.origin!==base)return route.abort();
      if(req.method()!=='GET'){posts.push(url.pathname);return route.fulfill({status:409,json:{error:'Closed read-only case'}});}
      if(!url.pathname.startsWith('/api/'))return route.continue();
      if(url.pathname==='/api/orbit/navigation')return route.fulfill({json:{ok:true,navigation:[]}});
      if(url.pathname==='/api/orbit/operations-runtime')return route.fulfill(unavailable
        ?{status:503,json:{error:'Runtime unavailable'}}:{json:runtime});
      if(url.pathname==='/api/orbit/tasks')return route.fulfill({json:{tasks:items,release,
        executor:{connected:false,templates:[]},dispatcher:{state:'stopped',running:false}}});
      const selected=items.find(t=>url.pathname==='/api/orbit/tasks/'+t.task_id);
      return route.fulfill(selected?{json:{task:selected,events:[]}}:{status:404,json:{error:'No closed route'}});
    });
    const row=id=>page.locator('#taskRows tr').filter({has:page.locator(`[data-detail="${id}"]`)});
    const refresh=async()=>{await page.locator('#refreshTasks').click();
      await page.waitForFunction(()=>!document.querySelector('#refreshTasks').disabled);};
    const open=async id=>{const control=row(id).getByRole('button',{name:'详情',exact:true});
      assert.equal(await control.count(),1);assert.equal(await control.isVisible(),true);await control.click();
      await page.waitForFunction(id=>document.querySelector('#detailTitle').textContent===id,id);};
    const close=()=>page.locator('[data-close="detailDialog"]').click();
    await page.goto(base);
    await page.waitForFunction(()=>document.querySelector('#connection').textContent.includes('新建利润任务已接入只读统计'));
    assert.match(await row('owned-profit').innerText(),/待执行/);
    assert.match(await row('reused-old-month').innerText(),/已登记，未接续/);
    await page.locator('#createTask').click();await page.locator('#newType').selectOption('profit');
    assert.match(await page.locator('#creationStatus').innerText(),/仅本次新建利润任务自动接续只读统计和本地报告/);
    assert.match(await page.locator('#creationStatus').innerText(),/同月旧任务复用|UNKNOWN 不自动重跑/);
    assert.doesNotMatch(await page.locator('#creationStatus').innerText(),/商业审核/);
    await page.locator('[data-close="createDialog"]').first().click();
    await open('unknown-profit');
    assert.match(await page.locator('#detailBody').innerText(),/未经核实|待核对/);
    assert.doesNotMatch(await page.locator('#detailBody').innerText(),/当前任务已接入只读观测/);
    assert.equal(await page.locator('#retryTask,#cancelTask').count(),0);await close();
    await open('missing-inputs');
    assert.match(await page.locator('#detailBody').innerText(),/缺少完整月份结算导出与实际广告来源/);
    assert.equal(await page.locator('#inputForm').count(),1);await close();
    await open('completed-profit');
    assert.equal(await page.locator('#detailBody a[href="/artifacts/owned-profit/index.html"]').count(),1);await close();
    for(const field of ['profit_executor_connected','running']){
      runtime.new_task_preparation[field]=false;await refresh();
      assert.match(await row('owned-profit').innerText(),/已登记，未接续/);
      await page.locator('#createTask').click();await page.locator('#newType').selectOption('profit');
      assert.match(await page.locator('#creationStatus').innerText(),/未确认只读后台接续/);
      await page.locator('[data-close="createDialog"]').first().click();
      runtime.new_task_preparation[field]=true;await refresh();
      assert.match(await row('owned-profit').innerText(),/待执行/);
    }
    runtime.version='c'.repeat(40);await refresh();
    assert.match(await row('owned-profit').innerText(),/执行状态待核实/);
    runtime.version=release.code_version;await refresh();
    assert.match(await row('owned-profit').innerText(),/待执行/);
    unavailable=true;await refresh();
    assert.match(await row('owned-profit').innerText(),/执行状态待核实/);
    await page.locator('#createTask').click();await page.locator('#newType').selectOption('profit');
    assert.match(await page.locator('#creationStatus').innerText(),/能力尚未核实/);
    await page.locator('[data-close="createDialog"]').first().click();
    for(const width of [1440,390]){
      await page.setViewportSize({width,height:width===390?844:1000});
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      await page.screenshot({path:path.join(out,`explicit-profit-${width}.png`),fullPage:true});
    }
    assert.deepEqual(posts,[]);assert.deepEqual(errors,[]);
    console.log(JSON.stringify({synthetic:true,widths:[1440,390],posts,errors,
      journeys:['new-owned-profit','old-month-reuse','unknown-no-observer','missing-inputs',
        'original-result-link','missing-binding','stopped-worker','foreign-runtime','runtime-503']}));
  }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
