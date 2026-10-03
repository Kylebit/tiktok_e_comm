const {chromium}=require('C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..');
const markupRoot=process.env.ORBIT_ORIGINAL_UI_ROOT||root;
(async()=>{const browser=await chromium.launch({headless:true,executablePath:'C:/Users/Windows11/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe'});try{
 const page=await browser.newPage();let writes=0;
 await page.route('http://127.0.0.1/**',r=>{if(r.request().method()==='POST')writes++;return r.fulfill({contentType:'text/html',body:fs.readFileSync(markupRoot+'/web/product_workspace.html','utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'')});});
 await page.goto('http://127.0.0.1/product-workspace?offer_id=123&round=first');
 await page.addScriptTag({content:fs.readFileSync(root+'/web/static/publication_original_review.js','utf8')});
 for(const file of ['product_workspace_round1_category.js','publication_original_adapter.js'])await page.addScriptTag({content:fs.readFileSync(root+'/web/static/'+file,'utf8')});
 await page.evaluate(()=>document.dispatchEvent(new Event('DOMContentLoaded')));
 const value={product:{offer_id:'123',revision:1,title:'合成商品',seller_sku_candidate:'660001'},publication_scope:{selected_labels:['shopee:MY']},round1_prepared_review:{ok:true,status:'PREPARED',current_revision:1,prepared_reference:'r1-exact',approval_actor:'local-user',packet:{status:'FIRST_REVIEW_READY',blockers:[],offer_id:'123',targets:['shopee:MY'],publication_stock_policy:{schema_version:'publication-default-stock/v1',quantity_per_sku:200,scope:'EACH_SELECTED_SKU',source:'SYSTEM_GOVERNED_DEFAULT',review_round:'ROUND1'},image_execution_plan:{brand_plans:[]}}}};
 await page.evaluate(v=>{window.OrbitRound1Category.render(v);window.OrbitOriginalReview.render(v);},value);
 assert.equal(await page.locator('#originalPublicationReview').evaluate(n=>n.hidden),false);
 assert.equal(await page.locator('#firstReviewWorkspaceStatus').textContent(),'第一轮待确认');
 assert.match(await page.locator('#firstReviewDecisionFacts').textContent(),/每个所选 SKU 200 件/);
 assert.equal(await page.locator('#originalRound1Confirmation #round1Category').count(),1);
 assert.equal(await page.locator('#round1Category').getByRole('button',{name:'批准当前首轮事实并冻结快照'}).isEnabled(),true);
 assert.equal(writes,0);
 for(const invalid of [packet=>{delete packet.publication_stock_policy;},packet=>{packet.publication_stock_policy.quantity_per_sku=2;},packet=>{packet.publication_stock_policy.extra='unreviewed';}]){
   const changed=structuredClone(value);invalid(changed.round1_prepared_review.packet);
   await page.evaluate(v=>{window.OrbitRound1Category.render(v);window.OrbitOriginalReview.render(v);},changed);
   assert.equal(await page.locator('#round1Category').getByRole('button',{name:'批准当前首轮事实并冻结快照'}).isEnabled(),false);
   assert.equal(await page.locator('#firstReviewWorkspaceStatus').textContent(),'首轮资料待补充');
   assert.match(await page.locator('#firstReviewDecisionFacts').textContent(),/库存策略 · 缺失或变更，当前不可审核/);
 }
 const legacyFrozen=structuredClone(value);delete legacyFrozen.round1_prepared_review.packet.publication_stock_policy;
 await page.evaluate(v=>{const frozen={...v,frozen_review_projection:true,frozen_first_review:{...v.round1_prepared_review.packet,approved_revision:2}};window.OrbitRound1Category.render(frozen);window.OrbitOriginalReview.render(frozen);},legacyFrozen);
 assert.equal(await page.locator('#firstReviewWorkspaceStatus').textContent(),'第一轮已冻结');
 assert.match(await page.locator('#firstReviewDecisionFacts').textContent(),/历史首轮记录未提供有效策略，当前页面不补写/);
 assert.equal(await page.locator('#round1Category').getByRole('button',{name:'批准当前首轮事实并冻结快照'}).count(),0);
 assert.equal(writes,0);console.log('PASS pending original layout is visible with exact confirmation; frozen label retained; no automatic writes');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
