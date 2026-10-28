/* Presentation-only fixture: original app/JS, no final decision or mutations. */
const {chromium}=require('playwright');
const assert=require('assert'),fs=require('fs');
const [base,out,offer,fixturePath]=process.argv.slice(2);
const fixture=JSON.parse(fs.readFileSync(fixturePath,'utf8'));
(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:1000}});
  const page=await context.newPage(),errors=[],posts=[],widths=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  await context.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(request.method()!=='GET'){
      posts.push(url.pathname);return route.abort();
    }
    if(request.resourceType()==='script'&&!url.pathname.endsWith('/publication_original_adapter.js'))return route.fulfill({status:200,contentType:'application/javascript',body:''});
    if(url.pathname==='/api/proxy-image'||request.resourceType()==='image'){
      return route.fulfill({status:200,contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160"><rect width="160" height="160" fill="#bed"/></svg>'});
    }
    assert(request.url().startsWith(base+'/'),'No external request');
    return route.continue();
  });
  try{
    await page.goto(base+'/product_workspace.html?offer_id='+offer+'&round=final');
    await page.waitForFunction(()=>window.OrbitOriginalReview);
    await page.evaluate(value=>{window.OrbitOriginalReview.render({product:{offer_id:value.offer_id,title:'Presentation fixture',revision:6,seller_sku_candidate:'2000431'}});window.OrbitOriginalReview.stages(value);},fixture);
    for(const width of [1440,390]){
      await page.setViewportSize({width,height:1000});
      const target=page.locator('#releaseCandidateTargets [data-listing-structure="ozon:RU"]');
      await target.scrollIntoViewIfNeeded();await target.waitFor({state:'visible'});
      const text=await target.innerText();
      assert(text.includes('3 条新 B 链接')&&text.includes('每件 1 片'));
      for(const sku of ['2000431','2000432','2000433'])assert(text.includes(sku+'-refrigerator-cover'));
      assert.strictEqual(await page.locator('#releaseCandidateTargets tbody tr').count(),15);
      assert.strictEqual(await page.locator('#releaseCandidateTargets [data-listing-structure]').count(),15);
      assert((await page.locator('#releaseCandidateChecks').innerText()).includes('其余 14 店'));
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
      await page.screenshot({path:out+'/ozon-structure-'+width+'.png'});widths.push(width);
    }
    assert.deepStrictEqual(errors,[]);assert.deepStrictEqual(posts,[]);
    process.stdout.write(JSON.stringify({widths,errors,posts,fixture_kind:'PRESENTATION_ONLY_15_TARGETS',provider_writes:0}));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
