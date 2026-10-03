// Synthetic Chromium regression for stale drafts, release changes, and late mutation responses.
const {chromium}=require('C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),http=require('node:http'),path=require('node:path');
const root=path.resolve(__dirname,'../..');
const server=http.createServer((req,res)=>{const file=path.join(root,'web',req.url==='/'?'task_workspace.html':req.url);res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html');res.end(fs.readFileSync(file));});
const version={channel:'synthetic',version:'v1'};
const task=(id,extra={})=>({task_id:id,title:id,template:'publication',execution_state:'waiting_user',version,steps:[],updated_at:'2026-09-23T10:00:00Z',required_action:{kind:'input',action_id:'action-1',label:'补充资料',reason:'旧动作'},...extra});

(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true,executablePath:'C:/Users/Windows11/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe'});
  try{
    const page=await browser.newPage(),base='http://127.0.0.1:'+server.address().port;
    let release=version,items={A:task('A'),B:task('B',{execution_state:'failed',required_action:null,executor_connected:true})};
    const posts=[],pending=new Map(),errors=[];
    page.on('pageerror',error=>errors.push(String(error)));
    await page.route('**/api/orbit/**',async route=>{
      const request=route.request(),url=new URL(request.url()),parts=url.pathname.split('/').filter(Boolean),id=parts[3];
      if(url.pathname.endsWith('/navigation'))return route.fulfill({json:{ok:true,navigation:[]}});
      if(url.pathname==='/api/orbit/operations-runtime')return route.fulfill({json:{environment:'stable',version:'test',worker_enabled:false,execution_mode:'web-only'}});
      if(request.method()==='POST'){
        posts.push({id,path:url.pathname});
        return new Promise(resolve=>pending.set(id,()=>{pending.delete(id);resolve(route.fulfill({status:409,json:{error:'synthetic '+id+' failure'}}));}));
      }
      if(id)return route.fulfill({json:{task:items[id],events:[]}});
      return route.fulfill({json:{tasks:Object.values(items),release,executor:{connected:true}}});
    });
    const refresh=async()=>{await page.evaluate(()=>document.querySelector('#refreshTasks').click());await page.waitForFunction(()=>!document.querySelector('#refreshTasks').disabled);};
    const waitPending=async id=>{for(let i=0;i<100;i++){if(pending.has(id))return;await new Promise(resolve=>setTimeout(resolve,10));}throw Error('missing synthetic POST: '+id);};
    await page.goto(base);
    await page.locator('[data-detail="A"]').last().click();
    await page.waitForSelector('#detailDialog[open] #inputNote');
    await page.locator('#inputNote').fill('未提交的旧动作草稿');
    items.A=task('A',{updated_at:'2026-09-23T10:01:00Z',required_action:{kind:'input',action_id:'action-2',label:'补充资料',reason:'新动作'}});
    await refresh();
    await page.waitForFunction(()=>document.querySelector('#detailBody')?.textContent.includes('新动作'));
    assert.equal(await page.locator('#inputNote').inputValue(),'','new action must start with an empty form');
    assert.equal(await page.locator('#staleInputDraft').count(),1,'old draft needs a read-only copy');
    assert.match(await page.locator('#staleInputDraft').innerText(),/旧动作已失效/);
    assert.match(await page.locator('#staleInputDraft').innerText(),/未提交的旧动作草稿/);
    assert.deepEqual(posts,[]);

    await page.locator('#inputNote').fill('第二个动作的草稿');
    items.A=task('A',{execution_state:'failed',updated_at:'2026-09-23T10:02:00Z',required_action:null,executor_connected:true});
    await refresh();
    await page.waitForFunction(()=>!document.querySelector('#inputForm'));
    assert.match(await page.locator('#staleInputDraft').innerText(),/第二个动作的草稿/);
    assert.equal(await page.locator('#retryTask').count(),1);
    release={channel:'synthetic',version:'v2'};
    await refresh();
    await page.waitForFunction(()=>!document.querySelector('#retryTask'));
    release=version;
    await refresh();
    await page.waitForFunction(()=>!!document.querySelector('#retryTask'));

    await page.locator('#cancelTask').click();
    await page.waitForFunction(()=>document.querySelector('#cancelTask')?.disabled);
    await waitPending('A');
    assert.equal(posts.filter(p=>p.id==='A').length,1);
    await page.evaluate(()=>document.querySelector('[data-detail="B"]').click());
    await page.waitForFunction(()=>document.querySelector('#detailTitle')?.textContent==='B');
    await page.locator('#cancelTask').click();
    await page.waitForFunction(()=>document.querySelector('#cancelTask')?.disabled);
    await waitPending('B');
    assert.equal(posts.filter(p=>p.id==='B').length,1);
    const aResponse=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/orbit/tasks/A/cancel');
    pending.get('A')();
    await aResponse;
    await page.waitForTimeout(50);
    assert.equal(await page.locator('#detailError').textContent(),'','late A error must not appear in B detail');
    assert.equal(await page.locator('#cancelTask').isDisabled(),true,'late A error must not re-enable B action');
    await page.evaluate(()=>document.querySelector('#cancelTask').dispatchEvent(new MouseEvent('click',{bubbles:true})));
    assert.equal(posts.filter(p=>p.id==='B').length,1,'B action must not duplicate while pending');
    pending.get('B')();
    await page.waitForFunction(()=>document.querySelector('#detailError')?.textContent.includes('synthetic B failure'));

    // Closing and reopening a task must not permit a second POST while its first POST is unresolved.
    await page.locator('[data-close="detailDialog"]').click();
    await page.locator('[data-detail="A"]').last().click();
    await page.waitForFunction(()=>document.querySelector('#detailTitle')?.textContent==='A');
    await page.locator('#cancelTask').click();
    await waitPending('A');
    const aCount=posts.filter(p=>p.id==='A').length;
    await page.locator('[data-close="detailDialog"]').click();
    await page.locator('[data-detail="A"]').last().click();
    await page.waitForFunction(()=>document.querySelector('#detailTitle')?.textContent==='A');
    assert.equal(await page.locator('#cancelTask').isDisabled(),true,'reopened A must show its pending mutation');
    await page.evaluate(()=>document.querySelector('#cancelTask').dispatchEvent(new MouseEvent('click',{bubbles:true})));
    assert.equal(posts.filter(p=>p.id==='A').length,aCount,'reopened A must not duplicate its POST');
    pending.get('A')();
    await page.waitForFunction(()=>document.querySelector('#cancelTask')&&!document.querySelector('#cancelTask').disabled);
    assert.equal(await page.locator('#detailError').textContent(),'','old A error must not attach to reopened A');

    // B's in-flight operation must not replace A's guard when A is reopened.
    await page.locator('#cancelTask').click();
    await waitPending('A');
    await page.evaluate(()=>document.querySelector('[data-detail="B"]').click());
    await page.waitForFunction(()=>document.querySelector('#detailTitle')?.textContent==='B');
    await page.locator('#cancelTask').click();
    await waitPending('B');
    await page.evaluate(()=>document.querySelector('[data-detail="A"]').click());
    await page.waitForFunction(()=>document.querySelector('#detailTitle')?.textContent==='A');
    assert.equal(await page.locator('#cancelTask').isDisabled(),true,'A guard must survive concurrent B mutation');
    const aCountDuringB=posts.filter(p=>p.id==='A').length;
    await page.evaluate(()=>document.querySelector('#cancelTask').dispatchEvent(new MouseEvent('click',{bubbles:true})));
    assert.equal(posts.filter(p=>p.id==='A').length,aCountDuringB,'B must not allow another A POST');
    pending.get('B')();
    await page.waitForTimeout(50);
    assert.equal(await page.locator('#detailError').textContent(),'','late B error must not appear in reopened A');
    assert.equal(await page.locator('#cancelTask').isDisabled(),true);
    pending.get('A')();
    await page.waitForFunction(()=>document.querySelector('#cancelTask')&&!document.querySelector('#cancelTask').disabled);
    assert.equal(await page.locator('#detailError').textContent(),'');
    assert.deepEqual(errors,[]);
    console.log('PASS stale drafts, release retry, late mutation isolation, and per-task in-flight guards');
  }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
