"""New-task frozen gallery display with real R1/R2 bytes in Chromium.

The graph, getter and image files are original producers under an owned test
root. Only the browser's media transport is fulfilled locally. This is a
display acceptance, not deployment or official marketplace verification.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess

from test_round1_workspace_freeze import live
from test_round1_auto_freeze import public_settings
from test_release_ux_contract import _browser_runtime
from owned_new_publication_task import complete_new_task
from modules.products import server
from shared_platform import publication_r3_image_bridge as bridge


ROOT = Path(__file__).resolve().parents[1]

BROWSER = r'''
const fs=require('fs'),path=require('path'),assert=require('assert'),crypto=require('crypto');
const {chromium}=require('playwright');
(async()=>{
 const root=process.argv[2],out=process.argv[3];
 const input=JSON.parse(fs.readFileSync(path.join(out,'gallery-input.json'))),stage=input.stage;
 const assets=new Map(input.assets.map(a=>[a.url,a]));
 const browser=await chromium.launch({headless:true,executablePath:process.env.ORBIT_BROWSER_EXECUTABLE||process.env.ORBIT_CHROMIUM_BIN});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],mutations=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());
   if(request.method()!=='GET'){mutations.push(request.method()+' '+url.pathname);return route.abort();}
   if(url.hostname!=='owned-gallery.test')return route.abort();
   if(url.pathname==='/product-workspace'){
    let html=fs.readFileSync(path.join(root,'web/product_workspace.html'),'utf8');
    html=html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,tag=>tag.includes('publication_original_adapter.js')?tag:'');
    return route.fulfill({contentType:'text/html',body:html});
   }
   if(url.pathname.startsWith('/static/'))return route.fulfill({path:path.join(root,'web',url.pathname)});
   if(url.pathname==='/api/proxy-image'){
    const asset=assets.get(url.searchParams.get('url'));assert(asset,'non-frozen image requested');
    const bytes=fs.readFileSync(asset.path);assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'),asset.sha256);
    return route.fulfill({body:bytes,contentType:'image/png'});
   }
   return route.abort();
  });
  await page.goto('http://owned-gallery.test/product-workspace?offer_id='+stage.offer_id+'&round=final');
  await page.evaluate(stage=>{
   document.querySelector('#productDetail').hidden=false;document.querySelector('#noProductSelected').hidden=true;
   for(const pane of document.querySelectorAll('[data-product-pane]'))pane.hidden=pane.id!=='pane-images';
   document.querySelector('#tab-images').setAttribute('aria-selected','true');
   window.OrbitOriginalReview.render({product:{offer_id:stage.offer_id,title:'Owned new three-model fixture'}});
   window.OrbitOriginalReview.stages(stage);
  },stage);
  const panel=page.locator('#nativeFinalFrozenContent');await panel.waitFor({state:'visible',timeout:10000});
  await page.waitForFunction(()=>{
   const images=[...document.querySelectorAll('[data-native-frozen-image]')];
   return images.length>0&&images.every(i=>i.complete&&i.naturalWidth>0);
  },null,{timeout:15000});
  const manifest=stage.marketplace.preview.review_manifest,sets=new Map();
  for(const set of manifest.image_sets)sets.set(JSON.stringify(set.images),set.images);
  const expected=[...sets.values()].flat().map(i=>i.url);
  const actual=await panel.locator('.native-frozen-gallery img').evaluateAll(images=>images.map(i=>new URL(i.src).searchParams.get('url')));
  assert.deepEqual(actual,expected);assert(actual.length>=14,'dual-brand masters must not collapse to one or two images');
  assert.equal(await panel.locator('[data-native-variant]').count(),3);
  assert.equal(await panel.locator('[data-native-copy-set]').count(),manifest.copy_sets.length);
  assert.equal(await page.locator('#embeddedImageReview').isVisible(),false);
  assert.equal(await page.locator('#saveEmbeddedImageReviewButton').isVisible(),false);
  await panel.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'NEW_FROZEN_GALLERY.png')});
  await page.evaluate(s=>{s.marketplace.native_final_review.candidate_digest='wrong';window.OrbitOriginalReview.stages(s);},structuredClone(stage));
  assert.equal(await page.locator('#nativeFinalFrozenContent').count(),0,'changed candidate must not borrow gallery');
  assert.deepEqual(errors,[]);assert.deepEqual(mutations,[]);
  fs.writeFileSync(path.join(out,'gallery-browser-result.json'),JSON.stringify({status:'PASS_OWNED_DISPLAY',
   candidate_digest:stage.marketplace.preview.candidate_digest,gallery_urls:actual,variants:3,
   page_errors:errors,non_GET_requests:mutations,approval_clicks:0,deployed_runtime_verified:false},null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
'''


def test_new_three_model_full_frozen_gallery_uses_exact_r2_bytes_in_chromium(live, monkeypatch, tmp_path):
    runtime, market, _, _ = complete_new_task(live, monkeypatch, tmp_path)
    try:
        code, stage = server._publication_stages_for_request({'offer_id': market['product_id']})
        assert code == 200 and stage['marketplace']['final_review_available'], stage
        documents = bridge.load_r2_documents(market['product_id'])
        assets = {}

        def collect(value):
            if isinstance(value, dict):
                if {'artifact_path', 'artifact_digest', 'public_url'} <= value.keys():
                    path = Path(value['artifact_path'])
                    assert path.resolve().is_relative_to(live['root'].resolve())
                    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
                    assert checksum == value['artifact_digest'].removeprefix('sha256:')
                    item = {'url': value['public_url'], 'path': str(path), 'sha256': checksum}
                    assert item['url'] not in assets or assets[item['url']] == item
                    assets[item['url']] = item
                for part in value.values():
                    collect(part)
            elif isinstance(value, list):
                for part in value:
                    collect(part)

        collect(documents)
        assert len(assets) >= 14
        (tmp_path/'gallery-input.json').write_text(json.dumps({'stage': stage, 'assets': list(assets.values())}), encoding='utf-8')
        script = tmp_path/'gallery-browser.cjs'
        script.write_text(BROWSER, encoding='utf-8')
        available = _browser_runtime()
        assert available, 'Real Chromium required for frozen-gallery acceptance'
        node, modules = available
        result = subprocess.run([str(node), str(script), str(ROOT), str(tmp_path)],
            env=dict(os.environ, NODE_PATH=str(modules)), capture_output=True, text=True, encoding='utf-8', timeout=80)
        assert result.returncode == 0, result.stdout + result.stderr
        proof = json.loads((tmp_path/'gallery-browser-result.json').read_bytes())
        assert proof['status'] == 'PASS_OWNED_DISPLAY' and proof['non_GET_requests'] == []
    finally:
        runtime.close()
