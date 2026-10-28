const {chromium} = require('playwright');
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');
const vm = require('vm');

(async () => {
  const [url, dashboard, output] = process.argv.slice(2);
  fs.mkdirSync(output, {recursive:true});
  const readObject = name => {const window={}; vm.runInNewContext(fs.readFileSync(path.join(dashboard,name),'utf8'),{window}); return structuredClone(Object.values(window)[0]);};
  const seed = readObject('data.js');
  const plan = readObject('inbound-plan.js');
  seed.snapshotDate = '2026-09-30';
  seed.orderDemandCapturedAt = '2026-09-30T02:00:00Z';
  plan.capturedAt = '2026-09-30T02:00:00Z';
  for (const region of ['MY','TH','VN','PH']) {
    const row = structuredClone(seed.countries[region][0]);
    row.sku='0010'; row.name='SYNTHETIC decision boundary'; row.kind='first_stock';
    row.costCny=0; row.dimensionsCm=null; row.weightG=0;
    row.inventory={stock:0,available:0,allocated:0,frozen:0,inbound:0,warehouse:seed.config[region].warehouse};
    for (const [key,label] of [['tiktok','TikTok'],['shopee','Shopee']]) {
      row.channels[key]={days:30,units:key==='tiktok'?9:0,orders:0,recent30Units:key==='tiktok'?9:0,
        quantityBasis:'valid_order',state:'READY',source:`${label} ${region} synthetic`,
        settlementUnits:0,customerPayment:0,actualShippingFee:null,economicsBasis:'settlement_unavailable'};
    }
    seed.countries[region]=[row];
    seed.config[region].inventoryEvidence.capturedAt='2026-09-30T02:00:00Z';
    plan.regions[region]={batches:[],totalUnits:0,allocationPolicy:'NO_ACTIVE_BATCH'};
  }
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  const context=await browser.newContext({acceptDownloads:true,viewport:{width:1440,height:1000}});
  const page=await context.newPage();
  const errors=[]; let external=0;
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
    const u=new URL(route.request().url());
    if(u.origin!==new URL(url).origin){external++;return route.abort();}
    if(u.pathname.endsWith('/data.js')) return route.fulfill({contentType:'application/javascript',body:'window.SUPPLY_CHAIN_DATA = '+JSON.stringify(seed)+';'});
    if(u.pathname.endsWith('/inbound-plan.js')) return route.fulfill({contentType:'application/javascript',body:'window.SUPPLY_CHAIN_INBOUND_PLAN = '+JSON.stringify(plan)+';'});
    if(u.pathname.startsWith('/static/')) return route.fulfill({contentType:u.pathname.endsWith('.css')?'text/css':'application/javascript',body:''});
    return route.continue();
  });
  const report={fixture:'SYNTHETIC_NOT_BUSINESS_DATA',checks:[],externalRequests:0};
  try {
    await page.goto(url+'/#region=SUMMARY');
    await page.locator('#skuRows .sku-summary').first().waitFor();
    assert.equal(await page.locator('.ledger-group thead th').first().textContent(),'国家');
    report.checks.push('country first');
    const row=page.locator('.sku-summary[data-country="TH"][data-sku="0010"]');
    await row.waitFor();
    assert.equal(await row.locator('[data-decision-field="quantity"]').inputValue(),'10');
    report.checks.push('exact 10 retained; missing cost does not block quantity');
    const prior=await row.locator('.recommendation').textContent();
    await row.locator('[data-decision-field="quantity"]').fill('17');
    await row.locator('[data-decision-field="quantity"]').press('Tab');
    await row.locator('[data-decision-field="note"]').fill('估算包装待核；人工计划17件');
    await row.locator('[data-decision-field="note"]').press('Tab');
    await page.reload(); await row.waitFor();
    assert.equal(await row.locator('[data-decision-field="quantity"]').inputValue(),'17');
    assert.match(await row.locator('[data-decision-field="note"]').inputValue(),/人工计划17件/);
    assert.equal(await row.locator('.recommendation').textContent(),prior);
    report.checks.push('local edit survives reload without changing either scenario');
    const downloadPromise=page.waitForEvent('download');
    await page.locator('#exportDecisionSheet').click();
    const download=await downloadPromise; const exported=path.join(output,'decision-sheet.html');
    await download.saveAs(exported);
    const html=fs.readFileSync(exported,'utf8');
    assert.match(html,/<th>国家<\/th>/); assert.match(html,/data:image\//);
    assert.match(html,/人工计划17件/); assert.match(html,/value="17"/);
    report.checks.push('single editable export embeds images, quantities, notes and source date');
    const sheet=await context.newPage();
    await sheet.goto('file:///'+exported.replace(/\\/g,'/'));
    assert.equal(await sheet.locator('table').count(),1);
    const portableRow=sheet.locator('tbody tr').filter({has:sheet.getByRole('cell',{name:'TH',exact:true})});
    await portableRow.locator('input').fill('23');
    await portableRow.locator('textarea').fill('</textarea><script>window.UNSAFE=true</script> 估算待核');
    const editedDownload=sheet.waitForEvent('download');
    await sheet.locator('#saveSheet').click();
    const editedPath=path.join(output,'decision-sheet-edited.html');
    await (await editedDownload).saveAs(editedPath);
    await sheet.goto('file:///'+editedPath.replace(/\\/g,'/'));
    assert.equal(await portableRow.locator('input').inputValue(),'23');
    assert.equal(await portableRow.locator('small').textContent(),'人工调整');
    assert.match(await portableRow.locator('textarea').inputValue(),/window.UNSAFE/);
    assert.equal(await sheet.evaluate(()=>window.UNSAFE),undefined);
    await sheet.close();
    report.checks.push('portable edits survive re-download; note markup stays inert');
    const token=await page.evaluate(()=>localStorage.getItem('supply-chain-decision-sheet-v1'));
    seed.orderDemandCapturedAt='2026-10-01T02:00:00Z';
    await page.reload(); await row.waitFor();
    assert.equal(await row.locator('[data-decision-field="quantity"]').inputValue(),'10');
    assert.equal(await page.evaluate(()=>localStorage.getItem('supply-chain-decision-sheet-v1')),token);
    report.checks.push('stale local edit excluded; stored evidence preserved');
    await row.locator('[data-decision-field="quantity"]').fill('-1');
    await row.locator('[data-decision-field="quantity"]').press('Tab');
    assert.equal(await page.evaluate(()=>localStorage.getItem('supply-chain-decision-sheet-v1')),token);
    await row.locator('[data-decision-field="quantity"]').fill('0');
    await row.locator('[data-decision-field="quantity"]').press('Tab');
    assert.equal(await row.locator('[data-decision-field="quantity"]').inputValue(),'0');
    report.checks.push('invalid quantities do not save; explicit zero stays visible');
    const other=await context.newPage();await other.goto(url);
    const concurrent='{"schema":1,"rows":{},"otherEditor":true}';
    await other.evaluate(value=>localStorage.setItem('supply-chain-decision-sheet-v1',value),concurrent);
    await row.locator('[data-decision-field="quantity"]').fill('19');
    await row.locator('[data-decision-field="quantity"]').press('Tab');
    assert.equal(await page.evaluate(()=>localStorage.getItem('supply-chain-decision-sheet-v1')),concurrent);
    assert.match(await page.locator('#decisionSaveStatus').textContent(),/未保存草稿/);
    assert.equal(await row.locator('[data-decision-field="quantity"]').inputValue(),'19');
    await other.close();
    report.checks.push('another editor blocks stale save and retains visible draft');
    await page.reload();await row.waitFor();
    await page.evaluate(()=>{
      window.originalStorageSetItem=Storage.prototype.setItem;
      Storage.prototype.setItem=function(key,value){
        if(key==='supply-chain-decision-sheet-v1')throw new DOMException('synthetic quota','QuotaExceededError');
        return window.originalStorageSetItem.call(this,key,value);
      };
    });
    await row.locator('[data-decision-field="quantity"]').fill('25');
    await row.locator('[data-decision-field="quantity"]').press('Tab');
    assert.equal(await page.evaluate(()=>localStorage.getItem('supply-chain-decision-sheet-v1')),concurrent);
    assert.match(await page.locator('#decisionSaveStatus').textContent(),/未保存草稿/);
    assert.equal(await row.locator('[data-decision-field="quantity"]').inputValue(),'25');
    const draftDownload=page.waitForEvent('download');await page.locator('#exportDecisionSheet').click();
    const draftPath=path.join(output,'decision-sheet-unsaved-draft.html');await (await draftDownload).saveAs(draftPath);
    assert.match(fs.readFileSync(draftPath,'utf8'),/value="25"/);
    await page.evaluate(()=>{Storage.prototype.setItem=window.originalStorageSetItem;delete window.originalStorageSetItem;});
    report.checks.push('storage failure preserves confirmed store and exports visible unsaved draft');
    await row.locator('.sku-detail-toggle').click();
    await page.locator('#sku-detail-TH-0010 [data-action="manual-entry"]').click();
    for(const name of ['lengthCm','widthCm','heightCm','weightG','costCny']) await page.locator(`[name="${name}"]`).fill('2');
    await page.locator('[name="sourceNote"]').fill('参考同规格包装估算，待实测');
    await page.locator('#manualInputForm button[type="submit"]').click();
    assert.match(await row.textContent(),/估算 \/ 手动参数待核/);
    assert.equal(await row.locator('.recommendation').textContent(),prior);
    report.checks.push('estimated package/cost labelled without changing quantity forecast');
    await page.setViewportSize({width:390,height:844});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1));
    assert.deepEqual(errors,[]); assert.equal(external,0);
    report.checks.push('mobile no page overflow; no runtime error; no external request');
    await page.setViewportSize({width:1440,height:1000});
    await page.screenshot({path:path.join(output,'decision-sheet.png'),fullPage:true});
    report.ok=true;
  } catch(e){report.ok=false;report.error=e.message;throw e;}
  finally {report.externalRequests=external;fs.writeFileSync(path.join(output,'browser-report.json'),JSON.stringify(report,null,2));await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
