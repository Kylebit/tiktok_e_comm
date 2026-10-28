"""Offline source/HMAC/CAS and approval preservation; no provider or live store."""
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import hmac,json,sqlite3,subprocess
from pathlib import Path
import pytest
from shared_platform import agent_prepared_source_successor as source,agent_prepared_publication_adoption as adoption
from shared_platform.operations_runtime import RuntimeProfile,runtime_manifest


def git(root,*args):
    return subprocess.check_output(['git','-C',str(root),'-c','user.name=Offline test','-c',
        'user.email=offline@example.test',*args],text=True,stderr=subprocess.DEVNULL).strip()


@pytest.fixture
def segments(tmp_path):
    root=tmp_path/'source';root.mkdir();git(root,'init')
    for name in source.R1_READER_MECHANISM_FILES|source.R1_READER_FILES:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('old\n')
    git(root,'add','.');git(root,'commit','-m','approved UI source')
    old=dict(code_version=git(root,'rev-parse','HEAD'),environment='stable',manifest_digest=runtime_manifest(root))
    for name in source.R1_READER_MECHANISM_FILES:(root/name).write_text('reviewed mechanism\n')
    git(root,'add','.');git(root,'commit','-m','mechanism only')
    mechanism=dict(code_version=git(root,'rev-parse','HEAD'),environment='stable',manifest_digest=runtime_manifest(root))
    for name in source.R1_READER_FILES:(root/name).write_text('adoption reader only\n')
    git(root,'add','.');git(root,'commit','-m','reader only')
    profile=RuntimeProfile(root,tmp_path/'ops','stable',git(root,'rev-parse','HEAD'),runtime_manifest(root))
    return root,old,mechanism,profile


class Store:
    def __init__(self,path):self.path=path
    @contextmanager
    def _connect_readonly(self):
        db=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
        try:yield db
        finally:db.close()


def pin(path,value):
    path.write_bytes(adoption._bytes(value));return dict(path=str(path),sha256=sha256(path.read_bytes()).hexdigest())


@pytest.fixture
def approved(tmp_path):
    store=Store(tmp_path/'release.db');sid='offline-owner';plan='original-plan';review='{"frozen":"review"}'
    digest='sha256:'+sha256(review.encode()).hexdigest();decision='native-final-decision:'+sha256(review.encode()).hexdigest()
    rows=dict(plan=dict(plan_id=plan,product_id='offer',status='APPROVED',payload_digest='payload',confirmation_token='token'),
        decision=dict(plan_id=plan,decision_id=decision,owner_sid=sid,review_digest=digest,review_json=review,approval_id='approval',snapshot_digest='approved-snapshot'),
        approval=dict(plan_id=plan,approval_id='approval',status='APPROVED',user_approved=1,approved_by='Kyle',payload_digest='payload',confirmation_token='token'),
        approved_publication_snapshots=dict(plan_id=plan,snapshot_digest='approved-snapshot',snapshot_json='原始已批准快照',product_revision=7,nullable=None),
        publication_business_snapshots=dict(plan_id=plan,snapshot_digest='business-snapshot',snapshot_json='original-business-bytes'))
    tables=dict(plan='release_plans',decision='native_sole_final_decisions',approval='release_approvals')
    with sqlite3.connect(store.path) as db:
        for name,row in rows.items():
            table=tables.get(name,name);db.execute('CREATE TABLE '+table+' ('+','.join(k+(' INTEGER' if type(v) is int else ' TEXT') for k,v in row.items())+')')
            db.execute('INSERT INTO '+table+' VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))
        for table in ('release_runs','release_sku_reservations','native_final_budget_scopes'):
            db.execute('CREATE TABLE '+table+' (plan_id TEXT)')
    def proof(row):return dict(column_names=list(row),row_sha256=sha256(json.dumps(row,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        column_raw_sha256={k:sha256(v.encode()).hexdigest() for k,v in row.items() if type(v) is str})
    preimage=dict(schema_version='post-approval-preservation-preimage/v1',decision_id=decision,plan_id=plan,task=dict(task_id='offline-task'),source_head='old-head',
        **{name:proof(rows[name]) for name in ('plan','decision','approval')},
        approved_snapshot_rows={name:dict(rows=[proof(rows[name])]) for name in ('approved_publication_snapshots','publication_business_snapshots')})
    order=dict(schema='orbit-new-product-b-link-work-order/v1',revision=46,source=dict(new_numeric_B_offer_id='offer',HEAD='old-head'),native_task=dict(task_id='offline-task'),
        authority=dict(current_durable_native_decision=dict(decision_id=decision,review_digest=digest,verified_plan_status='APPROVED',
            approval_count=1,decision_count=1,release_run_count=0)))
    return store,sid,dict(plan_id=plan,candidate_digest='c5',native_review_digest=digest),pin(tmp_path/'order.json',order),pin(tmp_path/'preimage.json',preimage)


def test_exact_source_segments_reject_unrelated_or_mechanism_edits_after_reader(segments):
    root,old,mechanism,profile=segments
    result=source._r1_reader_segments(profile,old,mechanism)
    assert set(result['reader']['changed_files'])==source.R1_READER_FILES
    (root/'scripts/continue_agent_prepared_source.py').write_text('slipped change\n')
    git(root,'add','.');git(root,'commit','-m','forbidden mechanism after reader')
    changed=RuntimeProfile(root,profile.data_root,'stable',git(root,'rev-parse','HEAD'),runtime_manifest(root))
    with pytest.raises(adoption.AdoptionBlocked):source._r1_reader_segments(changed,old,mechanism)


def test_historical_ui_git_segments_survive_only_as_authenticated_old_segments(tmp_path):
    root=tmp_path/'ui';root.mkdir();git(root,'init')
    for name in source.UI_MECHANISM_FILES|source.UI_FILES:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('before\n')
    git(root,'add','.');git(root,'commit','-m','gallery source')
    old=dict(code_version=git(root,'rev-parse','HEAD'),environment='stable',manifest_digest=runtime_manifest(root))
    for name in source.UI_MECHANISM_FILES:(root/name).write_text('UI mechanism\n')
    git(root,'add','.');git(root,'commit','-m','UI mechanism')
    mechanism=dict(code_version=git(root,'rev-parse','HEAD'),environment='stable',manifest_digest=runtime_manifest(root))
    for name in source.UI_FILES:(root/name).write_text('frozen display\n')
    git(root,'add','.');git(root,'commit','-m','UI only')
    ui_profile=RuntimeProfile(root,tmp_path/'data','stable',git(root,'rev-parse','HEAD'),runtime_manifest(root))
    proof=source._ui_source_segments(ui_profile,old,mechanism)
    (root/'shared_platform/agent_prepared_source_successor.py').write_text('later reader mechanism\n')
    git(root,'add','.');git(root,'commit','-m','later backend mechanism')
    current=RuntimeProfile(root,tmp_path/'data','stable',git(root,'rev-parse','HEAD'),runtime_manifest(root))
    assert source._historical_ui_segments(current,old,proof)==proof
    changed=deepcopy(proof);changed['frontend']['changed_files']['web/static/publication_original_adapter.js']='0'*64
    with pytest.raises(adoption.AdoptionBlocked):source._historical_ui_segments(current,old,changed)


def test_approved_rows_are_permanent_but_zero_execution_is_append_only(approved):
    store,sid,frozen,order,preimage=approved
    expected=source._r1_reader_approved(store,'offer',sid,frozen,order,preimage,task_id='offline-task',before_append=True)
    with sqlite3.connect(store.path) as db:
        for table in ('release_runs','release_sku_reservations','native_final_budget_scopes'):
            db.execute('INSERT INTO '+table+' VALUES (?)',(frozen['plan_id'],))
    assert source._r1_reader_approved(store,'offer',sid,frozen,order,preimage,task_id='offline-task')==expected
    with pytest.raises(adoption.AdoptionBlocked,match='ZERO_RUN_BUDGET_DISPATCH'):
        source._r1_reader_approved(store,'offer',sid,frozen,order,preimage,task_id='offline-task',before_append=True)
    with sqlite3.connect(store.path) as db:db.execute("UPDATE approved_publication_snapshots SET snapshot_json='tampered'")
    with pytest.raises(adoption.AdoptionBlocked,match='ORIGINAL_APPROVAL_BYTES'):
        source._r1_reader_approved(store,'offer',sid,frozen,order,preimage,task_id='offline-task')


def test_event_hmac_full_task_cas_once_and_consumption_after_run(segments,approved,monkeypatch):
    from shared_platform.workbench_engine import WorkbenchEngine
    from shared_platform import release_store,r3_native_final_approval as actor
    root,old,mechanism,profile=segments;store,sid,frozen,order,preimage=approved;key=b'offline-signing-key'
    engine=WorkbenchEngine(profile.data_root/'tasks.db',old)
    task_id=engine.create(dict(template='publication',source_key='approved-reader-test',scope=dict(offer_id='offer',shops=['api-shop'],skus=['0431'])))['task_id']
    order_doc=json.loads(Path(order['path']).read_bytes());order_doc['native_task']['task_id']=task_id;order=pin(Path(order['path']),order_doc)
    image_doc=json.loads(Path(preimage['path']).read_bytes());image_doc['task']['task_id']=task_id;preimage=pin(Path(preimage['path']),image_doc)
    original=dict(schema_version=adoption.SCHEMA,owner_sid=sid,task_id=task_id,offer_id='offer');raw=adoption._bytes(original)
    original_digest=sha256(raw).hexdigest();scope=engine.get(task_id)['scope']
    prior=dict(preserved={'original':'R1-R2-COMMON'},api_target_selection={'reduction_digest':'api9'})
    ui=dict(schema_version=source.UI_SCHEMA,owner_sid=sid,scope_digest=sha256(adoption._bytes(scope)).hexdigest(),
        api_selection_digest='api9',frozen_candidate=frozen,source_segments=dict(frontend=dict(new_version=old)))
    ui_raw=adoption._bytes(ui);envelope=dict(record_json=ui_raw.decode(),record_digest=sha256(ui_raw).hexdigest(),signature=hmac.new(key,ui_raw,'sha256').hexdigest())
    with engine.transaction() as db:
        db.execute('CREATE TABLE agent_prepared_publication_adoptions (task_id TEXT,record_json TEXT,record_digest TEXT,signature TEXT)')
        db.execute('INSERT INTO agent_prepared_publication_adoptions VALUES(?,?,?,?)',(task_id,raw.decode(),original_digest,hmac.new(key,raw,'sha256').hexdigest()))
        db.execute("UPDATE workbench_execution SET state='waiting_domain',step_index=2,action_json=?,checkpoint_json=? WHERE task_id=?",('{"original":"action"}','{"original":"checkpoint"}',task_id))
        for n in range(2):engine._event(db,task_id,source.GALLERY_EVENT,dict(record_digest=str(n)))
        engine._event(db,task_id,source.UI_EVENT,envelope)
        before=dict(engine._row(db,task_id));old_events=[tuple(r) for r in db.execute('SELECT * FROM workbench_events')]
    monkeypatch.setattr(adoption,'_owner_key',lambda p:(sid,key))
    monkeypatch.setattr(actor,'read_native_actor_identity',lambda _:dict(identity_verified=True,trust_mode='SAME_WINDOWS_USER',owner_sid=sid))
    monkeypatch.setattr(source,'_ui_frozen_candidate',lambda *a,**k:deepcopy(frozen))
    monkeypatch.setattr(release_store,'default_release_store',lambda:store)
    verify=source.verify_completed_gallery_current
    # The existing six-source/two-gallery/UI verifier is a tested boundary here.
    monkeypatch.setattr(source,'verify_completed_gallery_current',lambda *a,**k:deepcopy(prior) if k.get('historical') else verify(*a,**k))
    kwargs=dict(task_id=task_id,expected_adoption_digest=original_digest,actor_reader=object(),
        mechanism_head=mechanism['code_version'],mechanism_manifest=mechanism['manifest_digest'],candidate_digest='c5',work_order=order,preimage=preimage)
    def race(*a,**k):
        with engine.transaction() as db:db.execute("UPDATE workbench_execution SET action_json='{}' WHERE task_id=?",(task_id,))
        return deepcopy(frozen)
    monkeypatch.setattr(source,'_ui_frozen_candidate',race)
    with pytest.raises(adoption.AdoptionBlocked,match='CAS_CHANGED'):source.continue_approved_native_r1_reader(engine,profile,store,**kwargs)
    with engine.transaction() as db:
        assert not db.execute('SELECT 1 FROM workbench_events WHERE event_type=?',(source.R1_READER_EVENT,)).fetchone()
        db.execute('UPDATE workbench_execution SET action_json=? WHERE task_id=?',(before['action_json'],task_id))
    monkeypatch.setattr(source,'_ui_frozen_candidate',lambda *a,**k:deepcopy(frozen))
    with engine.transaction() as db:
        db.execute("INSERT INTO workbench_domain_operations VALUES ('offline-inflight','[]',?,'inflight','','now')",(task_id,))
    with pytest.raises(adoption.AdoptionBlocked,match='CAS_CHANGED'):source.continue_approved_native_r1_reader(engine,profile,store,**kwargs)
    with engine.transaction() as db:db.execute("DELETE FROM workbench_domain_operations WHERE operation_id='offline-inflight'")
    source.continue_approved_native_r1_reader(engine,profile,store,**kwargs)
    with sqlite3.connect(store.path) as db:db.execute('INSERT INTO release_runs VALUES (?)',(frozen['plan_id'],))
    with engine.transaction() as db:
        after=dict(engine._row(db,task_id));assert {k:v for k,v in before.items() if k!='version_json'}=={k:v for k,v in after.items() if k!='version_json'}
        assert [tuple(r) for r in db.execute('SELECT * FROM workbench_events ORDER BY id')][:-1]==old_events
        current=engine._project(db,engine._row(db,task_id));events=db.execute('SELECT detail_json FROM workbench_events WHERE event_type=?',(source.GALLERY_EVENT,)).fetchall()
        assert verify(current,profile,original,connection=db,owner_sid=sid,key=key,events=events)==prior
        signed=json.loads(db.execute('SELECT detail_json FROM workbench_events WHERE event_type=?',(source.R1_READER_EVENT,)).fetchone()[0]);signed['signature']='0'*64
        db.execute('UPDATE workbench_events SET detail_json=? WHERE event_type=?',(json.dumps(signed),source.R1_READER_EVENT))
        with pytest.raises(adoption.AdoptionBlocked,match='SIGNED_BINDING'):verify(current,profile,original,connection=db,owner_sid=sid,key=key,events=events)
    with pytest.raises(adoption.AdoptionBlocked,match='SINGLE_EVENT'):source.continue_approved_native_r1_reader(engine,profile,store,**kwargs)
