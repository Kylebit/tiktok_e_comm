const {chromium}=require(require('path').join(process.env.ORBIT_NODE_MODULES,'playwright'));
const fs=require('fs');const path=require('path');const assert=require('node:assert/strict');
const out=process.argv[2],preview=JSON.parse(fs.readFileSync(path.join(out,'preview.json'),'utf8'));
const base=preview.url,origin=new URL(base).origin;
const expected=[
 ['catalog','商品目录','/catalog'],
 ['product','商品上架','/product-workspace'],
 ['supply-chain','供应链','/supply-chain/'],
 ['data','利润','/profit'],
 ['knowledge','知识工具','/knowledge'],
];
(async()=>{
 const executablePath=process.env.ORBIT_BROWSER_EXECUTABLE;
 const browser=await chromium.launch({headless:true,...(executablePath?{executablePath}:{channel:'chrome'})});
 const record={preview,status:'running',checks:[],console:[],requests:[],external:[],screenshots:[]};
 try{
  for(const [width,height] of [[1440,1000],[390,844]]){
   const context=await browser.newContext({viewport:{width,height}}),page=await context.newPage();
   await context.route('**/*',route=>{const url=new URL(route.request().url());if(url.origin!==origin){record.external.push(url.href);return route.abort();}record.requests.push({width,method:route.request().method(),url:url.href});return route.continue();});
   await context.route('**/api/orbit/tasks',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({ok:true,tasks:[],executor:{connected:false},dispatcher:{state:'stopped'},release:{environment:'preview'},domain_guard:{unresolved_operation_count:0,locked_resource_count:0},domain_operations:[]})}));
   await context.route('**/api/orbit/operations-runtime',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({execution_mode:'web-only',worker_enabled:false,environment:'preview'})}));
   page.on('console',message=>{if(message.type()==='error')record.console.push({width,text:message.text()});});
   page.on('pageerror',error=>record.console.push({width,text:String(error)}));
   await page.goto(base);await page.getByRole('heading',{name:'任务工作台',exact:true}).waitFor();
   await page.waitForFunction(()=>document.querySelector('#countAll')?.textContent==='0');
   const nav=await page.locator('#orbitCoreNav a').evaluateAll(nodes=>nodes.map(node=>[node.dataset.core,node.textContent,node.getAttribute('href')]));
   assert.deepEqual(nav,expected);assert.equal(await page.locator('iframe').count(),0);
   assert.equal(await page.locator('#createTask').isDisabled(),true);
   assert((await page.locator('#connection').innerText()).includes('只读预览'));
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   const shot=path.join(out,`task-home-${width}.png`);await page.screenshot({path:shot,fullPage:true});record.screenshots.push(shot);
   record.checks.push({width,view:'task-workspace',primary_navigation:nav.map(row=>row[0]),read_only:true});
   await page.goto(new URL('/knowledge',base).href);await page.locator('#knowledgeDirectory').waitFor();
   assert.equal(await page.locator('#orbitCoreNav a[aria-current="page"]').getAttribute('href'),'/knowledge');
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   record.checks.push({width,view:'knowledge',navigation_retained:true});
   await context.unroute('**/api/orbit/tasks');
   await context.route('**/api/orbit/tasks',route=>route.fulfill({status:503,contentType:'application/json',body:'{}'}));
   await page.goto(base);await page.locator('#pageError').waitFor({state:'visible'});
   assert.equal(await page.locator('#orbitCoreNav a').count(),5);
   record.checks.push({width,view:'task-workspace-error',navigation_retained:true});
   await context.close();
  }
  assert.deepEqual(record.external,[]);assert.deepEqual(record.requests.filter(item=>item.method!=='GET'),[]);
  assert.deepEqual(record.console.filter(item=>!item.text.includes('503')),[]);record.status='passed';
 }catch(error){record.status='failed';record.error=String(error);throw error;}
 finally{fs.writeFileSync(path.join(out,'browser-result.json'),JSON.stringify(record,null,2));await browser.close();}
 console.log(JSON.stringify({status:record.status,checks:record.checks.length,screenshots:record.screenshots}));
})().catch(error=>{console.error(error);process.exitCode=1;});
