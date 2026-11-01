"""Current owner-verified R2 images remain visible before final-category readiness."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from test_round1_workspace_freeze import live
from test_release_ux_contract import _browser_runtime
from owned_new_publication_task import complete_new_task
from modules.products import server
from shared_platform.publication_prepared_image_review import project_prepared_images


ROOT = Path(__file__).resolve().parents[1]
BROWSER = r'''
const fs=require('fs'),path=require('path'),assert=require('assert'),crypto=require('crypto');
const {chromium}=require('playwright');
(async()=>{
 const root=process.argv[2],out=process.argv[3],input=JSON.parse(fs.readFileSync(path.join(out,'prepared-input.json')));
 const assets=new Map(input.assets.map(a=>[a.url,a]));
 const browser=await chromium.launch({headless:true,executablePath:process.env.ORBIT_BROWSER_EXECUTABLE||process.env.ORBIT_CHROMIUM_BIN});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],mutations=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
   const req=route.request(),url=new URL(req.url());
   if(req.method()!=='GET'){mutations.push(req.method()+' '+url.pathname);return route.abort();}
   if(url.hostname!=='owned-prepared.test')return route.abort();
   if(url.pathname==='/product-workspace'){
    const html=fs.readFileSync(path.join(root,'web/product_workspace.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,tag=>tag.includes('publication_original_adapter.js')?tag:'');
    return route.fulfill({contentType:'text/html',body:html});
   }
   if(url.pathname.startsWith('/static/'))return route.fulfill({path:path.join(root,'web',url.pathname)});
   if(url.pathname==='/api/proxy-image'){
    const asset=assets.get(url.searchParams.get('url'));assert(asset,'non-owned image requested');
    const bytes=fs.readFileSync(asset.path);assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'),asset.sha256);
    return route.fulfill({body:bytes,contentType:'image/png'});
   }
   return route.abort();
  });
  await page.goto('http://owned-prepared.test/product-workspace?offer_id='+input.stage.offer_id);
  await page.evaluate(stage=>{
   document.querySelector('#productDetail').hidden=false;
   for(const pane of document.querySelectorAll('[data-product-pane]'))pane.hidden=pane.id!=='pane-images';
   window.OrbitOriginalReview.render({product:{offer_id:stage.offer_id,title:'Owned source title'}});
   window.OrbitOriginalReview.stages(stage);
  },input.stage);
  const panel=page.locator('#nativePreparedImageContent');await panel.waitFor({state:'visible'});
  await page.waitForFunction(()=>{
   const images=[...document.querySelectorAll('[data-native-prepared-image]')];
   return images.length>=14&&images.every(i=>i.complete&&i.naturalWidth>0);
  });
  const actual=await panel.locator('img').evaluateAll(images=>images.map(i=>new URL(i.src).searchParams.get('url')));
  assert.deepEqual(actual,input.stage.prepared_images.brand_groups.flatMap(g=>g.images.map(i=>i.url)));
  assert.equal(await panel.locator('.native-prepared-gallery').count(),2);
  assert.equal(await page.locator('#embeddedImageReview').isVisible(),false);
  assert.equal(await page.locator('#firstReviewImagePlan').isVisible(),false);
  assert.equal(await page.locator('#nativeFinalFrozenContent').count(),0);
  assert.equal(await panel.locator('button,input').count(),0);
  const drift=structuredClone(input.stage);drift.prepared_images.offer_id='999999999';
  await page.evaluate(stage=>window.OrbitOriginalReview.stages(stage),drift);
  assert.equal(await page.locator('#nativePreparedImageContent').count(),0);
  assert.deepEqual(errors,[]);assert.deepEqual(mutations,[]);
  fs.writeFileSync(path.join(out,'prepared-browser-result.json'),JSON.stringify({status:'PASS_OWNED_PRE_FINAL_DISPLAY',image_count:actual.length,page_errors:errors,non_GET_requests:mutations},null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
'''


def test_original_get_projects_current_r2_before_final_candidate_and_browser_shows_exact_bytes(live, monkeypatch, tmp_path):
    runtime, market, _, calls = complete_new_task(live, monkeypatch, tmp_path)
    try:
        before = list(calls)
        code, stage = server._publication_stages_for_request({'offer_id': market['product_id']})
        assert code == 200, stage
        assert calls == before, 'GET must not dispatch a provider request'
        prepared = stage['prepared_images']
        assert prepared['execution_authority'] is False
        assert prepared['publication_approval_recorded'] is False
        assert prepared['image_count'] >= 14
        assert len(prepared['brand_groups']) == 2
        documents = server._load_r2_documents_for_service(stage['offer_id'])
        assert prepared == project_prepared_images(documents)
        forged = deepcopy(documents)
        forged['generation_result']['assets'][0]['artifact_digest'] = 'sha256:' + '0' * 64
        with pytest.raises(ValueError):
            project_prepared_images(forged)
        assets = [{'url': a['public_url'], 'path': a['artifact_path'],
                   'sha256': hashlib.sha256(Path(a['artifact_path']).read_bytes()).hexdigest()}
                  for a in documents['generation_result']['assets']]
        # Keep the real GET's exact verified images and COMMON; only the owned
        # marketplace dependency is represented as not yet ready in this UI case.
        stage['marketplace'] = {'status': 'BLOCKED', 'blockers': ['OWNED_CATEGORY_METADATA_PENDING']}
        (tmp_path/'prepared-input.json').write_text(json.dumps({'stage': stage, 'assets': assets}), encoding='utf-8')
        script = tmp_path/'prepared-browser.cjs'
        script.write_text(BROWSER, encoding='utf-8')
        available = _browser_runtime()
        assert available, 'Real Chromium required'
        node, modules = available
        result = subprocess.run([str(node), str(script), str(ROOT), str(tmp_path)],
            env=dict(os.environ, NODE_PATH=str(modules)), capture_output=True,
            text=True, encoding='utf-8', timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        runtime.close()
