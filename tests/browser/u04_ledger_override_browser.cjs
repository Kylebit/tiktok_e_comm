const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const base=process.argv[2],out=path.resolve(process.argv[3]);fs.mkdirSync(out);
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.ORBIT_BROWSER_EXECUTABLE});
 const context=await browser.newContext({viewport:{width:1440,height:1000}}),checks=[],errors=[];
 await context.route('**/*',r=>new URL(r.request().url()).origin===new URL(base).origin?r.continue():r.abort());
 const ledger=await context.newPage(),batch=await context.newPage();for(const p of [ledger,batch])p.on('pageerror',e=>errors.push(String(e)));
 await ledger.goto(base+'supply-chain/#region=MY');await batch.goto(base+'supply-chain/inbound-batches.html#region=MY');
 async function check(name,fn){try{await fn();checks.push({name,ok:true});}catch(e){checks.push({name,ok:false,error:String(e)});}}
 await check('ledger baseline counts the exact synthetic batch',async()=>assert.equal(await ledger.evaluate(()=>calculated[0].countedInbound),3));
 await batch.locator('[name=anchorAt]').fill('2026-09-06T09:00');await batch.locator('[name=estimatedSellableDate]').fill('2027-01-01');await batch.locator('[name=sourceNote]').fill('synthetic delayed full batch');await batch.locator('[data-action=save]').click();
 await check('real storage event updates open ledger from persisted batch confirmation',async()=>{await ledger.waitForFunction(()=>calculated[0].countedInbound===0);assert.match(await ledger.locator('#skuRows').textContent(),/2027-01-01/);});
 await batch.screenshot({path:path.join(out,'batch-desktop.png'),fullPage:true});
 await batch.locator('[data-action=clear]').click();
 await check('clearing confirmation restores both real ledger scenarios',async()=>{await ledger.waitForFunction(()=>calculated[0].countedInbound===3);assert.equal(await ledger.locator('.scenario-audit').count(),2);assert.deepEqual(errors,[]);});
 await batch.locator('.override-transfer summary').click();await batch.setViewportSize({width:390,height:844});await batch.screenshot({path:path.join(out,'batch-mobile.png'),fullPage:true});
 fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({checks,errors,base},null,2));await browser.close();console.log(JSON.stringify(checks));process.exitCode=checks.every(c=>c.ok)?0:1;
})().catch(e=>{console.error(e);process.exit(2);});
