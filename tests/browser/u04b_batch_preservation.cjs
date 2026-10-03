const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');const{chromium}=require('playwright');
const base=process.argv[2],out=process.argv[3];
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.ORBIT_BROWSER_EXECUTABLE}),context=await browser.newContext(),checks=[];
 await context.route('**/*',r=>new URL(r.request().url()).origin===new URL(base).origin?r.continue():r.abort());
 const a=await context.newPage(),b=await context.newPage();await a.goto(base+'supply-chain/inbound-batches.html');await b.goto(base+'supply-chain/inbound-batches.html');
 async function fill(page,region,note){const row=page.locator(`#batchRows tr[data-region="${region}"]`);await row.locator('[name=anchorAt]').fill('2026-09-06T09:00');await row.locator('[name=estimatedSellableDate]').fill('2026-10-13');await row.locator('[name=sourceNote]').fill(note);return row;}
 const rowA=await fill(a,'MY','preserved draft MY'),rowB=await fill(b,'VN','other tab VN saved');await rowB.locator('[data-action=save]').click();
 await a.waitForFunction(()=>document.querySelector('#transferMessage').textContent.includes('其他页面'));
 await a.locator('.override-transfer summary').click();await a.locator('#importText').fill('bad JSON');await a.locator('#previewImport').click();await a.waitForFunction(()=>!document.querySelector('#previewImport').disabled);await rowA.locator('[data-action=save]').click();
 assert.match(await a.locator('#pageMessage').textContent(),/尚未保存/);assert.equal(await rowA.locator('[name=sourceNote]').inputValue(),'preserved draft MY');
 assert.equal(await a.evaluate(()=>JSON.parse(localStorage.getItem('supply-chain-inbound-batch-timing-v4')).values['VN:VN-SYNTHETIC-BATCH'].sourceNote),'other tab VN saved');checks.push('real Handler two-tab stale write rejected and draft plus other batch preserved');
 await b.locator('.override-transfer summary').click();await b.locator('#exportOverrides').click();await b.waitForFunction(()=>document.querySelector('#exportText').value.length>0);const exported=await b.locator('#exportText').inputValue();
 const targetContext=await browser.newContext();await targetContext.route('**/*',r=>new URL(r.request().url()).origin===new URL(base).origin?r.continue():r.abort());const target=await targetContext.newPage();await target.goto(base+'supply-chain/inbound-batches.html');await target.locator('.override-transfer summary').click();await target.locator('#importText').fill(exported);await target.locator('#previewImport').click();await target.waitForFunction(()=>!document.querySelector('#previewImport').disabled);
 assert.equal(await target.evaluate(()=>localStorage.getItem('supply-chain-inbound-batch-timing-v4')),null);await target.locator('#applyImport').click();assert.match(await target.locator('#transferMessage').textContent(),/已导入 1 项/);checks.push('real batch export and isolated-context import preview then persistence');
 await target.locator('#undoImport').click();assert.equal(await target.locator('#confirmedCount').textContent(),'0 批');checks.push('real batch undo restores preceding destination');
 fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({base,checks,scope:'real Handler; fresh isolated browser contexts; no personal storage'},null,2));await browser.close();console.log(JSON.stringify(checks));
})().catch(e=>{console.error(e);process.exit(1);});
