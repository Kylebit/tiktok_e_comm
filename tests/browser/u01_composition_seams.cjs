const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const base=process.argv[2],out=process.argv[3];
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.ORBIT_BROWSER_EXECUTABLE});
 const context=await browser.newContext({viewport:{width:1440,height:900}}), page=await context.newPage();
 const checks=[],errors=[],requests=[],blocked=[];
 await context.route('**/*',route=>{
  const request=route.request();
  if(new URL(request.url()).origin!==new URL(base).origin||request.method()!=='GET'){
   blocked.push(request.url());return route.abort();
  }
  return route.continue();
 });
 page.on('pageerror',e=>errors.push(String(e)));
 page.on('request',r=>requests.push({method:r.method(),url:r.url()}));
 try{
  await page.goto(base+'product-workspace');
  await page.waitForSelector('#orbitCoreNav a[href="/profit"]');
  const profit=await context.request.get(base+'profit',{maxRedirects:0});
   assert.equal(profit.status(),200);assert.ok((await profit.text()).includes('/profit-original/artifacts/profit_reports_monthly/2026-07/index.html'));
   checks.push({name:'product navigation opens profit hub with retained July report link',ok:true});
  await page.locator('#orbitCoreNav a[href="/supply-chain/"]').click();
  await page.waitForSelector('.sku-summary');
  assert.equal(await page.locator('.sku-summary').count(),8);
  checks.push({name:'finance navigation opens actual supply page with synthetic rows',ok:true});
  await page.goBack();await page.waitForSelector('#orbitCoreNav');
  assert.equal(new URL(page.url()).pathname,'/product-workspace');
  checks.push({name:'browser history returns through finance to product',ok:true});
  assert.deepEqual(blocked,[]);assert.deepEqual(errors,[]);
  checks.push({name:'navigation is GET-only without external requests or script errors',ok:true});
  await page.screenshot({path:path.join(out,'navigation-desktop.png')});
 }catch(error){checks.push({name:'composition navigation',ok:false,error:String(error)});}
 fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({base,checks,errors,requests,blocked},null,2));
 await browser.close();process.exitCode=checks.every(c=>c.ok)?0:1;
})().catch(e=>{console.error(e);process.exit(2);});
