"""Focused display regression and new UI-source seams, not a replay of v1-v6.

The browser uses the actual frozen candidate plus already verified local R2
bytes in an isolated component. It is not deployed-runtime acceptance.
"""
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import hmac,json,os,subprocess
from pathlib import Path
from types import SimpleNamespace
import pytest

from shared_platform import agent_prepared_source_successor as source,agent_prepared_publication_adoption as adoption
from shared_platform.operations_runtime import RuntimeProfile,runtime_manifest

ROOT=Path(__file__).resolve().parents[1]


def git(root,*args):
    return subprocess.check_output(['git','-C',str(root),'-c','user.name=Orbit Hive Codex',
        '-c','user.email=codex@orbit-hive.local',*args],text=True,stderr=subprocess.DEVNULL).strip()


@pytest.fixture
def segments(tmp_path):
    root=tmp_path/'source';root.mkdir();git(root,'init')
    for name in source.UI_MECHANISM_FILES|source.UI_FILES:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('original\n')
    git(root,'add','.');git(root,'commit','-m','original completed source')
    old=dict(code_version=git(root,'rev-parse','HEAD'),environment='stable',manifest_digest=runtime_manifest(root))
    for name in source.UI_MECHANISM_FILES:(root/name).write_text('reviewed mechanism\n')
    git(root,'add','.');git(root,'commit','-m','bounded UI source mechanism')
    mechanism=dict(code_version=git(root,'rev-parse','HEAD'),environment='stable',manifest_digest=runtime_manifest(root))
    for name in source.UI_FILES:(root/name).write_text('frozen image presentation\n')
    git(root,'add','.');git(root,'commit','-m','frontend only')
    return root,old,mechanism,RuntimeProfile(root,tmp_path/'operations','stable',git(root,'rev-parse','HEAD'),runtime_manifest(root))


def test_exact_two_git_segments_reject_backend_or_unrelated_frontend_changes(segments):
    root,old,mechanism,profile=segments
    proof=source._ui_source_segments(profile,old,mechanism)
    assert set(proof['frontend']['changed_files'])==source.UI_FILES
    assert proof['frontend']['new_version']['manifest_digest']==mechanism['manifest_digest']
    (root/'shared_platform/prepared_target_consumer.py').write_text('backend slipped after bootstrap\n')
    git(root,'add','.');git(root,'commit','-m','forbidden second-segment backend')
    changed=RuntimeProfile(root,profile.data_root,'stable',git(root,'rev-parse','HEAD'),runtime_manifest(root))
    with pytest.raises(adoption.AdoptionBlocked):source._ui_source_segments(changed,old,mechanism)


def test_ui_source_real_engine_cas_hmac_once_preserves_old_events(segments,monkeypatch,tmp_path):
    """Old chain verification is a boundary stub here; Git/HMAC/CAS are real."""
    from shared_platform.workbench_engine import WorkbenchEngine
    from shared_platform import r3_native_final_approval as actor,release_store
    root,old,mechanism,profile=segments;key=b'local-ui-test-signature-key-32bytes';sid='test-owner'
    oldengine=WorkbenchEngine(profile.data_root/'tasks.db',old)
    task_id=oldengine.create(dict(template='publication',source_key='UI-test',scope=dict(offer_id='8412287877065586543',shops=['ozon:RU'],skus=['0431'])))['task_id']
    original=dict(schema_version=adoption.SCHEMA,owner_sid=sid,task_id=task_id,offer_id='8412287877065586543')
    raw=adoption._bytes(original);original_digest=sha256(raw).hexdigest()
    prior=dict(source=dict(new_version=old),preserved={'common_history_digest':'unchanged'},api_target_selection={'reduction_digest':'api9-unchanged'})
    with oldengine.transaction() as db:
        db.execute('CREATE TABLE agent_prepared_publication_adoptions (task_id TEXT,record_json TEXT,record_digest TEXT,signature TEXT)')
        db.execute('INSERT INTO agent_prepared_publication_adoptions VALUES (?,?,?,?)',(task_id,raw.decode(),original_digest,hmac.new(key,raw,'sha256').hexdigest()))
        db.execute("UPDATE workbench_execution SET state='waiting_domain',step_index=2,action_json=?,checkpoint_json=? WHERE task_id=?",
            ('{"action_id":"original","kind":"observe"}','{"frozen":"original"}',task_id))
        for n in (1,2):oldengine._event(db,task_id,source.GALLERY_EVENT,dict(record_digest='old-gallery-'+str(n)))
        before=dict(oldengine._row(db,task_id));old_events=[tuple(r) for r in db.execute('SELECT * FROM workbench_events')]
    engine=WorkbenchEngine(profile.data_root/'tasks.db',source._version(profile))
    frozen=dict(candidate_digest='c5-frozen',plan_id='same-plan',native_review_digest='same-review')
    request=tmp_path/'request.json';request.write_text(json.dumps(dict(schema_version='orbit-user-ui-defect-and-fix-scope/v1',
        task_id=task_id,offer_id=original['offer_id'],preserve_candidate_digest='c5-frozen',preserve_plan_id='same-plan',
        preserve_native_review_digest='same-review',local_ui_source_repair=True,original_gallery_event_limit=2,
        marketplace_final_approval=False,new_paid_images=False,new_COMMON_or_create=False,merchant_writes=False,new_credential_authority=False)))
    pins=dict(path=str(request),sha256=sha256(request.read_bytes()).hexdigest())
    monkeypatch.setattr(adoption,'_owner_key',lambda p:(sid,key))
    monkeypatch.setattr(actor,'read_native_actor_identity',lambda _:dict(identity_verified=True,trust_mode='SAME_WINDOWS_USER',owner_sid=sid))
    monkeypatch.setattr(source,'verify_gallery_source',lambda *a,**kw:deepcopy(prior))
    monkeypatch.setattr(source,'_ui_frozen_candidate',lambda *a,**kw:deepcopy(frozen))
    monkeypatch.setattr(release_store,'default_release_store',lambda:object())
    kwargs=dict(task_id=task_id,expected_adoption_digest=original_digest,actor_reader=object(),
        mechanism_head=mechanism['code_version'],candidate_digest='c5-frozen',user_request=pins)
    # A concurrent action change must fail the complete same-row CAS.
    def raced(*a,**kw):
        with engine.transaction() as db:db.execute("UPDATE workbench_execution SET action_json='{}' WHERE task_id=?",(task_id,))
        return deepcopy(frozen)
    monkeypatch.setattr(source,'_ui_frozen_candidate',raced)
    with pytest.raises(adoption.AdoptionBlocked,match='NATIVE_FINAL_UI_CAS_CHANGED'):
        source.continue_native_final_image_ui(engine,profile,object(),**kwargs)
    with engine.transaction() as db:
        assert not db.execute('SELECT 1 FROM workbench_events WHERE event_type=?',(source.UI_EVENT,)).fetchone()
        db.execute('UPDATE workbench_execution SET action_json=? WHERE task_id=?',(before['action_json'],task_id))
    monkeypatch.setattr(source,'_ui_frozen_candidate',lambda *a,**kw:deepcopy(frozen))
    with pytest.raises(adoption.AdoptionBlocked,match='EXACT_CANDIDATE'):
        source.continue_native_final_image_ui(engine,profile,object(),**{**kwargs,'candidate_digest':'wrong'})
    record=source.continue_native_final_image_ui(engine,profile,object(),**kwargs)
    with engine.transaction() as db:
        after=dict(engine._row(db,task_id));assert {k:v for k,v in after.items() if k!='version_json'}=={k:v for k,v in before.items() if k!='version_json'}
        assert [tuple(r) for r in db.execute('SELECT * FROM workbench_events ORDER BY id').fetchall()[:-1]]==old_events
        events=db.execute('SELECT detail_json FROM workbench_events WHERE event_type=?',(source.GALLERY_EVENT,)).fetchall()
        current=engine._project(db,engine._row(db,task_id))
        assert source.verify_completed_gallery_current(current,profile,original,connection=db,owner_sid=sid,key=key,events=events)==prior
        envelope=json.loads(db.execute('SELECT detail_json FROM workbench_events WHERE event_type=?',(source.UI_EVENT,)).fetchone()[0])
        envelope['signature']='0'*64
        db.execute('UPDATE workbench_events SET detail_json=? WHERE event_type=?',(json.dumps(envelope),source.UI_EVENT))
        with pytest.raises(adoption.AdoptionBlocked,match='SIGNED_BINDING'):
            source.verify_completed_gallery_current(current,profile,original,connection=db,owner_sid=sid,key=key,events=events)
    assert record['marketplace_approval'] is False and record['new_edit_attempts']==record['external_write_count']==0
    with pytest.raises(adoption.AdoptionBlocked,match='SINGLE_EVENT'):
        source.continue_native_final_image_ui(engine,profile,object(),**kwargs)


def test_actual_stored_candidate_and_request_are_unchanged(monkeypatch):
    from shared_platform import release_store
    actual=Path(os.environ['ORBIT_UI_ACTUAL_RELEASE_STORE']);assert actual.is_absolute()
    monkeypatch.setattr(release_store,'DEFAULT_RELEASE_STORE_PATH',actual)
    frozen=source._ui_frozen_candidate(release_store.default_release_store(),'8412287877065586543',before_append=True)
    evidence=json.loads(Path(os.environ['ORBIT_UI_ACTUAL_STAGE']).read_bytes());candidate=evidence['marketplace']['preview']
    assert frozen['candidate_digest']==candidate['candidate_digest']
    assert frozen['manifest_digest']==candidate['review_manifest']['manifest_digest']
    assert frozen['native_review_digest']==evidence['marketplace']['native_final_review']['review_digest']
    assert len(frozen['ordered_targets'])==9
    request=Path(os.environ['ORBIT_UI_USER_REQUEST']);pins=dict(path=str(request),sha256=sha256(request.read_bytes()).hexdigest())
    task={'task_id':'TASK-20261023-001'};original={'offer_id':'8412287877065586543'}
    assert source._ui_request(pins,task,original,frozen)==pins
    for change in (dict(candidate_digest='wrong'),dict(native_review_digest='wrong')):
        with pytest.raises(adoption.AdoptionBlocked):source._ui_request(pins,task,original,{**frozen,**change})
    with pytest.raises(adoption.AdoptionBlocked):source._ui_request({**pins,'sha256':'0'*64},task,original,frozen)


BROWSER=r'''
const fs=require('fs'),path=require('path'),assert=require('assert');
const {chromium}=require(process.env.ORBIT_UI_PLAYWRIGHT);
(async()=>{
const root=process.argv[2],out=process.env.ORBIT_UI_OUTPUT,stage=JSON.parse(fs.readFileSync(process.env.ORBIT_UI_ACTUAL_STAGE));
const imageProof=JSON.parse(fs.readFileSync(process.env.ORBIT_UI_IMAGE_PROOF)),assets=new Map(imageProof.images.map(i=>[i.url,i.local_cache]));
const browser=await chromium.launch({headless:true,executablePath:process.env.ORBIT_UI_CHROMIUM||undefined});
try{
const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],mutations=[];
page.on('pageerror',e=>errors.push(e.message));
await page.route('**/*',async route=>{
 const request=route.request(),url=new URL(request.url());
 if(request.method()!=='GET'){mutations.push(request.method()+' '+url.pathname);return route.abort();}
 if(url.pathname==='/product-workspace'){
  let html=fs.readFileSync(path.join(root,'web/product_workspace.html'),'utf8');
  html=html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,tag=>tag.includes('publication_original_adapter.js')?tag:'');
  return route.fulfill({contentType:'text/html',body:html});
 }
 if(url.pathname.startsWith('/static/')){let file=path.join(root,'web',url.pathname);return route.fulfill({path:file});}
 if(url.pathname==='/api/proxy-image'){
  const asset=assets.get(url.searchParams.get('url'));assert(asset,'non-frozen image requested');
  const bytes=fs.readFileSync(asset.path);assert.equal(require('crypto').createHash('sha256').update(bytes).digest('hex'),asset.sha256);
  return route.fulfill({body:bytes,contentType:'image/png'});
 }
 return route.abort();
});
await page.goto('http://native-ui.test/product-workspace?offer_id='+stage.offer_id+'&round=final');
await page.evaluate(stage=>{
 document.querySelector('#productDetail').hidden=false;document.querySelector('#noProductSelected').hidden=true;
 for(const pane of document.querySelectorAll('[data-product-pane]'))pane.hidden=pane.id!=='pane-images';
 document.querySelector('#tab-images').setAttribute('aria-selected','true');
 window.OrbitOriginalReview.render({product:{offer_id:stage.offer_id,title:'Frozen B refrigerator cover',seller_sku_candidate:'2000431'}});
 window.OrbitOriginalReview.stages(stage);
},stage);
const panel=page.locator('#nativeFinalFrozenContent');await panel.waitFor({state:'visible',timeout:5000});
await page.waitForFunction(()=>[...document.querySelectorAll('[data-native-frozen-image]')].every(i=>i.complete&&i.naturalWidth>0));
assert.equal(await panel.locator('.native-frozen-gallery img').count(),8);
assert.equal(await panel.locator('[data-native-variant]').count(),3);
assert.equal(await panel.locator('[data-native-ozon-card]').count(),3);
assert.equal(await panel.locator('[data-native-ozon-card] img').count(),24);
assert.equal(await panel.locator('[data-native-copy-set]').count(),stage.marketplace.preview.review_manifest.copy_sets.length);
assert.equal(await page.locator('#embeddedImageReview').isVisible(),false);
assert.equal(await page.locator('#content').isVisible(),false);
assert.equal(await page.locator('#saveEmbeddedImageReviewButton').isVisible(),false);
const originalControlCount=await page.locator('#publicationFlow').count();assert.equal(originalControlCount,1);
await panel.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'FROZEN_IMAGES_VISIBLE.png'),fullPage:false});
await panel.locator('[data-native-ozon-card]').first().scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'OZON_CARD_GALLERY_VISIBLE.png'),fullPage:false});
const values=await panel.locator('.native-frozen-gallery img').evaluateAll(imgs=>imgs.map(i=>({width:i.getBoundingClientRect().width,naturalWidth:i.naturalWidth,src:i.getAttribute('src')})));
assert(values.every(i=>i.width>=250&&i.naturalWidth>0));
for(const change of ['offer','plan','manifest','native']){
 const wrong=structuredClone(stage);
 if(change==='offer')wrong.marketplace.preview.offer_id='different-offer';
 if(change==='plan')wrong.marketplace.plan.plan_id='different-plan';
 if(change==='manifest')wrong.marketplace.preview.review_manifest.manifest_digest='different-manifest';
 if(change==='native')wrong.marketplace.native_final_review.candidate_digest='different-candidate';
 await page.evaluate(s=>window.OrbitOriginalReview.stages(s),wrong);
 assert.equal(await page.locator('#nativeFinalFrozenContent').count(),0,change+' must not borrow frozen images');
 await page.evaluate(s=>window.OrbitOriginalReview.stages(s),stage);
}
await page.evaluate(()=>window.OrbitOriginalReview.clear());
assert.equal(await page.locator('#nativeFinalFrozenContent').count(),0);assert.equal(await page.locator('#embeddedImageReview').isVisible(),true);
assert.deepEqual(errors,[]);assert.deepEqual(mutations,[]);
fs.writeFileSync(path.join(out,'BROWSER_COMPONENT_RESULT.json'),JSON.stringify({status:'PASS_COMPONENT_ONLY',browser:'Chromium',candidate_digest:stage.marketplace.preview.candidate_digest,gallery:values,variants:3,ozon_cards:3,ozon_images:24,identity_negative_cases:4,approval_clicks:0,non_GET_requests:mutations,page_errors:errors,deployed_runtime_verified:false},null,2));
}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
'''


def test_actual_frozen_native_images_pane_in_independent_chromium(tmp_path):
    node=Path(os.environ['ORBIT_NODE_BIN']);script=tmp_path/'native-ui.cjs';script.write_text(BROWSER,encoding='utf-8')
    root=Path(os.environ.get('ORBIT_UI_SOURCE_ROOT',str(ROOT)))
    result=subprocess.run([str(node),str(script),str(root)],text=True,capture_output=True,timeout=90)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
