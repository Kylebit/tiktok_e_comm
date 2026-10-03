// Read-only task-home browser regression. All task data is synthetic.
const {chromium}=require('C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..'),out=process.argv[2];
if(!out)throw Error('Evidence directory required');
fs.mkdirSync(out,{recursive:true});
const offer='3956742887';
const tasks=[
  {task_id:'TASK-395',template:'publication',title:'上架 Offer '+offer,scope:{offer_id:offer,shops:['TK MY','Shopee MY']},execution_state:'failed',current_step:'release',review_mode:'single-final-review/v1',blocked_reason:'ValueError',last_executor:'local-previous',steps:[{key:'release',label:'审核并发布',state:'failed'}],updated_at:'2026-09-22T10:00:00Z'},
  {task_id:'TASK-GAP',template:'profit',title:'八月利润',scope:{month:'2026-08'},execution_state:'external_task',external_task:{owner:'旧数据会话',observed_status:'needs_review',observed_at:'2026-09-19T08:00:00Z'},checkpoint:{business_status:'needs_review'},result_url:'/profit'},
  {task_id:'TASK-FAKE',template:'publication',title:'非法 Offer',scope:{offer_id:'123&action=approve'},execution_state:'failed',current_step:'release',steps:[{key:'release',label:'审核并发布',state:'failed'}]},
  {task_id:'TASK-OLD-RECEIPT',template:'publication',title:'旧凭据不能终审',scope:{offer_id:offer,shops:['TK MY']},owner:'current-owner',execution_state:'waiting_user',current_step:'release',review_mode:'single-final-review/v1',required_action:{kind:'review',action_id:'old-receipt',url:'/product-workspace?offer_id='+offer+'&action_id=old-receipt',receipt_binding:{adapter:'single-final-review/v1',offer_id:offer,task_id:'TASK-OLD-RECEIPT',action_id:'old-receipt'}},steps:[{key:'release',label:'审核并发布',state:'waiting_user'}]},
  {task_id:'TASK-CANCELLED',template:'publication',title:'已取消的历史审核',scope:{offer_id:offer},owner:'retired-owner',execution_state:'cancelled',current_step:'release',steps:[{key:'release',label:'审核并发布',state:'pending'}]},
];
const operations=Array.from({length:4},(_,i)=>({operation_id:'operation-'+(i+1),owner_task_id:['TASK-395','TASK-OLD-RECEIPT','TASK-CANCELLED',null][i],state:'inflight',created_at:'2026-09-22T08:00:00Z',resource_count:1,locked_resource_count:1,affected_targets:[{sku:'096'+i,target:'TK MY'}]}));
const posts=[],errors=[];
const server=http.createServer((req,res)=>{
  const file=req.url==='/'?'/task_workspace.html':req.url;
  const p=path.join(root,'web',file);
  if(!p.startsWith(path.join(root,'web'))||!fs.existsSync(p)){res.writeHead(404);return res.end();}
  res.setHeader('Content-Type',p.endsWith('.js')?'text/javascript':p.endsWith('.css')?'text/css':'text/html');res.end(fs.readFileSync(p));
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const base='http://127.0.0.1:'+server.address().port;
  const browser=await chromium.launch({headless:true,executablePath:'C:/Users/Windows11/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe'});
  try{
    const page=await browser.newPage();page.on('pageerror',e=>errors.push(String(e)));
    await page.route('**/*',route=>{
      const req=route.request(),url=new URL(req.url());
      if(url.origin!==base)return route.abort();
      if(req.method()!=='GET'){posts.push(url.pathname);return route.fulfill({status:405,json:{error:'read only'}});}
      if(url.pathname==='/api/orbit/navigation')return route.fulfill({json:{ok:true,navigation:[]}});
      if(url.pathname==='/api/orbit/operations-runtime')return route.fulfill({json:{execution_mode:'web-only',environment:'preview'}});
      if(url.pathname==='/api/orbit/tasks')return route.fulfill({json:{tasks,domain_guard:{unresolved_operation_count:4,locked_resource_count:4},domain_operations:operations,executor:{connected:false},dispatcher:{state:'stopped'},release:{environment:'preview'}}});
      if(url.pathname.startsWith('/api/orbit/tasks/'))return route.fulfill({json:{task:tasks.find(t=>t.task_id===url.pathname.split('/').pop()),events:[]}});
      return route.continue();
    });
    await page.goto(base);await page.waitForFunction(()=>document.querySelector('#countAll')?.textContent==='5');
    await page.waitForSelector('#pendingChecks', {state:'attached'});
    assert.equal(await page.locator('#pendingChecks').evaluate(element=>element.open),false);
    assert.equal(await page.locator('#pendingChecksList').isVisible(),false);
    await page.locator('#pendingChecks > summary').click();
    await page.waitForSelector('#pendingChecksList');
    const queue=await page.locator('#pendingChecksList').innerText();
    assert.match(queue,/operation-1/);assert.match(queue,/operation-4/);assert.match(queue,/0960/);
    assert.match(queue,/官方回读/);assert.match(queue,/未关联当前任务/);
    assert.match(queue,/TASK-395/);assert.match(queue,/ValueError/);assert.match(queue,/八月利润/);
    const operationCards=page.locator('#pendingChecksList .pending-group').first().locator('.pending-item');
    assert.match(await operationCards.nth(0).innerText(),/关联任务：上架 Offer 3956742887/);
    assert.match(await operationCards.nth(0).innerText(),/当前负责人：未登记/);
    assert.match(await operationCards.nth(1).innerText(),/当前负责人：current-owner/);
    assert.match(await operationCards.nth(2).innerText(),/当前负责人：未登记/);
    assert.doesNotMatch(await operationCards.nth(2).innerText(),/retired-owner/);
    const failedCard=page.locator('#pendingChecksList .pending-group').nth(1).locator('.pending-item').first();
    assert.match(await failedCard.innerText(),/当前负责人：未登记/);
    assert.match(await failedCard.innerText(),/上次执行者：local-previous/);
    const gapCard=page.locator('#pendingChecksList .pending-group').nth(2).locator('.pending-item').filter({hasText:'TASK-GAP'});
    assert.match(await gapCard.innerText(),/当前负责人：未登记/);
    assert.match(await gapCard.innerText(),/历史观测来源：旧数据会话/);
    assert.equal(await page.locator('#countAttention').innerText(),'0');
    assert.equal(await page.locator('#countCancelled').innerText(),'1');
    assert.equal(await page.locator('#pendingChecksList [data-detail="operation-1"]').count(),0);
    await page.locator('[data-detail="TASK-395"]').last().click();
    await page.waitForFunction(()=>document.querySelector('#detailBody')?.textContent.includes('冻结候选'));
    const detail=page.locator('#detailBody');
    assert.match(await detail.innerText(),/COMMON/);assert.match(await detail.innerText(),/完整目标/);
    assert.equal(await detail.locator('a[href="/product-workspace?offer_id=3956742887&round=final"]').count(),1);
    assert.equal(await detail.locator('button:has-text("批准")').count(),0);
    await page.locator('[data-close="detailDialog"]').click();
    await page.locator('[data-detail="TASK-FAKE"]').last().click();
    assert.equal(await page.locator('#detailBody a[href*="action=approve"]').count(),0);
    await page.locator('[data-close="detailDialog"]').click();
    await page.locator('[data-detail="TASK-OLD-RECEIPT"]').last().click();
    assert.equal(await page.locator('#detailBody a[href*="action_id="]').count(),0);
    assert.equal(await page.locator('#detailBody button:has-text("批准")').count(),0);
    assert.equal(await page.locator('#countAttention').innerText(),'0');
    assert.deepEqual(posts,[]);assert.deepEqual(errors,[]);
    await page.locator('[data-close="detailDialog"]').click();
    await page.setViewportSize({width:1440,height:950});await page.screenshot({path:path.join(out,'desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'mobile.png'),fullPage:true});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    fs.writeFileSync(path.join(out,'RESULT.json'),JSON.stringify({synthetic:true,checks:['four_operations','failure','data_gap','zero_user_actions','exact_offer_readonly_link','malformed_offer_blocked','stale_receipt_blocked','no_business_posts','responsive'],posts,errors},null,2));
    console.log('PASS task-home actionability Chromium');
  }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
