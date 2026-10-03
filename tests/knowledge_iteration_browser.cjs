const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(path.join(process.env.ORBIT_NODE_MODULES,'playwright'));
const [origin,out]=process.argv.slice(2);
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.ORBIT_BROWSER_EXECUTABLE?{executablePath:process.env.ORBIT_BROWSER_EXECUTABLE}:{channel:'chrome'})});
 const record={status:'running',external:[],writes:[],errors:[],views:[]};
 try{
  for(const width of [1440,390]){
   const context=await browser.newContext({viewport:{width,height:900},permissions:['clipboard-read','clipboard-write']});
   await context.route('**/*',route=>{const r=route.request();if(new URL(r.url()).origin!==origin){record.external.push(r.url());return route.abort();}if(r.method()!=='GET'){record.writes.push(r.method());return route.abort();}return route.continue();});
   const page=await context.newPage();page.setDefaultTimeout(5000);page.on('pageerror',e=>record.errors.push(String(e)));
   await page.goto(origin+'/knowledge');await page.locator('.knowledge-row').first().waitFor();
   assert.equal(await page.locator('.knowledge-row').count(),13);
   const data=await page.evaluate(()=>fetch('/static/knowledge_guides.json').then(r=>r.json()));
   await page.locator('#knowledgeIteration').scrollIntoViewIfNeeded();
   assert.match(await page.locator('#knowledgeIteration').innerText(),/1135/);
   await page.locator('.knowledge-candidate > summary').click();
   assert.match(await page.locator('.knowledge-candidate').innerText(),/尚未批准/);
   await page.locator('.knowledge-maintenance > summary').click();
   await page.getByText('检索已核验来源',{exact:true}).click();
   await page.locator('[data-copy-target="knowledgeSearchCommand"]').click();
   assert.equal((await page.evaluate(()=>navigator.clipboard.readText())).replace(/\r\n/g,'\n'),(await page.locator('#knowledgeSearchCommand').inputValue()).replace(/\r\n/g,'\n'));
   await page.evaluate(()=>{navigator.clipboard.writeText=async()=>{throw Error('synthetic denied');};});
   await page.locator('[data-copy-target="knowledgeSearchCommand"]').click();
   assert.match(await page.locator('#knowledgeSearchCommandState').innerText(),/Ctrl\+C/);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   await page.screenshot({path:path.join(out,`iteration-${width}.png`),fullPage:true});
   for(const kind of ['missing','empty','drift','malformed']){
    const mutated=structuredClone(data);
    if(kind==='missing')delete mutated.iteration;
    if(kind==='empty'){mutated.iteration.notes=0;mutated.iteration.by_kind={};mutated.iteration.by_freshness={};mutated.iteration.source_check={};}
    if(kind==='drift'){mutated.iteration.source_check={changed:1};mutated.iteration.candidate.matches=false;}
    if(kind==='malformed')mutated.iteration={schema:'wrong'};
    await context.route('**/static/knowledge_guides.json',r=>r.fulfill({contentType:'application/json',body:JSON.stringify(mutated)}));
    await page.reload();await page.locator('.knowledge-row').first().waitFor();
    const text=await page.locator('#knowledgeIteration').innerText();
    assert.match(text,kind==='empty'?/索引为空/:kind==='drift'?/来源变化或缺失/:/尚未接入/);
    assert.equal(await page.locator('.knowledge-row').count(),13);
    await context.unroute('**/static/knowledge_guides.json');
   }
   record.views.push({width,checks:'copy and fallback, no overflow, unreviewed candidate, missing/empty/drift/malformed index, 13 guides retained'});
   await context.close();
  }
  assert.deepEqual(record.external,[]);assert.deepEqual(record.writes,[]);assert.deepEqual(record.errors,[]);record.status='passed';
 }finally{fs.writeFileSync(path.join(out,'knowledge-iteration-result.json'),JSON.stringify(record,null,2));await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
