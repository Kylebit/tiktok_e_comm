const {chromium}=require('C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..');
const server=http.createServer((req,res)=>{const p=path.join(root,'web',req.url==='/'?'task_workspace.html':req.url);res.setHeader('Content-Type',p.endsWith('.js')?'text/javascript':p.endsWith('.css')?'text/css':'text/html');res.end(fs.readFileSync(p));});

(async()=>{
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const browser=await chromium.launch({headless:true,executablePath:'C:/Users/Windows11/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe'});
  try{
    const page=await browser.newPage(),base='http://127.0.0.1:'+server.address().port;
    let listReads=0,detailReads=0;
    let task={task_id:'one',title:'合成上架任务',template:'publication',execution_state:'running',current_step:'facts',updated_at:'2026-09-23T10:00:00Z',steps:[{key:'facts',label:'准备商品资料',state:'running'}]};
    let events=[{event_type:'claimed',created_at:'2026-09-23T10:00:00Z'}];
    await page.route('**/api/orbit/**',route=>{
      const url=new URL(route.request().url());
      if(url.pathname.endsWith('/navigation'))return route.fulfill({json:{ok:true,navigation:[]}});
      if(url.pathname==='/api/orbit/operations-runtime')return route.fulfill({json:{environment:'stable',version:'test',worker_enabled:false,execution_mode:'web-only'}});
      if(url.pathname==='/api/orbit/tasks'){listReads++;return route.fulfill({json:{tasks:[task],executor:{connected:false}}});}
      if(url.pathname==='/api/orbit/tasks/one'){detailReads++;return route.fulfill({json:{task,events}});}
      throw Error('unexpected request: '+url.pathname);
    });
    await page.goto(base);
    await page.locator('#taskRows [data-detail="one"]').first().click();
    await page.waitForSelector('#detailDialog[open] .badge.running');
    const unchangedBadge=await page.locator('#detailBody .badge').first().elementHandle();
    await page.evaluate(()=>document.querySelector('#refreshTasks').click());
    await page.waitForFunction(()=>document.querySelector('#refreshTasks').disabled===false);
    assert.equal(detailReads,2,'unchanged open detail should still be checked');
    assert.equal(await unchangedBadge.evaluate(node=>node.isConnected),true,'unchanged detail should retain its DOM');

    const readsBeforeTimedPoll=listReads;
    task={...task,execution_state:'waiting_user',updated_at:'2026-09-23T10:01:00Z',required_action:{kind:'input',action_id:'action-1',label:'补充资料',reason:'请提供资料位置'},steps:[{key:'facts',label:'准备商品资料',state:'waiting_user'}]};
    events=[...events,{event_type:'user_action_required',created_at:'2026-09-23T10:01:00Z'}];
    await page.waitForFunction(()=>document.querySelector('#detailDialog[open] #inputForm')&&document.querySelector('#detailBody').textContent.includes('请提供资料位置'),null,{timeout:20000});
    assert.ok(listReads>readsBeforeTimedPoll,'15-second dashboard poll should run');
    assert.ok(detailReads>=3,'open detail should be read again');
    assert.equal(await page.locator('#countAttention').textContent(),'1');
    assert.match(await page.locator('#attentionList').textContent(),/补充资料/);

    await page.locator('#inputNote').fill('尚未提交的用户草稿');
    await page.locator('#inputNote').focus();
    const draftNode=await page.locator('#inputNote').elementHandle();
    await page.evaluate(()=>document.querySelector('#refreshTasks').click());
    await page.waitForFunction(()=>document.querySelector('#refreshTasks').disabled===false);
    assert.equal(await draftNode.evaluate(node=>node.isConnected&&document.activeElement===node),true,'unchanged detail should not redraw the draft field');
    assert.equal(await page.locator('#inputNote').inputValue(),'尚未提交的用户草稿');
    task={...task,updated_at:'2026-09-23T10:02:00Z',required_action:{...task.required_action,reason:'请提供更新后的资料位置'}};
    events=[...events,{event_type:'checkpoint_saved',created_at:'2026-09-23T10:02:00Z'}];
    await page.evaluate(()=>document.querySelector('#refreshTasks').click());
    await page.waitForFunction(()=>document.querySelector('#detailBody').textContent.includes('请提供更新后的资料位置'));
    assert.equal(await page.locator('#inputNote').inputValue(),'尚未提交的用户草稿');
    assert.equal(await draftNode.evaluate(node=>node.isConnected&&document.activeElement===node),true,'draft textarea and focus should survive detail update');

    await page.locator('[data-close="detailDialog"]').click();
    const readsAfterClose=detailReads,listsBeforeCloseRefresh=listReads;
    await page.locator('#refreshTasks').click();
    await page.waitForFunction(()=>document.querySelector('#refreshTasks').disabled===false);
    assert.ok(listReads>listsBeforeCloseRefresh);
    assert.equal(detailReads,readsAfterClose,'closed detail should not issue another detail GET');
    console.log('PASS timed detail poll updates running/attention state, keeps draft focus, and stops detail reads after close');
  }finally{await browser.close();server.close();}
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
