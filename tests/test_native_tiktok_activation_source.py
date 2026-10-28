"""Connection-only source successor; original signed correction stays intact."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import hmac,json,sqlite3
import pytest
from shared_platform import agent_prepared_source_successor as source,agent_prepared_publication_adoption as adoption
from shared_platform.operations_runtime import RuntimeProfile,runtime_manifest
from test_approved_native_r1_reader_source import git,pin
from test_approved_platform_source import history as platform_history
from test_native_stock_category_source import history as stock_history
from test_native_tiktok_locale_draft_source import history as locale_history,segments as locale_segments


@pytest.fixture
def segments(tmp_path):
    root=tmp_path/'activation';root.mkdir();git(root,'init')
    for name in source.TIKTOK_ACTIVATION_MECHANISM_FILES|source.TIKTOK_ACTIVATION_FILES:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('original\n')
    git(root,'add','.');git(root,'commit','-m','original locale source')
    old=dict(code_version=git(root,'rev-parse','HEAD'),environment='stable',manifest_digest=runtime_manifest(root))
    for name in source.TIKTOK_ACTIVATION_MECHANISM_FILES:(root/name).write_text('new distinct source\n')
    git(root,'add','.');git(root,'commit','-m','mechanism')
    mechanism=dict(code_version=git(root,'rev-parse','HEAD'),environment='stable',manifest_digest=runtime_manifest(root))
    for name in source.TIKTOK_ACTIVATION_FILES:(root/name).write_text('bounded connection ordering\n')
    git(root,'add','.');git(root,'commit','-m','activation ordering')
    return root,old,mechanism,RuntimeProfile(root,tmp_path/'ops','stable',git(root,'rev-parse','HEAD'),runtime_manifest(root))


@pytest.fixture
def history(locale_history,tmp_path):
    store,frozen,prior_request,preimage,proof=locale_history
    intent=json.loads(Path(prior_request['path']).read_bytes());image=json.loads(Path(preimage['path']).read_bytes())
    old=dict(technical_request=prior_request,tiktok_draft_correction_ref=proof,frozen_candidate=frozen,
        source_segments=dict(correction=dict(new_version=dict(code_version=image['source_head']))))
    intent.update(schema='original-approved-tiktok-activation-connection-fix-source-request/v1',original_correction_proof_ref=proof,
        limits=dict(source_event_max=1),new_business_budget=False,new_draft_correction_authority=False,
        diagnostic_ref=pin(tmp_path/'ordering-evidence.json',dict(classification='OFFLINE_CONNECTION_ORDERING_PIN_EXAMPLE')))
    request=pin(tmp_path/'activation-request.json',intent)
    return store,frozen,request,preimage,old


def test_exact_connection_files_and_original_locale_history_stays_strict(segments,locale_segments):
    root,old,mechanism,profile=segments
    assert set(source._tiktok_activation_segments(profile,old,mechanism)['correction']['changed_files'])==source.TIKTOK_ACTIVATION_FILES
    (root/'modules/products/server.py').parent.mkdir(parents=True);(root/'modules/products/server.py').write_text('outside scope')
    git(root,'add','.');git(root,'commit','-m','outside')
    with pytest.raises(adoption.AdoptionBlocked):source._tiktok_activation_segments(RuntimeProfile(root,profile.data_root,'stable',git(root,'rev-parse','HEAD'),runtime_manifest(root)),old,mechanism)
    root,old,mechanism,profile=locale_segments;proof=source._locale_draft_segments(profile,old,mechanism)
    (root/'shared_platform/agent_prepared_source_successor.py').write_text('next connection purpose');git(root,'add','.');git(root,'commit','-m','later')
    current=RuntimeProfile(root,profile.data_root,'stable',git(root,'rev-parse','HEAD'),runtime_manifest(root))
    assert source._historical_locale_draft_segments(current,old,proof)==proof
    with pytest.raises(adoption.AdoptionBlocked):source._locale_draft_segments(current,old,mechanism)


def test_idle_only_creation_and_later_original_correction_dispatch_can_grow(history):
    store,frozen,request,preimage,old=history
    expected=source._tiktok_activation_history(store,'offline-task',frozen,request,preimage,old,before_append=True)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE release_target_runs SET status='RUNNING',attempts=4 WHERE target_label='tiktok:LH_MY'")
        db.execute("INSERT INTO native_final_budget_scopes VALUES('corr',?,'TIKTOK_DRAFT_CORRECTION')",(frozen['plan_id'],))
        db.execute("INSERT INTO native_final_budget_reservations VALUES('corr',1,'tiktok:LH_MY','save_draft',1,'target','UNKNOWN')")
        db.execute("INSERT INTO native_final_budget_dispatches VALUES('corr',1,'legitimate-later-unknown')")
    assert source._tiktok_activation_history(store,'offline-task',frozen,request,preimage,old)==expected
    with pytest.raises(adoption.AdoptionBlocked,match='HISTORY_CAS_CHANGED'):source._tiktok_activation_history(store,'offline-task',frozen,request,preimage,old,before_append=True)
    with sqlite3.connect(store.path) as db:db.execute("UPDATE native_final_budget_dispatches SET request_digest='changed' WHERE scope_key='tk' AND sequence=9")
    with pytest.raises(adoption.AdoptionBlocked,match='PRIOR_BUDGET_CHANGED'):source._tiktok_activation_history(store,'offline-task',frozen,request,preimage,old)


def test_no_new_authority_budget_or_unproven_unknown_at_creation(history):
    store,frozen,request,preimage,old=history;path=Path(preimage['path']);image=json.loads(path.read_bytes())
    changed=deepcopy(image);changed['native_final_budget_outcomes'].pop()
    with pytest.raises(adoption.AdoptionBlocked,match='OTHER_UNKNOWN_OR_NEW_EFFECT'):
        source._tiktok_activation_history(store,'offline-task',frozen,request,pin(path.with_name('other-unknown.json'),changed),old,before_append=True)
    changed=json.loads(Path(request['path']).read_bytes());changed['new_business_budget']=True
    with pytest.raises(adoption.AdoptionBlocked,match='EXACT_HISTORY_PINS_REQUIRED'):
        source._tiktok_activation_history(store,'offline-task',frozen,pin(path.with_name('new-budget.json'),changed),preimage,old)
    Path(image['tiktok_checkpoint_ref']['path']).write_bytes(b'changed')
    with pytest.raises(adoption.AdoptionBlocked,match='CHECKPOINT_CAS_CHANGED'):
        source._tiktok_activation_history(store,'offline-task',frozen,request,preimage,old,before_append=True)


def test_new_signature_purpose_before_historical_locale_and_cli_exact_action(tmp_path,monkeypatch):
    calls=[];key=b'offline';sid='owner'
    monkeypatch.setattr(source,'_verify_locale_draft_source',lambda *a,**k:calls.append(k))
    record=dict(schema_version=source.TIKTOK_ACTIVATION_SCHEMA,owner_sid=sid,purpose='wrong');raw=adoption._bytes(record)
    envelope=dict(record_json=raw.decode(),record_digest=sha256(raw).hexdigest(),signature=hmac.new(key,raw,'sha256').hexdigest())
    with pytest.raises(adoption.AdoptionBlocked,match='PURPOSE_REQUIRED'):source._verify_tiktok_activation_source({},None,{},connection=None,owner_sid=sid,key=key,events=[],rows=[(json.dumps(envelope),)])
    envelope['signature']='0'*64
    with pytest.raises(adoption.AdoptionBlocked,match='SIGNED_BINDING'):source._verify_tiktok_activation_source({},None,{},connection=None,owner_sid=sid,key=key,events=[],rows=[(json.dumps(envelope),)])
    assert calls==[]
    from scripts.continue_agent_prepared_source import main
    config=tmp_path/'deployment.json';config.write_text('{}');base=['--deployment',str(config),'--receipt',str(tmp_path/'unused')]
    with pytest.raises(ValueError,match='TIKTOK_ACTIVATION_SOURCE_EXACT_HISTORY_PINS_REQUIRED'):main(base+['--approved-tiktok-activation-connection-fix'])
    full=['--approved-tiktok-activation-connection-fix']
    for option in ('tiktok-activation-mechanism-head','tiktok-activation-mechanism-manifest','tiktok-activation-technical-request','tiktok-activation-technical-request-sha256','tiktok-activation-run-preimage','tiktok-activation-run-preimage-sha256'):full+=['--'+option,'offline']
    with pytest.raises(ValueError,match='SOURCE_SUCCESSOR_EXACT_SOURCE_ACTION_REQUIRED'):main(base+full+['--common-create-successor'])


def test_whole_current_Task_event_CAS_once_HMAC_and_release_bytes_preserved(segments,history,monkeypatch):
    from shared_platform.workbench_engine import WorkbenchEngine
    from shared_platform import r3_native_final_approval as actor,release_store
    root,version,mechanism,profile=segments;store,frozen,request,preimage,old=history;sid='owner';key=b'offline-key'
    engine=WorkbenchEngine(profile.data_root/'tasks.db',version)
    task_id=engine.create(dict(template='publication',source_key='activation-lock',scope=dict(offer_id='8412287877065586543',shops=frozen['ordered_targets'],skus=['0431'])))['task_id']
    intent=json.loads(Path(request['path']).read_bytes());intent.update(task_id=task_id,source_head=version['code_version']);request=pin(Path(request['path']),intent)
    image=json.loads(Path(preimage['path']).read_bytes());image['source_head']=version['code_version'];preimage=pin(Path(preimage['path']),image)
    old['source_segments']['correction']['new_version']=version;old.update(schema_version=source.LOCALE_DRAFT_SCHEMA,owner_sid=sid,scope_digest='same-scope')
    original=dict(schema_version=adoption.SCHEMA,owner_sid=sid,task_id=task_id,offer_id='8412287877065586543');raw=adoption._bytes(original);digest=sha256(raw).hexdigest();encoded=adoption._bytes(old)
    with engine.transaction() as db:
        db.execute('CREATE TABLE agent_prepared_publication_adoptions(task_id TEXT,record_json TEXT,record_digest TEXT,signature TEXT)')
        db.execute('INSERT INTO agent_prepared_publication_adoptions VALUES(?,?,?,?)',(task_id,raw.decode(),digest,hmac.new(key,raw,'sha256').hexdigest()))
        db.execute("UPDATE workbench_execution SET state='reconciliation_required',step_index=2,action_json=?,checkpoint_json=? WHERE task_id=?",('{"original":"action"}','{"original":"checkpoint"}',task_id))
        engine._event(db,task_id,source.LOCALE_DRAFT_EVENT,dict(record_json=encoded.decode(),record_digest=sha256(encoded).hexdigest(),signature=hmac.new(key,encoded,'sha256').hexdigest()))
        before=dict(engine._row(db,task_id))
    monkeypatch.setattr(adoption,'_owner_key',lambda _:(sid,key));monkeypatch.setattr(actor,'read_native_actor_identity',lambda _:dict(identity_verified=True,trust_mode='SAME_WINDOWS_USER',owner_sid=sid))
    # Genuine older business chain remains a named independent boundary here.
    monkeypatch.setattr(source,'_verify_locale_draft_source',lambda *a,**k:dict(original='prior verified locale boundary'))
    monkeypatch.setattr(release_store,'default_release_store',lambda:store)
    kwargs=dict(task_id=task_id,expected_adoption_digest=digest,actor_reader=object(),mechanism_head=mechanism['code_version'],mechanism_manifest=mechanism['manifest_digest'],technical_request=request,run_preimage=preimage)
    read_history=source._tiktok_activation_history
    def race(*a,**k):
        with engine.transaction() as db:engine._event(db,task_id,'current_runtime_progress',dict(preserved=True))
        return read_history(*a,**k)
    monkeypatch.setattr(source,'_tiktok_activation_history',race)
    with pytest.raises(adoption.AdoptionBlocked,match='CAS_CHANGED'):source.continue_tiktok_activation_source(engine,profile,store,**kwargs)
    with engine.transaction() as db:events=[tuple(r) for r in db.execute('SELECT * FROM workbench_events ORDER BY id')]
    monkeypatch.setattr(source,'_tiktok_activation_history',read_history);release_before=sha256(store.path.read_bytes()).hexdigest()
    source.continue_tiktok_activation_source(engine,profile,store,**kwargs)
    assert sha256(store.path.read_bytes()).hexdigest()==release_before
    with engine.transaction() as db:
        after=dict(engine._row(db,task_id));assert {k:v for k,v in before.items() if k!='version_json'}=={k:v for k,v in after.items() if k!='version_json'}
        assert [tuple(r) for r in db.execute('SELECT * FROM workbench_events ORDER BY id')][:-1]==events
        task=engine._project(db,engine._row(db,task_id));rows=db.execute('SELECT detail_json FROM workbench_events WHERE event_type=?',(source.TIKTOK_ACTIVATION_EVENT,)).fetchall()
        assert source._verify_tiktok_activation_source(task,profile,original,connection=db,owner_sid=sid,key=key,events=[],rows=rows)==dict(original='prior verified locale boundary')
    with pytest.raises(adoption.AdoptionBlocked,match='SINGLE_EVENT'):source.continue_tiktok_activation_source(engine,profile,store,**kwargs)
