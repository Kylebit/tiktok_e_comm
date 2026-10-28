"""Offline platform source continuation: old imports are effects, never zero."""
from copy import deepcopy
from hashlib import sha256
import hmac,json,sqlite3
from pathlib import Path
import pytest
from shared_platform import agent_prepared_source_successor as source,agent_prepared_publication_adoption as adoption
from shared_platform.operations_runtime import RuntimeProfile,runtime_manifest
from test_approved_native_r1_reader_source import git,Store,pin


@pytest.fixture
def segments(tmp_path):
    root=tmp_path/'source';root.mkdir();git(root,'init')
    for name in source.PLATFORM_MECHANISM_FILES|source.PLATFORM_FILES:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('old\n')
    git(root,'add','.');git(root,'commit','-m','approved reader source')
    old=dict(code_version=git(root,'rev-parse','HEAD'),environment='stable',manifest_digest=runtime_manifest(root))
    for name in source.PLATFORM_MECHANISM_FILES:(root/name).write_text('platform mechanism\n')
    git(root,'add','.');git(root,'commit','-m','platform mechanism only')
    mechanism=dict(code_version=git(root,'rev-parse','HEAD'),environment='stable',manifest_digest=runtime_manifest(root))
    for name in source.PLATFORM_FILES:(root/name).write_text('bounded compatibility\n')
    git(root,'add','.');git(root,'commit','-m','compatibility only')
    return root,old,mechanism,RuntimeProfile(root,tmp_path/'ops','stable',git(root,'rev-parse','HEAD'),runtime_manifest(root))


@pytest.fixture
def history(tmp_path):
    store=Store(tmp_path/'release.db');offer='8412287877065586543';plan='r3-marketplace:'+offer+':original';run='release-run:original'
    labels=['ozon:RU','shopee:MY','shopee:PH','shopee:TH','shopee:VN','tiktok:LH_MY','tiktok:LH_PH','tiktok:LH_TH','tiktok:LH_VN']
    imports='ozon:import-task:5906902718,ozon:import-task:5906902998,ozon:import-task:5906903305'
    frozen=dict(plan_id=plan,candidate_digest='c5',payload_digest='payload',native_review_digest='sha256:review',ordered_targets=labels)
    image=dict(source_head='old-head',all_targets_terminal=True,
        run=dict(run_id=run,plan_id=plan,approval_id='approval',status='FAILED',created_at='old',updated_at='done',completed_at='done',technical_admission_json=None,technical_admission_digest=None,technical_execution_state='LEGACY'),
        release_target_runs=[dict(run_id=run,target_label=label,idempotency_key='identity:'+label,status='FAILED',attempts=1,external_id=imports if label=='ozon:RU' else None,created_at='old',updated_at='done',completed_at='done') for label in labels],
        release_target_submissions=[dict(run_id=run,target_label='ozon:RU',external_id=imports,evidence_json='original import observation',evidence_digest='original-sha',submitted_at='old',status='SUBMITTED_UNVERIFIED')],
        release_target_readbacks=[],release_target_failure_events=[],native_final_budget_scopes=[dict(scope_key='ozon',plan_id=plan,platform='OZON')],
        native_final_budget_reservations=[dict(scope_key='ozon',sequence=n,target_label='ozon:RU',operation='import',attempt_count=1) for n in range(1,4)],
        native_final_budget_dispatches=[dict(scope_key='ozon',sequence=n,request_digest='original-'+str(n)) for n in range(1,4)],
        native_final_budget_outcomes=[dict(scope_key='ozon',sequence=n,outcome='ACCEPTED') for n in range(1,4)])
    for label in labels[1:]:
        raw=adoption._bytes(dict(known_pre_submit_zero=True,request_attempted=False,outcome_unknown=False,external_writes_performed=[],mutation_budget=dict(reservations=[]))).decode()
        image['release_target_failure_events'].append(dict(run_id=run,target_label=label,attempt=1,evidence_json=raw,evidence_digest=sha256(raw.encode()).hexdigest(),created_at='done'))
    with sqlite3.connect(store.path) as db:
        for name,value in image.items():
            rows=[value] if name=='run' else value
            if not isinstance(rows,list) or not rows:continue
            table='release_runs' if name=='run' else name
            db.execute('CREATE TABLE '+table+' ('+','.join(k+(' INTEGER' if type(v) is int else ' TEXT') for k,v in rows[0].items())+')')
            for row in rows:db.execute('INSERT INTO '+table+' VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))
        db.execute('CREATE TABLE release_target_readbacks (run_id TEXT,target_label TEXT,evidence_json TEXT)')
    request=dict(schema='original-approved-platform-compatibility-request/v1',task_id='offline-task',offer_id=offer,plan_id=plan,
        candidate_digest='c5',payload_digest='payload',review_digest='sha256:review',decision_id='native-final-decision:review',source_head='old-head',original_run_id=run,new_marketplace_approval=False,reimport_ozon=False,
        preserve_original_approval_candidate_content_common_images_and_targets=True,preserve_attempts_failures_submissions_readbacks_budgets_and_source_history=True,
        known_zero_targets=labels[1:],limits=dict(source_event_max=1))
    ozon=dict(schema_version='original-Ozon-three-imports-readonly/v1',original_task_ids=[5906902718,5906902998,5906903305],
        normalized_identities=[dict(offer_id=offer_id,id=identity,statuses=dict(moderate_status='approved',is_created=True)) for offer_id,identity in [('2000431',6760984630),('2000432',6760985024),('2000433',6760985460)]])
    return store,frozen,pin(tmp_path/'request.json',request),pin(tmp_path/'run.json',image),pin(tmp_path/'ozon.json',ozon)


def test_exact_mechanism_and_compatibility_segments(segments):
    root,old,mechanism,profile=segments
    assert set(source._platform_segments(profile,old,mechanism)['compatibility']['changed_files'])==source.PLATFORM_FILES
    (root/'modules/products/unrelated.py').write_text('forbidden\n');git(root,'add','.');git(root,'commit','-m','unrelated')
    changed=RuntimeProfile(root,profile.data_root,'stable',git(root,'rev-parse','HEAD'),runtime_manifest(root))
    with pytest.raises(adoption.AdoptionBlocked):source._platform_segments(changed,old,mechanism)


def test_original_nonzero_import_history_allows_later_managed_run_growth(history):
    store,frozen,request,preimage,ozon=history
    expected=source._platform_history(store,'offline-task',frozen,request,preimage,ozon,before_append=True)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE release_target_runs SET attempts=2,status='RUNNING' WHERE target_label='shopee:MY'")
        db.execute("INSERT INTO native_final_budget_reservations VALUES('ozon',4,'ozon:RU','stock',1)")
        db.execute("INSERT INTO native_final_budget_dispatches VALUES('ozon',4,'new-stock')")
    assert source._platform_history(store,'offline-task',frozen,request,preimage,ozon)==expected
    with pytest.raises(adoption.AdoptionBlocked):source._platform_history(store,'offline-task',frozen,request,preimage,ozon,before_append=True)
    with sqlite3.connect(store.path) as db:db.execute("UPDATE native_final_budget_dispatches SET request_digest='changed' WHERE sequence=1")
    with pytest.raises(adoption.AdoptionBlocked,match='ORIGINAL_BUDGET_CHANGED'):source._platform_history(store,'offline-task',frozen,request,preimage,ozon)


def test_imports_cannot_be_relabelled_as_zero_and_old_failure_cannot_change(history):
    store,frozen,request,preimage,ozon=history
    doc=json.loads(Path(ozon['path']).read_bytes());doc['original_task_ids'][0]=999
    wrong=pin(Path(ozon['path']).with_name('wrong.json'),doc)
    with pytest.raises(adoption.AdoptionBlocked,match='HISTORY_PINS_CHANGED'):source._platform_history(store,'offline-task',frozen,request,preimage,wrong)
    with sqlite3.connect(store.path) as db:db.execute("UPDATE release_target_failure_events SET evidence_json='changed' WHERE target_label='shopee:MY'")
    with pytest.raises(adoption.AdoptionBlocked,match='ORIGINAL_EVIDENCE_CHANGED'):source._platform_history(store,'offline-task',frozen,request,preimage,ozon)


def test_append_once_hmac_full_task_and_old_event_cas(segments,history,monkeypatch):
    from shared_platform.workbench_engine import WorkbenchEngine
    from shared_platform import r3_native_final_approval as actor,release_store
    root,old,mechanism,profile=segments;store,frozen,request,preimage,ozon=history;sid='offline-owner';key=b'offline-key'
    engine=WorkbenchEngine(profile.data_root/'tasks.db',old)
    task_id=engine.create(dict(template='publication',source_key='compat-test',scope=dict(offer_id='8412287877065586543',shops=frozen['ordered_targets'],skus=['0431'])))['task_id']
    intent=json.loads(Path(request['path']).read_bytes());intent['task_id']=task_id;intent['source_head']=old['code_version'];request=pin(Path(request['path']),intent)
    image=json.loads(Path(preimage['path']).read_bytes());image['source_head']=old['code_version'];preimage=pin(Path(preimage['path']),image)
    original=dict(schema_version=adoption.SCHEMA,owner_sid=sid,task_id=task_id,offer_id='8412287877065586543');raw=adoption._bytes(original);original_digest=sha256(raw).hexdigest()
    reader=dict(schema_version=source.R1_READER_SCHEMA,owner_sid=sid,scope_digest='original-scope',frozen_candidate=frozen,source_segments=dict(reader=dict(new_version=old)))
    signed=adoption._bytes(reader)
    with engine.transaction() as db:
        db.execute('CREATE TABLE agent_prepared_publication_adoptions (task_id TEXT,record_json TEXT,record_digest TEXT,signature TEXT)')
        db.execute('INSERT INTO agent_prepared_publication_adoptions VALUES(?,?,?,?)',(task_id,raw.decode(),original_digest,hmac.new(key,raw,'sha256').hexdigest()))
        db.execute("UPDATE workbench_execution SET state='reconciliation_required',step_index=2,action_json=?,checkpoint_json=? WHERE task_id=?",('{"original":"action"}','{"original":"checkpoint"}',task_id))
        engine._event(db,task_id,source.R1_READER_EVENT,dict(record_json=signed.decode(),record_digest=sha256(signed).hexdigest(),signature=hmac.new(key,signed,'sha256').hexdigest()))
        before=dict(engine._row(db,task_id));oldevents=[tuple(r) for r in db.execute('SELECT * FROM workbench_events')]
    monkeypatch.setattr(adoption,'_owner_key',lambda p:(sid,key))
    monkeypatch.setattr(actor,'read_native_actor_identity',lambda _:dict(identity_verified=True,trust_mode='SAME_WINDOWS_USER',owner_sid=sid))
    monkeypatch.setattr(source,'_verify_r1_reader',lambda *a,**k:dict(original='verified historical boundary'))
    monkeypatch.setattr(release_store,'default_release_store',lambda:store)
    kwargs=dict(task_id=task_id,expected_adoption_digest=original_digest,actor_reader=object(),mechanism_head=mechanism['code_version'],mechanism_manifest=mechanism['manifest_digest'],technical_request=request,run_preimage=preimage,ozon_readback=ozon)
    original_history=source._platform_history
    def raced(*a,**k):
        with engine.transaction() as db:db.execute("UPDATE workbench_execution SET action_json='{}' WHERE task_id=?",(task_id,))
        return original_history(*a,**k)
    monkeypatch.setattr(source,'_platform_history',raced)
    with pytest.raises(adoption.AdoptionBlocked,match='CAS_CHANGED'):source.continue_approved_platform_compatibility(engine,profile,store,**kwargs)
    with engine.transaction() as db:db.execute('UPDATE workbench_execution SET action_json=? WHERE task_id=?',(before['action_json'],task_id))
    monkeypatch.setattr(source,'_platform_history',original_history)
    source.continue_approved_platform_compatibility(engine,profile,store,**kwargs)
    with engine.transaction() as db:
        after=dict(engine._row(db,task_id));assert {k:v for k,v in before.items() if k!='version_json'}=={k:v for k,v in after.items() if k!='version_json'}
        assert [tuple(r) for r in db.execute('SELECT * FROM workbench_events ORDER BY id')][:-1]==oldevents
        task=engine._project(db,engine._row(db,task_id));rows=db.execute('SELECT detail_json FROM workbench_events WHERE event_type=?',(source.PLATFORM_EVENT,)).fetchall()
        assert source._verify_platform_compatibility(task,profile,original,connection=db,owner_sid=sid,key=key,events=[],rows=rows)==dict(original='verified historical boundary')
        envelope=json.loads(rows[0][0]);envelope['signature']='0'*64
        with pytest.raises(adoption.AdoptionBlocked,match='SIGNED_BINDING'):source._verify_platform_compatibility(task,profile,original,connection=db,owner_sid=sid,key=key,events=[],rows=[(json.dumps(envelope),)])
    with pytest.raises(adoption.AdoptionBlocked,match='SINGLE_EVENT'):source.continue_approved_platform_compatibility(engine,profile,store,**kwargs)
