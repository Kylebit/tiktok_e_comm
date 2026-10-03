const {chromium} = require('playwright');
const fs = require('fs');
const path = require('path');
const assert = require('assert');

(async () => {
  const [base, output] = process.argv.slice(2);
  const profileHome=path.join(fs.realpathSync(output),'profile-home');
  fs.mkdirSync(profileHome,{recursive:true});
  const context = await chromium.launchPersistentContext(path.join(fs.realpathSync(output),'chrome-profile'),{headless:true, timeout:15000, executablePath:'C:/Users/Windows11/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe',
    env:{...process.env,USERPROFILE:profileHome,LOCALAPPDATA:path.join(profileHome,'Local'),APPDATA:path.join(profileHome,'Roaming')},
    viewport:{width:1440,height:1000},serviceWorkers:'block',
    args:['--disable-background-networking','--disable-component-update','--no-first-run']});
  const requests=[], denied=[], errors=[], stageResponses=[];
  await context.route('**/*',route=>{
    const url = route.request().url();
    if (url.startsWith(base+'/') || url.startsWith('data:') || url.startsWith('blob:')) return route.continue();
    denied.push(url);return route.abort();
  });
  const page=await context.newPage();
  page.on('pageerror',e=>errors.push(String(e)));
  page.on('response',async r=>{if(r.url().includes('/publication-stages?'))stageResponses.push({url:r.url(),status:r.status(),body:await r.json()});});
  page.on('request',r=>requests.push({url:r.url(),method:r.method()}));
  try {
    if(['common-unknown','common-verified'].includes(process.env.U01_BROWSER_SCENARIO)){
      const unknown=process.env.U01_BROWSER_SCENARIO==='common-unknown';
      await page.goto(base+'/product-workspace?offer_id=9000052');
      await page.locator('#productTitle').filter({hasText:'Synthetic retained product'}).waitFor({timeout:10000});
      await page.waitForFunction(label=>document.querySelector('#nextStepTitle')?.textContent===label,unknown?'核对原 COMMON 结果':'核对平台准备条件',{timeout:5000});
      await page.locator('#tab-release').click();
      assert((await page.locator('#publicationFlowLedger').textContent()).includes(unknown?'RECONCILIATION_REQUIRED':'VERIFIED'));
      assert(await page.locator('#publicationFlowActions button').count()===0);
      assert((await page.locator('#publicationFlowReview').textContent()).includes('原计划的成功或批准，不代表当前审核版本可发布'));
      assert(requests.filter(r=>r.method==='POST').length===0);
      await page.screenshot({path:path.join(output,`common-${unknown?'unknown':'verified'}-1440.png`),fullPage:true});
      await page.setViewportSize({width:390,height:844});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
      await page.screenshot({path:path.join(output,`common-${unknown?'unknown':'verified'}-390.png`),fullPage:true});
      fs.writeFileSync(path.join(output,'BROWSER_RESULT.json'),JSON.stringify({passed:true,scenario:process.env.U01_BROWSER_SCENARIO,requests,denied,errors,stageResponses},null,2));
      assert.deepStrictEqual(errors,[]);assert.deepStrictEqual(denied,[]);
      return;
    }
    await page.goto(base+'/product-workspace');
    await page.locator('#openManualIntake').click({timeout:5000});
    await page.locator('#manualTitle').fill('Local blue tile');
    await page.locator('#manualDescription').fill('User supplied local description');
    await page.locator('[data-manual-field="label"]').fill('Blue 10 cm');
    for(const [name,value] of Object.entries({cost:'3.25',weight:'0.12',length:'10',width:'10',height:'2'})) {
      await page.locator(`[data-manual-field="${name}"]`).fill(value);
    }
    await page.locator('#manualImages').setInputFiles(path.join(output,'tile.png'));
    await page.locator('#submitManualIntake').click();
    await page.locator('#manualStatus').filter({hasText:'已保存本地商品'}).waitFor({timeout:10000,state:'attached'});
    await page.locator('#openProductHistory').click();
    const row=page.locator('#productHistoryRows tr').filter({hasText:'Local blue tile'});
    assert(await row.count()===1);
    assert((await row.textContent()).includes('手工录入'));
    await row.locator('button').click();
    await page.locator('#productTitle').filter({hasText:'Local blue tile'}).waitFor({timeout:10000});
    assert((await page.locator('#productIdentity').textContent()).includes('手工录入'));
    await page.locator('#factsSourceDetails > summary').click();
    await page.locator('#roundEvidence > summary').click();
    const localImage=page.locator('[data-local-source-image]');
    await localImage.waitFor();
    assert(await localImage.evaluate(image=>image.complete&&image.naturalWidth===12));
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:path.join(output,'history-1440.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:path.join(output,'history-390.png'),fullPage:true});
    assert.deepStrictEqual(errors,[]);
    assert.deepStrictEqual(denied,[]);
    const posts=requests.filter(r=>r.method==='POST');
    assert(posts.length===1 && posts[0].url.endsWith('/manual-intake'));
    fs.writeFileSync(path.join(output,'BROWSER_RESULT.json'),JSON.stringify({passed:true,requests,denied,errors},null,2));
  } catch(e) {
    await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});
    fs.writeFileSync(path.join(output,'BROWSER_RESULT.json'),JSON.stringify({passed:false,error:String(e),requests,denied,errors,stageResponses},null,2));
    throw e;
  } finally {await context.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
