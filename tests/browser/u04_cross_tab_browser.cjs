const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {chromium}=require('playwright');
const root=path.resolve(process.argv[2]),out=path.resolve(process.argv[3]);fs.mkdirSync(out,{recursive:false});
const dashboard=path.join(root,'domains/supply_chain_operations/dashboard');
const key='supply-chain-inbound-batch-timing-v3';
const batch={batchId:'TEST-BATCH-001',createdAt:'2026-09-01T00:00:00',estimatedAnchorAt:'2026-09-05T00:00:00',
 estimatedSellableDate:'2026-09-12',estimatedSellableConfirmedAt:'2026-09-05T12:00:00Z',totalUnits:3,transportDays:7,
 skuQuantities:{'0001':2,'0002':1},source:'synthetic complete batch detail'};
const data={snapshotDate:'2026-09-05',orderDemandCapturedAt:'2026-09-04T10:00:00Z',config:{TH:{warehouse:'TEST-WAREHOUSE'}}};
const plan={capturedAt:'2026-09-05T11:00:00Z',regions:{TH:{allocationPolicy:'EXACT_BATCH_SKU_REQUIRED',batches:[batch,{...batch,batchId:'TEST-BATCH-002'}]}}};
const requests=[];let servers=[];
async function start(){const server=http.createServer((req,res)=>{
 requests.push({method:req.method,url:req.url});if(req.method!=='GET'){res.writeHead(403);return res.end('read only');}
 const name=new URL(req.url,'http://local').pathname.split('/').pop()||'inbound-batches.html';
 if(name==='data.js'){res.setHeader('Content-Type','text/javascript');return res.end('window.SUPPLY_CHAIN_DATA='+JSON.stringify(data)+';');}
 if(name==='inbound-plan.js'){res.setHeader('Content-Type','text/javascript');return res.end('window.SUPPLY_CHAIN_INBOUND_PLAN='+JSON.stringify(plan)+';');}
 if(!['inbound-batches.html','inbound-batches.js','inbound-timeline.js','styles.css'].includes(name)){res.writeHead(404);return res.end();}
 res.setHeader('Content-Type',name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':'text/html');res.end(fs.readFileSync(path.join(dashboard,name)));
});await new Promise(r=>server.listen(0,'127.0.0.1',r));servers.push(server);return 'http://127.0.0.1:'+server.address().port;}
(async()=>{
 const origin=await start(),browser=await chromium.launch({headless:true,executablePath:process.env.ORBIT_BROWSER_EXECUTABLE});
 const context=await browser.newContext(),checks=[];
 await context.route('**/*',r=>new URL(r.request().url()).origin===origin?r.continue():r.abort());
 for(const bad of [false,true]){
  const a=await context.newPage();await a.goto(origin);await a.evaluate(()=>localStorage.clear());await a.reload();
  const b=await context.newPage();await b.goto(origin);
  async function fill(page,id,note){const row=page.locator(`tr[data-batch-id="${id}"]`);await row.locator('[name=anchorAt]').fill('2026-09-06T09:00');await row.locator('[name=estimatedSellableDate]').fill('2026-09-13');await row.locator('[name=sourceNote]').fill(note);return row;}
  const rowA=await fill(a,'TEST-BATCH-001','unsaved A draft');
  const rowB=await fill(b,'TEST-BATCH-002','B independent saved confirmation');await rowB.locator('[data-action=save]').click();
  await a.waitForFunction(()=>document.querySelector('#transferMessage').textContent.includes('其他页面'));
  await b.locator('.override-transfer summary').click();await b.locator('#exportOverrides').click();await b.waitForFunction(()=>document.querySelector('#exportText').value.length>0);
  await a.locator('.override-transfer summary').click();await a.locator('#importText').fill(bad?'bad JSON':await b.locator('#exportText').inputValue());await a.locator('#previewImport').click();await a.waitForFunction(()=>!document.querySelector('#previewImport').disabled);
  await rowA.locator('[data-action=save]').click();
  const values=await a.evaluate(()=>JSON.parse(localStorage.getItem('supply-chain-inbound-batch-timing-v4')).values);
  const preserved=values['TH:TEST-BATCH-002']?.sourceNote==='B independent saved confirmation';
  const draft=await rowA.locator('[name=sourceNote]').inputValue();
  checks.push({name:(bad?'failed':'valid')+' preview cannot advance manual write token over stale values',ok:preserved&&draft==='unsaved A draft',values,message:await a.locator('#pageMessage').textContent(),draft});
  await a.close();await b.close();
 }
 fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({checks,requests},null,2));
 await browser.close();await Promise.all(servers.map(s=>new Promise(r=>s.close(r))));console.log(JSON.stringify(checks));process.exitCode=checks.every(c=>c.ok)?0:1;
})().catch(e=>{console.error(e);process.exit(2);});
