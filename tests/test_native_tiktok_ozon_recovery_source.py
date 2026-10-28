"""Combined native-wrapper/media source event; prior real effects stay retained."""
from hashlib import sha256
import hmac,json,sqlite3,sys
from types import SimpleNamespace
from pathlib import Path
import pytest
from shared_platform import agent_prepared_source_successor as source,agent_prepared_publication_adoption as adoption
from shared_platform.operations_runtime import RuntimeProfile,runtime_manifest
from test_approved_native_r1_reader_source import git,pin
from test_approved_platform_source import history as platform_history,segments as platform_segments


@pytest.fixture
def segments(tmp_path):
    root=tmp_path/'tk-source';root.mkdir();git(root,'init')
    for name in source.NATIVE_RECOVERY_MECHANISM_FILES|source.NATIVE_RECOVERY_FILES:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('old\n')
    git(root,'add','.');git(root,'commit','-m','platform source')
    old=dict(code_version=git(root,'rev-parse','HEAD'),environment='stable',manifest_digest=runtime_manifest(root))
    for name in source.NATIVE_RECOVERY_MECHANISM_FILES:(root/name).write_text('distinct native-wrapper mechanism\n')
    git(root,'add','.');git(root,'commit','-m','mechanism only')
    mechanism=dict(code_version=git(root,'rev-parse','HEAD'),environment='stable',manifest_digest=runtime_manifest(root))
    for name in source.NATIVE_RECOVERY_FILES:(root/name).write_text('native wrapper retry\n')
    git(root,'add','.');git(root,'commit','-m','TK wrapper only')
    return root,old,mechanism,RuntimeProfile(root,tmp_path/'ops','stable',git(root,'rev-parse','HEAD'),runtime_manifest(root))


@pytest.fixture
def history(platform_history):
    store,frozen,request,preimage,ozon=platform_history
    with sqlite3.connect(store.path) as db:
        db.row_factory=sqlite3.Row
        db.execute("UPDATE release_target_runs SET status='SUCCEEDED',attempts=2,external_id='real-SH-item' WHERE target_label='shopee:MY'")
        db.execute("UPDATE release_target_runs SET status='PENDING',completed_at=NULL WHERE target_label='shopee:VN'")
        db.execute("INSERT INTO native_final_budget_scopes VALUES('shopee',?,'SHOPEE')",(frozen['plan_id'],))
        db.execute("INSERT INTO native_final_budget_reservations VALUES('shopee',1,'shopee:MY','create_global',1)")
        db.execute("INSERT INTO native_final_budget_dispatches VALUES('shopee',1,'real-SH-request')")
        db.execute("INSERT INTO native_final_budget_outcomes VALUES('shopee',1,'ACCEPTED')")
        run=db.execute('SELECT run_id FROM release_runs').fetchone()[0]
        db.execute("INSERT INTO release_target_submissions VALUES(?,'shopee:MY','real-SH-item','SH-original-evidence','SH-sha','now','SUBMITTED_UNVERIFIED')",(run,))
        db.execute("INSERT INTO release_target_readbacks VALUES(?,'shopee:MY','real-SH-readback')",(run,))
        for label in source.NATIVE_RECOVERY_TARGETS:
            row=db.execute('SELECT evidence_json FROM release_target_failure_events WHERE target_label=?',(label,)).fetchone()
            proof=json.loads(row[0]);proof.update(schema_version='native-scoped-v4-target-result/v1',target_label=label)
            raw=adoption._bytes(proof).decode();db.execute('UPDATE release_target_failure_events SET evidence_json=?,evidence_digest=? WHERE target_label=?',(raw,sha256(raw.encode()).hexdigest(),label))
        image=json.loads(Path(preimage['path']).read_bytes());image['all_targets_idle']=True;image['run']=dict(db.execute('SELECT * FROM release_runs').fetchone())
        for table in [k for k,v in image.items() if isinstance(v,list)]:image[table]=[dict(r) for r in db.execute('SELECT * FROM '+table)]
    preimage=pin(Path(preimage['path']),image)
    intent=json.loads(Path(request['path']).read_bytes());intent.update(schema='original-approved-native-tiktok-ozon-recovery-source-request/v1',
        known_zero_targets=sorted(source.NATIVE_RECOVERY_TARGETS),append_requires_original_worker_and_all_mutations_idle=True,Shopee_must_be_zero_or_terminal=False)
    return store,frozen,pin(Path(request['path']),intent),preimage


def test_exact_tk_segments_and_real_historical_platform_git(segments,platform_segments):
    root,old,mechanism,profile=segments
    assert set(source._native_recovery_segments(profile,old,mechanism)['retry']['changed_files'])==source.NATIVE_RECOVERY_FILES
    (root/'modules/ozon/unrelated.py').parent.mkdir(parents=True,exist_ok=True);(root/'modules/ozon/unrelated.py').write_text('forbidden\n')
    git(root,'add','.');git(root,'commit','-m','forbidden Ozon scope')
    changed=RuntimeProfile(root,profile.data_root,'stable',git(root,'rev-parse','HEAD'),runtime_manifest(root))
    with pytest.raises(adoption.AdoptionBlocked):source._native_recovery_segments(changed,old,mechanism)
    root,old,mechanism,profile=platform_segments;proof=source._platform_segments(profile,old,mechanism)
    (root/'shared_platform/agent_prepared_source_successor.py').write_text('later signed purpose mechanism\n');git(root,'add','.');git(root,'commit','-m','later mechanism')
    current=RuntimeProfile(root,profile.data_root,'stable',git(root,'rev-parse','HEAD'),runtime_manifest(root))
    assert source._historical_platform_segments(current,old,proof)==proof
    with pytest.raises(adoption.AdoptionBlocked):source._platform_segments(current,old,mechanism)


def test_nonzero_shopee_and_original_ozon_retained_while_later_tk_attempt_can_grow(history):
    store,frozen,request,preimage=history
    result=source._native_recovery_history(store,'offline-task',frozen,request,preimage,before_append=True)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE release_target_runs SET attempts=2,status='RUNNING' WHERE target_label='tiktok:LH_MY'")
        db.execute("INSERT INTO native_final_budget_scopes VALUES('tk',?,'TIKTOK')",(frozen['plan_id'],))
        db.execute("INSERT INTO native_final_budget_reservations VALUES('tk',1,'tiktok:LH_MY','create_draft',1)")
    assert source._native_recovery_history(store,'offline-task',frozen,request,preimage)==result
    with pytest.raises(adoption.AdoptionBlocked,match='HISTORY_CAS_CHANGED'):source._native_recovery_history(store,'offline-task',frozen,request,preimage,before_append=True)
    with sqlite3.connect(store.path) as db:db.execute("UPDATE native_final_budget_dispatches SET request_digest='tampered' WHERE scope_key='shopee'")
    with pytest.raises(adoption.AdoptionBlocked,match='PRIOR_BUDGET_CHANGED'):source._native_recovery_history(store,'offline-task',frozen,request,preimage)


def test_new_purpose_signature_required_before_historical_consumption(monkeypatch):
    calls=[];key=b'key';sid='owner'
    monkeypatch.setattr(source,'_verify_platform_compatibility',lambda *a,**k:calls.append(k))
    record=dict(schema_version=source.NATIVE_RECOVERY_SCHEMA,owner_sid=sid,purpose='wrong-purpose');raw=adoption._bytes(record)
    row=dict(record_json=raw.decode(),record_digest=sha256(raw).hexdigest(),signature=hmac.new(key,raw,'sha256').hexdigest())
    with pytest.raises(adoption.AdoptionBlocked,match='PURPOSE_REQUIRED'):source._verify_native_recovery_source({},None,{},connection=None,owner_sid=sid,key=key,events=[],rows=[(json.dumps(row),)])
    row['signature']='0'*64
    with pytest.raises(adoption.AdoptionBlocked,match='SIGNED_BINDING'):source._verify_native_recovery_source({},None,{},connection=None,owner_sid=sid,key=key,events=[],rows=[(json.dumps(row),)])
    assert calls==[]


def test_media3_pin_is_required_and_validated_by_shared_readonly_reader(tmp_path,monkeypatch):
    decision=pin(tmp_path/'media3.json',dict(scope='B-only',maximum_media_mutations=3))
    request=pin(tmp_path/'request.json',dict(ozon_media3_decision_ref=decision))
    calls=[]
    def read(ref):calls.append(ref);return dict(validated='finite-B-media3')
    monkeypatch.setitem(sys.modules,'shared_platform.native_ozon_three_card_media_recovery',SimpleNamespace(read_media3_decision=read))
    assert source._native_recovery_media3(request,decision)==sha256(adoption._bytes(dict(validated='finite-B-media3'))).hexdigest()
    with pytest.raises(adoption.AdoptionBlocked,match='MEDIA3_PIN_REQUIRED'):source._native_recovery_media3(request,None)
    Path(decision['path']).write_text('changed')
    with pytest.raises(adoption.AdoptionBlocked,match='IMMUTABLE_PIN_CHANGED'):source._native_recovery_media3(request,decision)
    assert calls==[decision]


def test_idle_pin_cannot_hide_running_target_or_unknown_dispatched_budget(history):
    store,frozen,request,preimage=history;image=json.loads(Path(preimage['path']).read_bytes())
    image['release_target_runs'][1]['status']='RUNNING'
    wrong=pin(Path(preimage['path']).with_name('running.json'),image)
    with pytest.raises(adoption.AdoptionBlocked,match='UNDISPATCHED_FOUR_REQUIRED'):source._native_recovery_history(store,'offline-task',frozen,request,wrong,before_append=True)
    image=json.loads(Path(preimage['path']).read_bytes());image['native_final_budget_outcomes'].pop()
    unknown=pin(Path(preimage['path']).with_name('unknown.json'),image)
    with pytest.raises(adoption.AdoptionBlocked,match='UNKNOWN_INFLIGHT_REQUIRED_RESOLUTION'):source._native_recovery_history(store,'offline-task',frozen,request,unknown,before_append=True)


def test_real_task_cas_signed_once_and_all_prior_events_unchanged(segments,history,monkeypatch):
    from shared_platform.workbench_engine import WorkbenchEngine
    from shared_platform import r3_native_final_approval as actor,release_store
    root,old,mechanism,profile=segments;store,frozen,request,preimage=history;sid='owner';key=b'offline-key'
    engine=WorkbenchEngine(profile.data_root/'tasks.db',old)
    task_id=engine.create(dict(template='publication',source_key='tk-zero-test',scope=dict(offer_id='8412287877065586543',shops=frozen['ordered_targets'],skus=['0431'])))['task_id']
    intent=json.loads(Path(request['path']).read_bytes());intent.update(task_id=task_id,source_head=old['code_version']);request=pin(Path(request['path']),intent)
    image=json.loads(Path(preimage['path']).read_bytes());image['source_head']=old['code_version'];preimage=pin(Path(preimage['path']),image)
    original=dict(schema_version=adoption.SCHEMA,owner_sid=sid,task_id=task_id,offer_id='8412287877065586543');raw=adoption._bytes(original);digest=sha256(raw).hexdigest()
    previous=dict(schema_version=source.PLATFORM_SCHEMA,owner_sid=sid,scope_digest='scope',frozen_candidate=frozen,source_segments=dict(compatibility=dict(new_version=old)));signed=adoption._bytes(previous)
    with engine.transaction() as db:
        db.execute('CREATE TABLE agent_prepared_publication_adoptions (task_id TEXT,record_json TEXT,record_digest TEXT,signature TEXT)')
        db.execute('INSERT INTO agent_prepared_publication_adoptions VALUES(?,?,?,?)',(task_id,raw.decode(),digest,hmac.new(key,raw,'sha256').hexdigest()))
        db.execute("UPDATE workbench_execution SET state='reconciliation_required',step_index=2,action_json=?,checkpoint_json=? WHERE task_id=?",('{"original":"action"}','{"original":"checkpoint"}',task_id))
        engine._event(db,task_id,source.PLATFORM_EVENT,dict(record_json=signed.decode(),record_digest=sha256(signed).hexdigest(),signature=hmac.new(key,signed,'sha256').hexdigest()))
        before=dict(engine._row(db,task_id));events_before=[tuple(r) for r in db.execute('SELECT * FROM workbench_events')]
    monkeypatch.setattr(adoption,'_owner_key',lambda p:(sid,key));monkeypatch.setattr(actor,'read_native_actor_identity',lambda _:dict(identity_verified=True,trust_mode='SAME_WINDOWS_USER',owner_sid=sid))
    monkeypatch.setattr(source,'_verify_platform_compatibility',lambda *a,**k:dict(original='verified historical boundary'))
    monkeypatch.setattr(source,'_native_recovery_media3',lambda *a,**k:'verified-media3-authorization-digest')
    monkeypatch.setattr(release_store,'default_release_store',lambda:store)
    kwargs=dict(task_id=task_id,expected_adoption_digest=digest,actor_reader=object(),mechanism_head=mechanism['code_version'],mechanism_manifest=mechanism['manifest_digest'],technical_request=request,run_preimage=preimage,
        ozon_media3_decision_ref=dict(path='bound-B-media3',sha256='immutable-pin'))
    original_history=source._native_recovery_history
    def raced(*a,**k):
        with engine.transaction() as db:db.execute("UPDATE workbench_execution SET action_json='{}' WHERE task_id=?",(task_id,))
        return original_history(*a,**k)
    monkeypatch.setattr(source,'_native_recovery_history',raced)
    with pytest.raises(adoption.AdoptionBlocked,match='CAS_CHANGED'):source.continue_native_tiktok_ozon_recovery_source(engine,profile,store,**kwargs)
    with engine.transaction() as db:db.execute('UPDATE workbench_execution SET action_json=? WHERE task_id=?',(before['action_json'],task_id))
    monkeypatch.setattr(source,'_native_recovery_history',original_history);source.continue_native_tiktok_ozon_recovery_source(engine,profile,store,**kwargs)
    with engine.transaction() as db:
        after=dict(engine._row(db,task_id));assert {k:v for k,v in before.items() if k!='version_json'}=={k:v for k,v in after.items() if k!='version_json'}
        assert [tuple(r) for r in db.execute('SELECT * FROM workbench_events ORDER BY id')][:-1]==events_before
        task=engine._project(db,engine._row(db,task_id));rows=db.execute('SELECT detail_json FROM workbench_events WHERE event_type=?',(source.NATIVE_RECOVERY_EVENT,)).fetchall()
        assert source._verify_native_recovery_source(task,profile,original,connection=db,owner_sid=sid,key=key,events=[],rows=rows)==dict(original='verified historical boundary')
    with pytest.raises(adoption.AdoptionBlocked,match='SINGLE_EVENT'):source.continue_native_tiktok_ozon_recovery_source(engine,profile,store,**kwargs)
