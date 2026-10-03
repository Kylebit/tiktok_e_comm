// Returned-task contract only. Every task POST is intercepted before HTTP I/O.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const root=path.resolve(__dirname,'../..'),out=process.argv[2];
if(!out||!process.env.ORBIT_BROWSER_EXECUTABLE)throw Error('Fixed browser and evidence directory required');
fs.mkdirSync(out,{recursive:true});
const release={environment:'stable',code_version:'a'.repeat(40),manifest_digest:'b'.repeat(64)};
const profitScope='EXPLICIT_NEW_POST_PROFIT_READONLY';
const runtime={environment:'stable',version:release.code_version,manifest_digest:release.manifest_digest,
  capabilities:{profit:{connected:true,scope:profitScope}},
  new_task_preparation:{scope:'EXPLICIT_NEW_POST_PUBLICATION_ONLY',running:true,state:'polling',
    profit_scope:profitScope,profit_executor_connected:true,historical_task_scan:false}};
const cases=['old-completed','old-unknown','old-offline','same-source','missing-source'];
const taskFor=(kind,source)=>({task_id:'original-'+kind,title:'Original '+kind,template:'profit',
  source_key:kind==='missing-source'?undefined:kind==='same-source'?source:'old-'+kind,
  scope:{month:'2026-08',shops:['owned-shop'],skus:[]},version:release,executor_connected:false,steps:[],
  execution_state:kind==='old-completed'?'completed':kind==='old-unknown'?'reconciliation_required':'executor_offline',
  ...(kind==='old-completed'?{result_url:'/artifacts/original-report/index.html'}:{}),
  ...(kind==='old-unknown'?{current_step:'coverage',profit_unknown_readback:{status:'UNKNOWN',
    session_readback:'NOT_VERIFIED',automatic_resume_allowed:false}}:{})});
const errors=[],creates=[],otherPosts=[];let selected=null;
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
    for(const width of [1440,390]){
      const page=await browser.newPage({viewport:{width,height:width===390?844:1000}});
      page.on('pageerror',error=>errors.push(String(error)));
      let kind=cases[0];selected=null;
      await page.route('**/*',route=>{
        const req=route.request(),url=new URL(req.url());
        if(url.origin!==base)return route.abort();
        if(req.method()!=='GET'){
          if(url.pathname!=='/api/orbit/tasks'){otherPosts.push(url.pathname);return route.abort();}
          const body=req.postDataJSON();
          assert.equal(body.template,'profit');assert.equal(body.reuse_existing_scope,true);
          assert.equal(body.scope.month,'2026-08');assert.deepEqual(body.scope.shops,['owned-shop']);
          assert.ok(body.source_key);selected=taskFor(kind,body.source_key);creates.push({width,kind});
          // Original workbench_http schema has a task and no invented created flag.
          return route.fulfill({status:201,json:{task:selected}});
        }
        if(!url.pathname.startsWith('/api/'))return route.continue();
        if(url.pathname==='/api/orbit/navigation')return route.fulfill({json:{ok:true,navigation:[]}});
        if(url.pathname==='/api/orbit/operations-runtime')return route.fulfill({json:runtime});
        if(url.pathname==='/api/orbit/tasks')return route.fulfill({json:{tasks:selected?[selected]:[],release,
          executor:{connected:false,templates:[]},dispatcher:{state:'stopped',running:false}}});
        if(selected&&url.pathname==='/api/orbit/tasks/'+selected.task_id)return route.fulfill({json:{task:selected,events:[]}});
        return route.fulfill({status:404,json:{error:'No closed route'}});
      });
      await page.goto(base);await page.waitForFunction(()=>document.querySelector('#connection').textContent.includes('新建利润任务已接入只读统计'));
      for(kind of cases){
        await page.locator('#createTask').click();await page.locator('#newType').selectOption('profit');
        await page.locator('#newTitle').fill('Requested '+kind);
        await page.locator('#newMonth').fill('2026-08');await page.locator('#newShops').fill('owned-shop');
        await page.locator('#submitTask').click();
        await page.waitForFunction(kind=>document.querySelector('#detailTitle').textContent==='Original '+kind,kind);
        const notice=await page.locator('#toast').innerText();
        assert.match(notice,new RegExp('original-'+kind));assert.match(notice,/2026-08/);
        assert.doesNotMatch(notice,/grant|授权|已自动重跑|商业审核/);
        if(kind.startsWith('old-')){
          assert.match(notice,/本月已有任务，已打开原任务/);assert.match(notice,/本次未重新执行统计/);
        }else if(kind==='same-source')assert.match(notice,/利润任务已登记/);
        else assert.match(notice,/创建或复用状态待核/);
        if(kind==='old-completed'){
          assert.match(notice,/已完成|查看原报告/);
          assert.equal(await page.locator('#detailBody a[href="/artifacts/original-report/index.html"]').count(),1);
        }else assert.match(notice,/尚未提供报告入口/);
        if(kind==='old-unknown'){
          assert.match(notice,/原尝试待核，不自动重跑/);
          assert.equal(await page.locator('#retryTask,#cancelTask').count(),0);
        }
        if(kind==='old-offline')assert.match(notice,/等待执行器接入/);
        await page.locator('[data-close="detailDialog"]').click();
      }
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      await page.screenshot({path:path.join(out,`profit-reuse-${width}.png`),fullPage:true});
      await page.close();
    }
    assert.deepEqual(otherPosts,[]);assert.deepEqual(errors,[]);
    assert.equal(creates.length,10);
    console.log(JSON.stringify({synthetic:true,widths:[1440,390],cases,closed_task_creates:10,
      other_posts:otherPosts,errors,provider_calls:0}));
  }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
