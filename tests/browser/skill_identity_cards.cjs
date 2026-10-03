const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(path.join(process.env.ORBIT_NODE_MODULES, 'playwright'));
const [origin, output] = process.argv.slice(2);

const ids = ['prepare-product-publication','prepare-product-images','publish-approved-product',
  'delist-products-by-sku','apply-product-discounts','manage-seaya-replenishment','manage-profit-settlement'];
const states = { 'prepare-product-publication':'MATCH', 'prepare-product-images':'MATCH',
  'publish-approved-product':'RAW_DRIFT', 'delist-products-by-sku':'CONFLICT',
  'apply-product-discounts':'RAW_DRIFT', 'manage-seaya-replenishment':'CONFLICT',
  'manage-profit-settlement':'NOT_INSTALLED' };
const skill = (id) => {
  const status = states[id];
  return {project:{registry_verified:true},
    installed:status==='NOT_INSTALLED'?{state:'NOT_INSTALLED',source_type:'missing'}:
      {state:'PRESENT',source_type:id==='manage-seaya-replenishment'?'junction':'copy'},
    comparison:{status,missing_count:0,extra_count:0,changed_count:status==='CONFLICT'?1:0,
      normalized_changed_count:status==='CONFLICT'?1:0},execution_authority:false};
};
const identity = {schema:'orbit-skill-identity/v1',observed_at:'2026-09-28T00:00:00+00:00',
  execution_authority:false,skills:Object.fromEntries(ids.map(id=>[id,skill(id)]))};

(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.ORBIT_BROWSER_EXECUTABLE?{executablePath:process.env.ORBIT_BROWSER_EXECUTABLE}:{channel:'chrome'})});
  const record={views:[],errors:[],writes:[],external:[]};
  try{
    for(const width of [1440,390]){
      const context=await browser.newContext({viewport:{width,height:900}});
      await context.route('**/*',route=>{
        const request=route.request();
        if(new URL(request.url()).origin!==origin){record.external.push(request.url());return route.abort();}
        if(request.method()!=='GET'){record.writes.push(request.url());return route.abort();}
        if(new URL(request.url()).pathname==='/api/orbit/skill-identity')
          return route.fulfill({contentType:'application/json',body:JSON.stringify(identity)});
        return route.continue();
      });
      const page=await context.newPage();page.on('pageerror',error=>record.errors.push(String(error)));
      await page.goto(origin+'/knowledge');await page.locator('.knowledge-row').first().waitFor();
      assert.match(await page.locator('[data-knowledge-key="tool:delist-products-by-sku"] .knowledge-row-status').innerText(),/版本冲突/);
      assert.match(await page.locator('[data-knowledge-key="tool:manage-profit-settlement"] .knowledge-row-status').innerText(),/未安装/);
      await page.locator('[data-knowledge-key="tool:manage-seaya-replenishment"]').click();
      assert.match(await page.locator('.knowledge-skill-identity').innerText(),/BLOCKED_INVENTORY/);
      assert.match(await page.locator('.knowledge-skill-identity').innerText(),/junction/);
      assert.doesNotMatch(await page.locator('.knowledge-skill-identity').innerText(),/[CDE]:\//);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      const unknown={...identity,skills:Object.fromEntries(ids.map(id=>[id,{project:{registry_verified:false},
        installed:{state:'UNKNOWN',source_type:'unknown'},comparison:{status:'UNKNOWN'},execution_authority:false}]))};
      await context.route('**/api/orbit/skill-identity',route=>route.fulfill({contentType:'application/json',body:JSON.stringify(unknown)}));
      await page.reload();await page.locator('.knowledge-row').first().waitFor();
      await page.locator('[data-knowledge-key="tool:prepare-product-publication"]').click();
      assert.match(await page.locator('.knowledge-skill-identity').innerText(),/未核验/);
      assert.match(await page.locator('.knowledge-readiness').innerText(),/业务执行保持阻断/);
      // An unavailable identity check stays visible as unknown; it cannot turn into a green badge.
      await context.route('**/api/orbit/skill-identity',route=>route.fulfill({status:503,body:'{}'}));
      await page.reload();await page.locator('.knowledge-row').first().waitFor();
      await page.locator('[data-knowledge-key="tool:prepare-product-publication"]').click();
      assert.match(await page.locator('.knowledge-skill-identity').innerText(),/未核验/);
      record.views.push({width,checked:'match/conflict/missing/junction/fail-closed/no-overflow'});
      await context.close();
    }
    assert.deepEqual(record.errors,[]);assert.deepEqual(record.writes,[]);assert.deepEqual(record.external,[]);
    record.status='passed';
  }finally{fs.writeFileSync(path.join(output,'skill-identity-browser.json'),JSON.stringify(record,null,2));await browser.close();}
  console.log(JSON.stringify(record));
})().catch(error=>{console.error(error);process.exitCode=1;});
