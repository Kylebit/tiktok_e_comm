// Closed browser DTO projection only. No task mutation or provider transport.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const root=path.resolve(__dirname,'../..'),out=process.argv[2];
if(!out||!process.env.ORBIT_BROWSER_EXECUTABLE)throw Error('Fixed browser and evidence directory required');
fs.mkdirSync(out,{recursive:true});
const release={environment:'stable',code_version:'a'.repeat(40),manifest_digest:'b'.repeat(64)};
const task=(id,template,extra={})=>({task_id:id,title:id,template,execution_state:'queued',
  scope:template==='profit'?{month:'2026-09',shops:['owned-shop']}:{offer_id:'3828811808',shops:['tiktok:LH_MY']},
  version:release,executor_connected:false,steps:[],...extra});
const items=[
  task('owned-publication','publication',{executor_connected:true,preparation_scope:'EXPLICIT_NEW_POST_PUBLICATION_ONLY'}),
  task('historical-publication','publication'),task('registered-profit','profit'),
  task('pending-delisting','delisting',{execution_state:'waiting_domain',pending_observation:{reason:'原提交结果待核。'}}),
  task('pending-publication','publication',{execution_state:'waiting_domain',executor_connected:true,
    preparation_scope:'EXPLICIT_NEW_POST_PUBLICATION_ONLY',pending_observation:{reason:'原提交结果待核。'}}),
  task('profit-unknown','profit',{execution_state:'failed',current_step:'coverage',
    profit_unknown_readback:{status:'UNKNOWN',session_readback:'NOT_VERIFIED',automatic_resume_allowed:false}}),
];
let runtime={environment:'stable',version:release.code_version,manifest_digest:release.manifest_digest,
  worker_enabled:false,capabilities:{publication:{connected:false},profit:{connected:false},delisting:{connected:false}},
  new_task_preparation:{scope:'EXPLICIT_NEW_POST_PUBLICATION_ONLY',running:true,state:'polling',historical_task_scan:false}};
let runtimeUnavailable=false;
const posts=[],errors=[],reads=[];
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
      if(req.method()!=='GET'){posts.push(url.pathname);return route.fulfill({status:409,json:{error:'Closed read-only browser case'}});}
      if(!url.pathname.startsWith('/api/'))return route.continue();
      reads.push(url.pathname);
      if(url.pathname==='/api/orbit/navigation')return route.fulfill({json:{ok:true,navigation:[]}});
      if(url.pathname==='/api/orbit/operations-runtime')return route.fulfill(runtimeUnavailable
        ?{status:503,json:{error:'Runtime unavailable'}}:{json:runtime});
      if(url.pathname==='/api/orbit/tasks')return route.fulfill({json:{tasks:items,release,
        executor:{connected:false,templates:[]},dispatcher:{state:'stopped',running:false}}});
      const selected=items.find(t=>url.pathname==='/api/orbit/tasks/'+t.task_id);
      return route.fulfill(selected?{json:{task:selected,events:[]}}:{status:404,json:{error:'No closed route'}});
    });
    await page.goto(base);
    await page.waitForFunction(()=>document.querySelector('#connection').textContent.includes('新建上架任务已接入'));
    assert.match(await page.locator('#connection').innerText(),/商品下架、利润统计任务当前仅登记/);
    assert.match(await page.locator('#connection').innerText(),/历史任务不会自动接管/);
    const row=id=>page.locator('#taskRows tr').filter({has:page.locator(`[data-detail="${id}"]`)});
    assert.match(await row('owned-publication').innerText(),/待执行/);
    assert.match(await row('historical-publication').innerText(),/已登记，未接续/);
    assert.match(await row('registered-profit').innerText(),/已登记，未接续/);
    await page.locator('#createTask').click();
    assert.match(await page.locator('#creationStatus').innerText(),/仅本次新建上架任务/);
    await page.locator('#newType').selectOption('profit');
    assert.match(await page.locator('#creationStatus').innerText(),/利润统计任务仅登记/);
    await page.locator('#newType').selectOption('delisting');
    assert.match(await page.locator('#creationStatus').innerText(),/商品下架任务仅登记/);
    await page.locator('[data-close="createDialog"]').first().click();
    const open=async id=>{const control=row(id).getByRole('button',{name:'详情',exact:true});
      assert.equal(await control.count(),1);assert.equal(await control.isVisible(),true);await control.click();
      await page.waitForFunction(id=>document.querySelector('#detailTitle').textContent===id,id);};
    const close=()=>page.locator('[data-close="detailDialog"]').click();
    await open('pending-publication');
    assert.match(await page.locator('#detailBody').innerText(),/当前任务已接入只读观测/);
    assert.equal(await page.locator('#retryTask,#cancelTask').count(),0);
    await close();await open('pending-delisting');
    assert.match(await page.locator('#detailBody').innerText(),/未确认该任务的自动回读接续/);
    assert.doesNotMatch(await page.locator('#detailBody').innerText(),/工作台会自动读取|当前任务已接入只读观测/);
    assert.equal(await page.locator('#retryTask,#cancelTask').count(),0);
    await close();await open('profit-unknown');
    assert.match(await page.locator('#detailBody').innerText(),/未经核实|待核对/);
    assert.equal(await page.locator('#retryTask,#cancelTask').count(),0);
    await close();
    for(const width of [1440,390]){
      await page.setViewportSize({width,height:width===390?844:1000});
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      await page.screenshot({path:path.join(out,`scoped-task-status-${width}.png`),fullPage:true});
    }
    runtime.new_task_preparation.state='source_mismatch';
    items[0].executor_connected=false;items[4].executor_connected=false;
    await page.locator('#refreshTasks').click();
    await page.waitForFunction(()=>document.querySelector('#connection').textContent.includes('后台执行尚未启动'));
    await open('pending-publication');
    assert.match(await page.locator('#detailBody').innerText(),/未确认该任务的自动回读接续/);await close();
    runtime.new_task_preparation.state='polling';runtime.version='c'.repeat(40);
    await page.locator('#refreshTasks').click();
    await page.locator('#createTask').click();
    await page.waitForFunction(()=>document.querySelector('#creationStatus').textContent.includes('能力尚未核实'));
    assert.doesNotMatch(await page.locator('#creationStatus').innerText(),/交由准备执行器接续/);
    assert.match(await row('owned-publication').innerText(),/已登记，执行状态待核实/);
    assert.match(await row('registered-profit').innerText(),/已登记，执行状态待核实/);
    await page.locator('[data-close="createDialog"]').first().click();
    runtime.version=release.code_version;
    await page.locator('#refreshTasks').click();
    await page.waitForFunction(()=>!document.querySelector('#refreshTasks').disabled
      && document.querySelector('#connection').textContent.includes('新建上架任务已接入'));
    await page.locator('#createTask').click();
    assert.match(await page.locator('#creationStatus').innerText(),/仅本次新建上架任务/);
    assert.match(await row('registered-profit').innerText(),/已登记，未接续/);
    await page.locator('[data-close="createDialog"]').first().click();
    runtimeUnavailable=true;
    await page.locator('#refreshTasks').click();
    await page.waitForFunction(()=>!document.querySelector('#refreshTasks').disabled);
    await page.locator('#createTask').click();
    assert.match(await page.locator('#creationStatus').innerText(),/能力尚未核实/);
    assert.match(await row('owned-publication').innerText(),/已登记，执行状态待核实/);
    assert.match(await row('registered-profit').innerText(),/已登记，执行状态待核实/);
    assert.deepEqual(posts,[]);assert.deepEqual(errors,[]);
    assert.ok(reads.includes('/api/orbit/operations-runtime'));
    console.log(JSON.stringify({synthetic:true,widths:[1440,390],posts,errors,
      journeys:['owned-publication','unowned-history','nonpublication-registration','exact-observer',
        'no-observer','profit-unknown','paused-worker','foreign-runtime','runtime-unavailable']}));
  }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
