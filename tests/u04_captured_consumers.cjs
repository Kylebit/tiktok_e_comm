const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),assert=require('node:assert/strict');
const [root,output,mode]=process.argv.slice(2),context=vm.createContext({crypto:require('node:crypto').webcrypto,TextEncoder,URL});
context.window=context;
for(const file of ['data.js','inbound-plan.js'])vm.runInContext(fs.readFileSync(path.join(output,file),'utf8'),context,{filename:file});
const consumer=path.join(root,'domains/supply_chain_operations/dashboard/inbound-timeline.js');
vm.runInContext(fs.readFileSync(consumer,'utf8'),context,{filename:consumer});
const data=context.SUPPLY_CHAIN_DATA,plan=context.SUPPLY_CHAIN_INBOUND_PLAN,api=context.SUPPLY_CHAIN_OVERRIDES,results=[];
const storageFile=path.join(output,'saved-storage.json');
const storage={getItem:key=>fs.existsSync(storageFile)?JSON.parse(fs.readFileSync(storageFile,'utf8'))[key]??null:null,
 setItem:(key,value)=>fs.writeFileSync(storageFile,JSON.stringify({[key]:value}))};
(async()=>{
 if(mode==='save'){
  const values={};
  for(const region of ['MY','TH','VN','PH']){
   const batch={...plan.regions[region].batches[0],region};
   values[`${region}:${batch.batchId}`]={anchorAt:'2026-09-06T09:00',estimatedSellableDate:'2026-09-13',sourceNote:'saved before refresh',updatedAt:'2026-09-06T10:00:00Z',binding:api.basis(batch,data,plan)};
  }
  const saved=api.write(storage,api.read(storage),values);
  const exported=await api.exportBundle(saved.values,data,plan,'http://localhost');
  assert.equal(exported.excluded.length,0);
  fs.writeFileSync(path.join(output,'saved-bundle.json'),JSON.stringify(exported.bundle));
  return;
 }
 const stored=fs.readFileSync(storageFile),previous=api.read(storage);
 const preview=await api.preview(fs.readFileSync(path.join(output,'saved-bundle.json'),'utf8'),previous,data,plan);
 for(const region of ['MY','TH','VN','PH']){
  const batch={...plan.regions[region].batches[0],region},row=data.countries[region][0],key=`${region}:${batch.batchId}`;
  const saved=previous.values[key],accepted=api.current(saved,batch,data,plan),status=preview.rows.find(r=>r.key===key).status;
  assert.equal(status,mode==='changed'?'STALE_PLAN':'DUPLICATE');
  if(mode==='changed')assert.equal(accepted,null);else assert.equal(accepted,saved);
  const projection=context.SUPPLY_CHAIN_TIMELINE.projectSupply({snapshotDate:data.snapshotDate,nextArrivalDate:'2026-10-01',available:row.inventory.available,dailyVelocity:(row.channels.tiktok.recent30Units+row.channels.shopee.recent30Units)/30,inboundEvents:[{batchId:batch.batchId,quantity:batch.skuQuantities[row.sku],estimatedSellableDate:accepted?.estimatedSellableDate??batch.estimatedSellableDate}]});
  assert.equal(projection.projectionMethod,'TIME_PHASED_BATCH_EVENTS_V1');assert.ok(projection.steps.length>1);
  results.push({region,status,manualRetained:saved.sourceNote,projection});
 }
 assert.deepEqual(fs.readFileSync(storageFile),stored);
 fs.writeFileSync(path.join(output,'consumer-result.json'),JSON.stringify({consumer,mode,layer:'actual Node VM consumers with file-backed storage; not browser localStorage',results},null,2));
})().catch(error=>{console.error(error);process.exitCode=1;});
