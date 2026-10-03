const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {chromium}=require('playwright');const [origin,out]=process.argv.slice(2);
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.ORBIT_BROWSER_EXECUTABLE});
 const checks=[],errors=[],blocked=[],requests=[],resources=[],pending=[];
 try{for(const width of [1440,390]){
  const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage();
  await context.route('**/*',route=>{const r=route.request();requests.push({method:r.method(),url:r.url()});if(new URL(r.url()).origin!==origin||r.method()!=='GET'){blocked.push(r.url());return route.abort();}return route.continue();});
  page.on('pageerror',e=>errors.push(e.message));
  page.on('response',r=>{if(r.status()===200&&(/\.(js|css)$/.test(r.url())||r.url().endsWith('/product-workspace')||r.url().endsWith('/profit')))pending.push(r.body().then(b=>resources.push({url:r.url(),sha256:crypto.createHash('sha256').update(b).digest('hex')})));});
  await page.goto(origin+'/product-workspace');
  const nav=async href=>{const a=page.locator('#orbitCoreNav a[href="'+href+'"]');await a.waitFor({state:'attached'});if(!await a.isVisible())await page.locator('#orbitMenu').click();await a.click();};
  const profit=await context.request.get(origin+'/profit',{maxRedirects:0});assert.equal(profit.status(),200);assert.ok((await profit.text()).includes('/profit-original/artifacts/profit_reports_monthly/2026-07/index.html'));
  await nav('/supply-chain/');await page.waitForFunction(()=>window.SUPPLY_CHAIN_CAPTURE_STATE?.status==='COMPLETE');
  assert.equal(await page.locator('.sku-summary').count(),1);
  assert.deepEqual(await page.evaluate(()=>Object.keys(window.SUPPLY_CHAIN_DATA.countries).sort()),['MY','PH','TH','VN']);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.screenshot({path:path.join(out,'combined-supply-'+width+'.png'),fullPage:true});
  await page.goBack();await page.waitForSelector('#orbitMenu',{state:'attached'});
  assert.equal(new URL(page.url()).pathname,'/product-workspace');
  checks.push({width,productFinanceCapturedSupplyAndHistoryReturn:true});await context.close();
 }await Promise.all(pending);assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);
 }finally{fs.writeFileSync(path.join(out,'navigation-result.json'),JSON.stringify({checks,errors,blocked,requests,resources},null,2));await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
