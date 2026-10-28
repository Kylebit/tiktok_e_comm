"""Dual-proof source/HMAC/CAS; pure business parsers and old chain are named boundaries."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import hmac,json,sqlite3,sys
import pytest
from shared_platform import agent_prepared_source_successor as source,agent_prepared_publication_adoption as adoption
from shared_platform.operations_runtime import RuntimeProfile,runtime_manifest
from test_approved_native_r1_reader_source import git,pin
from test_approved_platform_source import history as platform_history
from test_native_stock_category_source import history as stock_history
from test_native_tiktok_locale_draft_source import history as locale_history
from test_native_tiktok_activation_source import segments as activation_segments


@pytest.fixture
def segments(tmp_path):
    root=tmp_path/'regional';root.mkdir();git(root,'init')
    for name in source.SHOPEE_REGIONAL_MECHANISM_FILES|source.SHOPEE_REGIONAL_FILES:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('original\n')
    git(root,'add','.');git(root,'commit','-m','original activation source')
    old=dict(code_version=git(root,'rev-parse','HEAD'),environment='stable',manifest_digest=runtime_manifest(root))
    for name in source.SHOPEE_REGIONAL_MECHANISM_FILES:(root/name).write_text('distinct regional source\n')
    git(root,'add','.');git(root,'commit','-m','source mechanism')
    mechanism=dict(code_version=git(root,'rev-parse','HEAD'),environment='stable',manifest_digest=runtime_manifest(root))
    for name in source.SHOPEE_REGIONAL_FILES:(root/name).write_text('existing global and PH item\n')
    git(root,'add','.');git(root,'commit','-m','regional continuation')
    return root,old,mechanism,RuntimeProfile(root,tmp_path/'ops','stable',git(root,'rev-parse','HEAD'),runtime_manifest(root))


@pytest.fixture
def history(locale_history,tmp_path,monkeypatch):
    store,frozen,request,preimage,_=locale_history
    intent=json.loads(Path(request['path']).read_bytes());image=json.loads(Path(preimage['path']).read_bytes())
    old=dict(run_preimage=pin(tmp_path/'original-activation-image.json',image),frozen_candidate=frozen,
        source_segments=dict(correction=dict(new_version=dict(code_version=image['source_head']))))
    tables=('release_target_runs','release_target_submissions','release_target_readbacks','release_target_failure_events',
        'native_final_budget_scopes','native_final_budget_reservations','native_final_budget_dispatches','native_final_budget_outcomes')
    def capture(db):
        for table in tables:image[table]=[dict(r) for r in db.execute('SELECT * FROM '+table)]
    with sqlite3.connect(store.path) as db:
        db.row_factory=sqlite3.Row
        for table in tables[-3:]:db.execute('DELETE FROM '+table+" WHERE scope_key='sh'")
        for n in range(1,12):
            operation='upload_image' if n<=8 else 'create_global_item' if n==9 else 'initialize_models' if n==10 else 'create_publish_task'
            db.execute("INSERT INTO native_final_budget_reservations VALUES('sh',?,'shopee:PH',?,1,?,'UNKNOWN')",(n,operation,'shared' if n<=10 else 'target'))
            db.execute("INSERT INTO native_final_budget_dispatches VALUES('sh',?,?)",(n,'retained-sh-'+str(n)))
            db.execute("INSERT INTO native_final_budget_outcomes VALUES('sh',?,'ACCEPTED')",(n,))
        db.execute("UPDATE release_target_runs SET status='FAILED',attempts=3 WHERE target_label LIKE 'shopee:%'")
        db.execute("UPDATE release_target_runs SET status='PENDING',attempts=2 WHERE target_label='shopee:VN'")
        capture(db);retained=deepcopy(image)
        # Fresh input must accept real TK growth, not freeze FAILED3 or a ledger count.
        db.execute("UPDATE release_target_runs SET status='SUCCEEDED',attempts=4 WHERE target_label='tiktok:LH_MY'")
        db.execute("INSERT INTO native_final_budget_dispatches VALUES('tk',10,'accepted-original-publish')")
        db.execute("INSERT INTO native_final_budget_outcomes VALUES('tk',10,'ACCEPTED')")
        db.execute("INSERT INTO native_final_budget_scopes VALUES('corr',?,'TIKTOK_DRAFT_CORRECTION')",(frozen['plan_id'],))
        db.execute("INSERT INTO native_final_budget_reservations VALUES('corr',1,'tiktok:LH_MY','save_draft',1,'target','UNKNOWN')")
        db.execute("INSERT INTO native_final_budget_dispatches VALUES('corr',1,'accepted-corrective-save')")
        db.execute("INSERT INTO native_final_budget_outcomes VALUES('corr',1,'ACCEPTED')")
        capture(db)
    cp=dict(schema_version='shopee-global-v4-checkpoint/v1',offer_id=intent['offer_id'],product_revision=6,run_id='original-global',report_id='original-report',
        global_item_id='43135542690',image_bindings=['frozen-eight'],model_skus=['2000431','2000432','2000433'])
    image['shopee_checkpoint']=cp;image['shopee_checkpoint_ref']=pin(tmp_path/'live-sh-cp.json',cp)
    proof=dict(task_id='offline-task',offer_id=intent['offer_id'],plan_id=frozen['plan_id'],run_id=image['run']['run_id'],
        decision_id=intent['decision_id'],candidate_digest=frozen['candidate_digest'],snapshot_digest=intent['snapshot_digest'],base_source_head=image['source_head'],
        target_labels=['shopee:PH','shopee:MY','shopee:TH','shopee:VN'],retained_ref=pin(tmp_path/'original-sh-eleven.json',retained),
        checkpoint_ref=pin(tmp_path/'captured-sh-cp.json',cp))
    proof_ref=pin(tmp_path/'regional-proof.json',proof)
    preimage=pin(Path(preimage['path']),image)
    saved_doc={**proof,'target_labels':['tiktok:LH_PH','tiktok:LH_TH'],'retained_ref':preimage}
    saved_ref=pin(tmp_path/'saved-publish-proof.json',saved_doc)
    intent.update(schema='original-approved-shopee-regional-tiktok-saved-publish-source-request/v1',shopee_regional_continuation_ref=proof_ref,
        tiktok_saved_publish_continuation_ref=saved_ref,
        limits=dict(source_event_max=1,shared_maximum=10,per_target_maximum=9,new_shared_writes=0),new_business_budget=False,new_PH_create=False,
        new_TK_SAVE=False,retry_dispatched_UNKNOWN=False)
    # Actual B parser gets a separate real retained-input gate, not a fabricated PASS here.
    monkeypatch.setitem(sys.modules,'shared_platform.native_shopee_regional_continuation',SimpleNamespace(read_regional_proof=source._r1_reader_pin))
    monkeypatch.setattr(sys.modules['shared_platform.native_tiktok_locale_draft_correction'],'read_saved_publish_continuation_proof',source._r1_reader_pin,raising=False)
    return store,frozen,pin(Path(request['path']),intent),preimage,proof_ref,saved_ref,old


def test_exact_files_and_old_activation_segments_remain_strict(segments,activation_segments):
    root,old,mechanism,profile=segments
    assert set(source._shopee_regional_segments(profile,old,mechanism)['regional']['changed_files'])==source.SHOPEE_REGIONAL_FILES
    (root/'shared_platform/ozon_extra.py').write_text('outside scope');git(root,'add','.');git(root,'commit','-m','outside scope')
    with pytest.raises(adoption.AdoptionBlocked):source._shopee_regional_segments(RuntimeProfile(root,profile.data_root,'stable',git(root,'rev-parse','HEAD'),runtime_manifest(root)),old,mechanism)
    root,old,mechanism,profile=activation_segments;signed=source._tiktok_activation_segments(profile,old,mechanism)
    (root/'shared_platform/agent_prepared_source_successor.py').write_text('next independent source');git(root,'add','.');git(root,'commit','-m','next source')
    current=RuntimeProfile(root,profile.data_root,'stable',git(root,'rev-parse','HEAD'),runtime_manifest(root))
    assert source._historical_tiktok_activation_segments(current,old,signed)==signed
    with pytest.raises(adoption.AdoptionBlocked):source._tiktok_activation_segments(current,old,mechanism)


def test_fresh_tk_effects_preserved_and_post_append_regional_growth_allowed(history):
    store,frozen,request,preimage,proof,saved,old=history
    expected=source._shopee_regional_history(store,'offline-task',frozen,request,preimage,proof,saved,old,before_append=True)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE release_target_runs SET status='RUNNING',attempts=4 WHERE target_label='shopee:MY'")
        db.execute("INSERT INTO native_final_budget_reservations VALUES('sh',12,'shopee:MY','create_publish_task',1,'target','UNKNOWN')")
        db.execute("INSERT INTO native_final_budget_dispatches VALUES('sh',12,'legitimate-new-regional-dispatch')")
    assert source._shopee_regional_history(store,'offline-task',frozen,request,preimage,proof,saved,old)==expected
    with pytest.raises(adoption.AdoptionBlocked,match='HISTORY_CAS_CHANGED'):source._shopee_regional_history(store,'offline-task',frozen,request,preimage,proof,saved,old,before_append=True)
    with sqlite3.connect(store.path) as db:db.execute("UPDATE native_final_budget_outcomes SET outcome='REJECTED' WHERE scope_key='corr'")
    with pytest.raises(adoption.AdoptionBlocked,match='PRIOR_BUDGET_CHANGED'):source._shopee_regional_history(store,'offline-task',frozen,request,preimage,proof,saved,old)


def test_new_unknown_budget_or_global_cp_change_never_cleared(history):
    store,frozen,request,preimage,proof,saved,old=history;image=json.loads(Path(preimage['path']).read_bytes())
    changed=deepcopy(image);changed['native_final_budget_outcomes']=[r for r in changed['native_final_budget_outcomes'] if r['scope_key']!='corr']
    altered_image=pin(Path(preimage['path']).with_name('unknown.json'),changed)
    saved_doc=json.loads(Path(saved['path']).read_bytes());saved_doc['retained_ref']=altered_image
    altered_saved=pin(Path(saved['path']).with_name('unknown-proof.json'),saved_doc)
    altered_intent=json.loads(Path(request['path']).read_bytes());altered_intent['tiktok_saved_publish_continuation_ref']=altered_saved
    with pytest.raises(adoption.AdoptionBlocked,match='UNRECONCILED_MUTATION'):source._shopee_regional_history(store,'offline-task',frozen,pin(Path(request['path']).with_name('unknown-request.json'),altered_intent),altered_image,proof,altered_saved,old,before_append=True)
    changed=json.loads(Path(request['path']).read_bytes());changed['new_business_budget']=True
    with pytest.raises(adoption.AdoptionBlocked,match='EXACT_HISTORY_PINS_REQUIRED'):source._shopee_regional_history(store,'offline-task',frozen,pin(Path(request['path']).with_name('new-budget.json'),changed),preimage,proof,saved,old)
    path=Path(image['shopee_checkpoint_ref']['path']);path.write_text('{}')
    with pytest.raises(adoption.AdoptionBlocked,match='GLOBAL_CHECKPOINT_CHANGED'):source._shopee_regional_history(store,'offline-task',frozen,request,preimage,proof,saved,old)


def test_new_hmac_and_purpose_required_before_old_historical_or_runtime_access(tmp_path,monkeypatch):
    calls=[];key=b'offline';sid='owner'
    monkeypatch.setattr(source,'_verify_tiktok_activation_source',lambda *a,**k:calls.append(k))
    record=dict(schema_version=source.SHOPEE_REGIONAL_SCHEMA,owner_sid=sid,purpose='ORIGINAL_APPROVED_SHOPEE_EXISTING_GLOBAL_AND_EXISTING_REGIONAL_ITEM_CONTINUATION');raw=adoption._bytes(record)
    envelope=dict(record_json=raw.decode(),record_digest=sha256(raw).hexdigest(),signature=hmac.new(key,raw,'sha256').hexdigest())
    with pytest.raises(adoption.AdoptionBlocked,match='PURPOSE_REQUIRED'):source._verify_shopee_regional_source({},None,{},connection=None,owner_sid=sid,key=key,events=[],rows=[(json.dumps(envelope),)])
    envelope['signature']='0'*64
    with pytest.raises(adoption.AdoptionBlocked,match='SIGNED_BINDING'):source._verify_shopee_regional_source({},None,{},connection=None,owner_sid=sid,key=key,events=[],rows=[(json.dumps(envelope),)])
    assert calls==[]
    from scripts.continue_agent_prepared_source import main
    config=tmp_path/'deployment.json';config.write_text('{}');base=['--deployment',str(config),'--receipt',str(tmp_path/'unused')]
    with pytest.raises(ValueError,match='SHOPEE_REGIONAL_SOURCE_EXACT_HISTORY_PINS_REQUIRED'):main(base+['--approved-shopee-regional-tiktok-saved-publish-continuation'])
    full=['--approved-shopee-regional-tiktok-saved-publish-continuation']
    for option in ('shopee-regional-mechanism-head','shopee-regional-mechanism-manifest','shopee-regional-technical-request','shopee-regional-technical-request-sha256','shopee-regional-run-preimage','shopee-regional-run-preimage-sha256','shopee-regional-proof','shopee-regional-proof-sha256','tiktok-saved-publish-proof','tiktok-saved-publish-proof-sha256'):full+=['--'+option,'offline']
    with pytest.raises(ValueError,match='SHOPEE_REGIONAL_SOURCE_EXACT_HISTORY_PINS_REQUIRED'):main(base+full[:-4])
    with pytest.raises(ValueError,match='SOURCE_SUCCESSOR_EXACT_SOURCE_ACTION_REQUIRED'):main(base+full+['--common-create-successor'])


def test_saved_publish_proof_requires_same_fresh_image_and_exact_proven_subset(history):
    store,frozen,request,preimage,proof,saved,old=history
    for change in ({'target_labels':['tiktok:LH_MY']},{'retained_ref':old['run_preimage']}):
        doc=json.loads(Path(saved['path']).read_bytes());doc.update(change)
        altered=pin(Path(saved['path']).with_name('wrong-saved-proof.json'),doc)
        intent=json.loads(Path(request['path']).read_bytes());intent['tiktok_saved_publish_continuation_ref']=altered
        with pytest.raises(adoption.AdoptionBlocked,match='EXACT_CURRENT_IMAGE_REQUIRED'):
            source._shopee_regional_history(store,'offline-task',frozen,pin(Path(request['path']).with_name('wrong-saved-request.json'),intent),preimage,proof,altered,old)


def test_one_signed_append_full_current_Task_events_CAS_and_release_preservation(segments,history,monkeypatch):
    from shared_platform.workbench_engine import WorkbenchEngine
    from shared_platform import r3_native_final_approval as actor,release_store
    root,version,mechanism,profile=segments;store,frozen,request,preimage,proof,saved,old=history;sid='owner';key=b'offline-key'
    engine=WorkbenchEngine(profile.data_root/'tasks.db',version)
    task_id=engine.create(dict(template='publication',source_key='regional',scope=dict(offer_id='8412287877065586543',shops=frozen['ordered_targets'],skus=['0431'])))['task_id']
    doc=json.loads(Path(proof['path']).read_bytes());doc.update(task_id=task_id,base_source_head=version['code_version']);proof=pin(Path(proof['path']),doc)
    image=json.loads(Path(preimage['path']).read_bytes());image['source_head']=version['code_version'];preimage=pin(Path(preimage['path']),image)
    doc=json.loads(Path(saved['path']).read_bytes());doc.update(task_id=task_id,base_source_head=version['code_version'],retained_ref=preimage);saved=pin(Path(saved['path']),doc)
    intent=json.loads(Path(request['path']).read_bytes());intent.update(task_id=task_id,source_head=version['code_version'],shopee_regional_continuation_ref=proof,tiktok_saved_publish_continuation_ref=saved);request=pin(Path(request['path']),intent)
    old['source_segments']['correction']['new_version']=version;old.update(schema_version=source.TIKTOK_ACTIVATION_SCHEMA,owner_sid=sid,scope_digest='same-scope')
    original=dict(schema_version=adoption.SCHEMA,owner_sid=sid,task_id=task_id,offer_id='8412287877065586543');raw=adoption._bytes(original);digest=sha256(raw).hexdigest();encoded=adoption._bytes(old)
    with engine.transaction() as db:
        db.execute('CREATE TABLE agent_prepared_publication_adoptions(task_id TEXT,record_json TEXT,record_digest TEXT,signature TEXT)')
        db.execute('INSERT INTO agent_prepared_publication_adoptions VALUES(?,?,?,?)',(task_id,raw.decode(),digest,hmac.new(key,raw,'sha256').hexdigest()))
        db.execute("UPDATE workbench_execution SET state='reconciliation_required',step_index=2,action_json=?,checkpoint_json=? WHERE task_id=?",('{"old":"action"}','{"old":"checkpoint"}',task_id))
        engine._event(db,task_id,source.TIKTOK_ACTIVATION_EVENT,dict(record_json=encoded.decode(),record_digest=sha256(encoded).hexdigest(),signature=hmac.new(key,encoded,'sha256').hexdigest()))
        before=dict(engine._row(db,task_id))
    monkeypatch.setattr(adoption,'_owner_key',lambda _:(sid,key));monkeypatch.setattr(actor,'read_native_actor_identity',lambda _:dict(identity_verified=True,trust_mode='SAME_WINDOWS_USER',owner_sid=sid))
    monkeypatch.setattr(source,'_verify_tiktok_activation_source',lambda *a,**k:dict(original='prior activation chain boundary'))
    monkeypatch.setattr(release_store,'default_release_store',lambda:store)
    kwargs=dict(task_id=task_id,expected_adoption_digest=digest,actor_reader=object(),mechanism_head=mechanism['code_version'],mechanism_manifest=mechanism['manifest_digest'],technical_request=request,run_preimage=preimage,shopee_regional_continuation_ref=proof,tiktok_saved_publish_continuation_ref=saved)
    read_history=source._shopee_regional_history
    def race(*a,**k):
        with engine.transaction() as db:engine._event(db,task_id,'later_original_TK_progress',dict(preserved=True))
        return read_history(*a,**k)
    monkeypatch.setattr(source,'_shopee_regional_history',race)
    with pytest.raises(adoption.AdoptionBlocked,match='CAS_CHANGED'):source.continue_shopee_regional_source(engine,profile,store,**kwargs)
    with engine.transaction() as db:events=[tuple(r) for r in db.execute('SELECT * FROM workbench_events ORDER BY id')]
    monkeypatch.setattr(source,'_shopee_regional_history',read_history);release_before=sha256(store.path.read_bytes()).hexdigest()
    source.continue_shopee_regional_source(engine,profile,store,**kwargs)
    assert sha256(store.path.read_bytes()).hexdigest()==release_before
    with engine.transaction() as db:
        after=dict(engine._row(db,task_id));assert {k:v for k,v in before.items() if k!='version_json'}=={k:v for k,v in after.items() if k!='version_json'}
        assert [tuple(r) for r in db.execute('SELECT * FROM workbench_events ORDER BY id')][:-1]==events
        task=engine._project(db,engine._row(db,task_id));rows=db.execute('SELECT detail_json FROM workbench_events WHERE event_type=?',(source.SHOPEE_REGIONAL_EVENT,)).fetchall()
        assert source._verify_shopee_regional_source(task,profile,original,connection=db,owner_sid=sid,key=key,events=[],rows=rows)==dict(original='prior activation chain boundary')
    with pytest.raises(adoption.AdoptionBlocked,match='SINGLE_EVENT'):source.continue_shopee_regional_source(engine,profile,store,**kwargs)
