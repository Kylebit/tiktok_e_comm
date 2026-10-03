const {chromium}=require('C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs'),assert=require('node:assert/strict'),path=require('node:path');
(async()=>{const browser=await chromium.launch({headless:true,executablePath:'C:/Users/Windows11/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe'});try{
 const page=await browser.newPage();let requests=[];
 await page.route('http://127.0.0.1/**',r=>{if(r.request().method()==='POST'){requests.push(r.request().postDataJSON());return r.fulfill({json:{ok:true,status:'FROZEN'}});}return r.fulfill({contentType:'text/html',body:'<div id="pane-facts"></div><button id="approvalButton" disabled><span class="button-label"></span></button>'});});
 await page.goto('http://127.0.0.1');await page.addScriptTag({content:fs.readFileSync(path.resolve(__dirname,'../../web/static/product_workspace_round1_category.js'),'utf8')});
 const value={product:{offer_id:'123',revision:1},publication_scope:{selected_labels:['shopee:MY']},round1_prepared_review:{ok:true,status:'PREPARED',current_revision:1,prepared_reference:'exact-reference',approval_actor:'local-user',packet:{status:'FIRST_REVIEW_READY',blockers:[],offer_id:'123',targets:['shopee:MY']}}};
 await page.evaluate(v=>window.OrbitRound1Category.render(v),value);
 await page.locator('#round1Category').getByRole('button',{name:'批准当前首轮事实并冻结快照',exact:true}).click();
 await page.waitForTimeout(100);
 assert.equal(requests.length,1);assert.deepEqual(requests[0],{offer_id:'123',prepared_reference:'exact-reference',approved_by:'local-user',user_approved:true});
 await page.evaluate(v=>window.OrbitRound1Category.render({...v,round1_prepared_review:undefined}),value);
 assert.equal(await page.locator('#approvalButton').isDisabled(),true);
 console.log('PASS server prepared restoration submits exact original approval, missing reference disables it');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
