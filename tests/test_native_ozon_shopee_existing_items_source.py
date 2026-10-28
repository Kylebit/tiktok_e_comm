"""Dual-proof source/HMAC/CAS; business parsers and old chain are named boundaries."""
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
from test_native_shopee_regional_source import history as regional_history,segments as regional_segments


@pytest.fixture
def segments(tmp_path):
    root=tmp_path/'two-card';root.mkdir();git(root,'init')
    for name in source.COMBINED_COMPLETION_MECHANISM_FILES|source.COMBINED_COMPLETION_FILES:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('original\n')
    git(root,'add','.');git(root,'commit','-m','original combined source')
    old=dict(code_version=git(root,'rev-parse','HEAD'),environment='stable',manifest_digest=runtime_manifest(root))
    for name in source.COMBINED_COMPLETION_MECHANISM_FILES:(root/name).write_text('distinct stock source\n')
    git(root,'add','.');git(root,'commit','-m','source mechanism')
    mechanism=dict(code_version=git(root,'rev-parse','HEAD'),environment='stable',manifest_digest=runtime_manifest(root))
    for name in source.COMBINED_COMPLETION_FILES:(root/name).write_text('two eligible stock cards only\n')
    git(root,'add','.');git(root,'commit','-m','partial stock continuation')
    return root,old,mechanism,RuntimeProfile(root,tmp_path/'ops','stable',git(root,'rev-parse','HEAD'),runtime_manifest(root))


@pytest.fixture
def history(regional_history,tmp_path,monkeypatch):
    store,frozen,request,preimage,_,_,_=regional_history
    intent=json.loads(Path(request['path']).read_bytes());image=json.loads(Path(preimage['path']).read_bytes())
    old=dict(run_preimage=pin(tmp_path/'original-combined-image.json',image),frozen_candidate=frozen,
        source_segments=dict(regional=dict(new_version=dict(code_version=image['source_head']))))
    tables=('release_target_runs','release_target_submissions','release_target_readbacks','release_target_failure_events',
        'native_final_budget_scopes','native_final_budget_reservations','native_final_budget_dispatches','native_final_budget_outcomes')
    # A new source must retain then-current TK/SH progress, not old row counts/statuses.
    with sqlite3.connect(store.path) as db:
        db.row_factory=sqlite3.Row
        db.execute("UPDATE release_target_runs SET status='SUCCEEDED',attempts=4 WHERE target_label='shopee:MY'")
        db.execute("INSERT INTO native_final_budget_reservations VALUES('sh',12,'shopee:MY','create_publish_task',1,'target','UNKNOWN')")
        db.execute("INSERT INTO native_final_budget_dispatches VALUES('sh',12,'accepted-current-regional')")
        db.execute("INSERT INTO native_final_budget_outcomes VALUES('sh',12,'ACCEPTED')")
        db.execute("UPDATE release_target_runs SET status='SUCCEEDED',attempts=5 WHERE target_label='tiktok:LH_PH'")
        db.execute("INSERT INTO native_final_budget_dispatches VALUES('tk',12,'accepted-current-publish')")
        db.execute("INSERT INTO native_final_budget_outcomes VALUES('tk',12,'ACCEPTED')")
        for table in tables:image[table]=[dict(r) for r in db.execute('SELECT * FROM '+table)]
    preimage=pin(Path(preimage['path']),image)
    proof=dict(task_id='offline-task',offer_id=intent['offer_id'],plan_id=frozen['plan_id'],run_id=image['run']['run_id'],
        decision_id=intent['decision_id'],candidate_digest=frozen['candidate_digest'],snapshot_digest=intent['snapshot_digest'],
        base_source_head=image['source_head'],retained_ref=preimage,selected_offers=['2000432','2000433'],blocked_offer='2000431')
    proof_ref=pin(tmp_path/'two-card-proof.json',proof)
    maxima={label:dict(**({'update_regional_copy':1} if label!='shopee:PH' else {}),update_description_media=1,list_regional_item=1)
        for label in ('shopee:MY','shopee:PH','shopee:TH','shopee:VN')}
    shopee_ref=pin(tmp_path/'existing-item-proof.json',dict(proof,target_labels=['shopee:MY','shopee:PH','shopee:TH','shopee:VN'],operation_maxima=maxima,
        items=dict(MY='44920064243',PH='47970054181',TH='53420052020',VN='52420043241')))
    intent.update(schema='original-approved-ozon-stock-shopee-frozen-completion-source-request/v1',ozon_two_card_stock_ref=proof_ref,
        shopee_existing_item_completion_ref=shopee_ref,
        limits=dict(source_event_max=1,original_shared_maximum=4,batch_stock_maximum=1,shopee_shared_maximum=10,
            shopee_per_target_maximum=9,new_shopee_shared_writes=0,shopee_operation_maxima=maxima),new_business_budget=False,
        new_marketplace_approval=False,new_product_import=False,new_ozon_media=False,new_image_upload=False,declare_all_three_success=False,
        new_shopee_create=False,new_shopee_shared_write=False,new_shopee_content=False,restore_original_frozen_copy=True,restore_original_eight_description_images=True)
    # Its owner tests actual typed business parsing; no fabricated actual-input PASS here.
    monkeypatch.setitem(sys.modules,'shared_platform.native_ozon_three_card_media_recovery',SimpleNamespace(read_two_card_stock_proof=source._r1_reader_pin))
    monkeypatch.setattr(sys.modules['shared_platform.native_shopee_regional_continuation'],'read_existing_item_completion_proof',source._r1_reader_pin,raising=False)
    return store,frozen,pin(Path(request['path']),intent),preimage,proof_ref,shopee_ref,old


def test_exact_new_files_and_original_combined_git_segments_stay_strict(segments,regional_segments):
    root,old,mechanism,profile=segments
    assert set(source._combined_completion_segments(profile,old,mechanism)['execution']['changed_files'])==source.COMBINED_COMPLETION_FILES
    (root/'shared_platform/outside.py').write_text('outside scope');git(root,'add','.');git(root,'commit','-m','outside')
    with pytest.raises(adoption.AdoptionBlocked):source._combined_completion_segments(RuntimeProfile(root,profile.data_root,'stable',git(root,'rev-parse','HEAD'),runtime_manifest(root)),old,mechanism)
    root,old,mechanism,profile=regional_segments;prior=source._shopee_regional_segments(profile,old,mechanism)
    (root/'shared_platform/agent_prepared_source_successor.py').write_text('next independent source');git(root,'add','.');git(root,'commit','-m','next')
    current=RuntimeProfile(root,profile.data_root,'stable',git(root,'rev-parse','HEAD'),runtime_manifest(root))
    assert source._historical_shopee_regional_segments(current,old,prior)==prior
    with pytest.raises(adoption.AdoptionBlocked):source._shopee_regional_segments(current,old,mechanism)


def test_current_tk_sh_effects_and_later_original_stock_run_allowed(history):
    store,frozen,request,preimage,proof,shopee,old=history
    expected=source._combined_completion_history(store,'offline-task',frozen,request,preimage,proof,shopee,old,before_append=True)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE release_target_runs SET status='RUNNING',attempts=3 WHERE target_label='ozon:RU'")
        db.execute("INSERT INTO native_final_budget_reservations VALUES('ozon',4,'ozon:RU','update_stock',1,'shared','UNKNOWN')")
        db.execute("INSERT INTO native_final_budget_dispatches VALUES('ozon',4,'original-two-card-stock')")
        db.execute("UPDATE release_target_runs SET status='RUNNING',attempts=5 WHERE target_label='shopee:MY'")
        db.execute("INSERT INTO native_final_budget_reservations VALUES('sh',13,'shopee:MY','list_regional_item',1,'target','UNKNOWN')")
        db.execute("INSERT INTO native_final_budget_dispatches VALUES('sh',13,'original-existing-item-list')")
    assert source._combined_completion_history(store,'offline-task',frozen,request,preimage,proof,shopee,old)==expected
    with pytest.raises(adoption.AdoptionBlocked,match='HISTORY_CAS_CHANGED'):source._combined_completion_history(store,'offline-task',frozen,request,preimage,proof,shopee,old,before_append=True)
    with sqlite3.connect(store.path) as db:db.execute("UPDATE native_final_budget_outcomes SET outcome='REJECTED' WHERE scope_key='corr'")
    with pytest.raises(adoption.AdoptionBlocked,match='PRIOR_BUDGET_CHANGED'):source._combined_completion_history(store,'offline-task',frozen,request,preimage,proof,shopee,old)


def test_no_unproven_unknown_new_authority_wrong_image_or_cp_change(history):
    store,frozen,request,preimage,proof,shopee,old=history;image=json.loads(Path(preimage['path']).read_bytes());intent=json.loads(Path(request['path']).read_bytes())
    for change in ({'new_business_budget':True},{'declare_all_three_success':True},{'new_shopee_create':True}):
        with pytest.raises(adoption.AdoptionBlocked,match='EXACT_HISTORY_PINS_REQUIRED'):
            source._combined_completion_history(store,'offline-task',frozen,pin(Path(request['path']).with_name('bad-request.json'),dict(intent,**change)),preimage,proof,shopee,old)
    doc=json.loads(Path(proof['path']).read_bytes());doc['retained_ref']=old['run_preimage'];wrong=pin(Path(proof['path']).with_name('wrong-image.json'),doc)
    with pytest.raises(adoption.AdoptionBlocked,match='EXACT_HISTORY_PINS_REQUIRED'):source._combined_completion_history(store,'offline-task',frozen,pin(Path(request['path']).with_name('wrong-request.json'),dict(intent,ozon_two_card_stock_ref=wrong)),preimage,wrong,shopee,old)
    changed=deepcopy(image);changed['native_final_budget_outcomes']=[r for r in changed['native_final_budget_outcomes'] if r['scope_key']!='corr']
    altered=pin(Path(preimage['path']).with_name('unknown-image.json'),changed);doc['retained_ref']=altered;wrong=pin(Path(proof['path']).with_name('unknown-proof.json'),doc)
    shdoc=json.loads(Path(shopee['path']).read_bytes());shdoc['retained_ref']=altered;shwrong=pin(Path(shopee['path']).with_name('unknown-shopee.json'),shdoc)
    with pytest.raises(adoption.AdoptionBlocked,match='UNRECONCILED_MUTATION'):source._combined_completion_history(store,'offline-task',frozen,pin(Path(request['path']).with_name('unknown-request.json'),dict(intent,ozon_two_card_stock_ref=wrong,shopee_existing_item_completion_ref=shwrong)),altered,wrong,shwrong,old,before_append=True)
    Path(image['shopee_checkpoint_ref']['path']).write_text('{}')
    with pytest.raises(adoption.AdoptionBlocked,match='GLOBAL_CHECKPOINT_CHANGED'):source._combined_completion_history(store,'offline-task',frozen,request,preimage,proof,shopee,old)


def test_new_hmac_and_purpose_before_historical_and_cli_exact_pins(tmp_path,monkeypatch):
    calls=[];key=b'offline';sid='owner'
    monkeypatch.setattr(source,'_verify_shopee_regional_source',lambda *a,**k:calls.append(k))
    record=dict(schema_version=source.COMBINED_COMPLETION_SCHEMA,owner_sid=sid,purpose='wrong');raw=adoption._bytes(record)
    envelope=dict(record_json=raw.decode(),record_digest=sha256(raw).hexdigest(),signature=hmac.new(key,raw,'sha256').hexdigest())
    with pytest.raises(adoption.AdoptionBlocked,match='PURPOSE_REQUIRED'):source._verify_combined_completion_source({},None,{},connection=None,owner_sid=sid,key=key,events=[],rows=[(json.dumps(envelope),)])
    envelope['signature']='0'*64
    with pytest.raises(adoption.AdoptionBlocked,match='SIGNED_BINDING'):source._verify_combined_completion_source({},None,{},connection=None,owner_sid=sid,key=key,events=[],rows=[(json.dumps(envelope),)])
    assert calls==[]
    from scripts.continue_agent_prepared_source import main
    config=tmp_path/'deployment.json';config.write_text('{}');base=['--deployment',str(config),'--receipt',str(tmp_path/'unused')]
    with pytest.raises(ValueError,match='COMBINED_COMPLETION_SOURCE_EXACT_HISTORY_PINS_REQUIRED'):main(base+['--approved-ozon-stock-shopee-frozen-completion-continuation'])
    full=['--approved-ozon-stock-shopee-frozen-completion-continuation']
    for option in ('completion-mechanism-head','completion-mechanism-manifest','completion-technical-request','completion-technical-request-sha256','completion-run-preimage','completion-run-preimage-sha256','ozon-two-card-proof','ozon-two-card-proof-sha256','shopee-existing-item-completion-proof','shopee-existing-item-completion-proof-sha256'):full+=['--'+option,'offline']
    with pytest.raises(ValueError,match='EXACT_HISTORY_PINS_REQUIRED'):main(base+full[:-4])
    with pytest.raises(ValueError,match='SOURCE_SUCCESSOR_EXACT_SOURCE_ACTION_REQUIRED'):main(base+full+['--common-create-successor'])


def test_shopee_proof_same_fresh_image_identity_and_no_scope_extension(history):
    store,frozen,request,preimage,proof,shopee,old=history
    original=json.loads(Path(shopee['path']).read_bytes());intent=json.loads(Path(request['path']).read_bytes())
    for change in ({'retained_ref':old['run_preimage']},{'task_id':'another-task'},{'base_source_head':'another-source'},
        {'operation_maxima':{'shopee:PH':{'update_regional_copy':1,'list_regional_item':1}}}):
        altered=pin(Path(shopee['path']).with_name('wrong-shopee-proof.json'),dict(original,**change))
        changed=pin(Path(request['path']).with_name('wrong-shopee-request.json'),dict(intent,shopee_existing_item_completion_ref=altered))
        with pytest.raises(adoption.AdoptionBlocked,match='EXACT_HISTORY_PINS_REQUIRED'):
            source._combined_completion_history(store,'offline-task',frozen,changed,preimage,proof,altered,old)


def test_one_signed_append_full_current_task_events_CAS_and_release_preserved(segments,history,monkeypatch):
    from shared_platform.workbench_engine import WorkbenchEngine
    from shared_platform import r3_native_final_approval as actor,release_store
    root,version,mechanism,profile=segments;store,frozen,request,preimage,proof,shopee,old=history;sid='owner';key=b'offline-key'
    engine=WorkbenchEngine(profile.data_root/'tasks.db',version)
    task_id=engine.create(dict(template='publication',source_key='two-card',scope=dict(offer_id='8412287877065586543',shops=frozen['ordered_targets'],skus=['0431'])))['task_id']
    image=json.loads(Path(preimage['path']).read_bytes());image['source_head']=version['code_version'];preimage=pin(Path(preimage['path']),image)
    doc=json.loads(Path(proof['path']).read_bytes());doc.update(task_id=task_id,base_source_head=version['code_version'],retained_ref=preimage);proof=pin(Path(proof['path']),doc)
    doc=json.loads(Path(shopee['path']).read_bytes());doc.update(task_id=task_id,base_source_head=version['code_version'],retained_ref=preimage);shopee=pin(Path(shopee['path']),doc)
    intent=json.loads(Path(request['path']).read_bytes());intent.update(task_id=task_id,source_head=version['code_version'],ozon_two_card_stock_ref=proof,shopee_existing_item_completion_ref=shopee);request=pin(Path(request['path']),intent)
    old['source_segments']['regional']['new_version']=version;old.update(schema_version=source.SHOPEE_REGIONAL_SCHEMA,owner_sid=sid,scope_digest='same-scope')
    original=dict(schema_version=adoption.SCHEMA,owner_sid=sid,task_id=task_id,offer_id='8412287877065586543');raw=adoption._bytes(original);digest=sha256(raw).hexdigest();encoded=adoption._bytes(old)
    with engine.transaction() as db:
        db.execute('CREATE TABLE agent_prepared_publication_adoptions(task_id TEXT,record_json TEXT,record_digest TEXT,signature TEXT)')
        db.execute('INSERT INTO agent_prepared_publication_adoptions VALUES(?,?,?,?)',(task_id,raw.decode(),digest,hmac.new(key,raw,'sha256').hexdigest()))
        db.execute("UPDATE workbench_execution SET state='reconciliation_required',step_index=2,action_json=?,checkpoint_json=? WHERE task_id=?",('{"old":"action"}','{"old":"checkpoint"}',task_id))
        engine._event(db,task_id,source.SHOPEE_REGIONAL_EVENT,dict(record_json=encoded.decode(),record_digest=sha256(encoded).hexdigest(),signature=hmac.new(key,encoded,'sha256').hexdigest()))
        before=dict(engine._row(db,task_id))
    monkeypatch.setattr(adoption,'_owner_key',lambda _:(sid,key));monkeypatch.setattr(actor,'read_native_actor_identity',lambda _:dict(identity_verified=True,trust_mode='SAME_WINDOWS_USER',owner_sid=sid))
    monkeypatch.setattr(source,'_verify_shopee_regional_source',lambda *a,**k:dict(original='prior combined chain boundary'))
    monkeypatch.setattr(release_store,'default_release_store',lambda:store)
    kwargs=dict(task_id=task_id,expected_adoption_digest=digest,actor_reader=object(),mechanism_head=mechanism['code_version'],mechanism_manifest=mechanism['manifest_digest'],technical_request=request,run_preimage=preimage,ozon_two_card_stock_ref=proof,shopee_existing_item_completion_ref=shopee)
    read_history=source._combined_completion_history
    def race(*a,**k):
        with engine.transaction() as db:engine._event(db,task_id,'later_current_TK_SH_progress',dict(preserved=True))
        return read_history(*a,**k)
    monkeypatch.setattr(source,'_combined_completion_history',race)
    with pytest.raises(adoption.AdoptionBlocked,match='CAS_CHANGED'):source.continue_combined_completion_source(engine,profile,store,**kwargs)
    with engine.transaction() as db:events=[tuple(r) for r in db.execute('SELECT * FROM workbench_events ORDER BY id')]
    monkeypatch.setattr(source,'_combined_completion_history',read_history);release_before=sha256(store.path.read_bytes()).hexdigest()
    source.continue_combined_completion_source(engine,profile,store,**kwargs)
    assert sha256(store.path.read_bytes()).hexdigest()==release_before
    with engine.transaction() as db:
        after=dict(engine._row(db,task_id));assert {k:v for k,v in before.items() if k!='version_json'}=={k:v for k,v in after.items() if k!='version_json'}
        assert [tuple(r) for r in db.execute('SELECT * FROM workbench_events ORDER BY id')][:-1]==events
        task=engine._project(db,engine._row(db,task_id));rows=db.execute('SELECT detail_json FROM workbench_events WHERE event_type=?',(source.COMBINED_COMPLETION_EVENT,)).fetchall()
        assert source._verify_combined_completion_source(task,profile,original,connection=db,owner_sid=sid,key=key,events=[],rows=rows)==dict(original='prior combined chain boundary')
    with pytest.raises(adoption.AdoptionBlocked,match='SINGLE_EVENT'):source.continue_combined_completion_source(engine,profile,store,**kwargs)
