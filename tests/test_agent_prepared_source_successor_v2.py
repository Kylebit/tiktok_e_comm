"""Second source link preserves signed v1 and UNKNOWN1 under the same Task."""
from copy import deepcopy
import json,sqlite3
from pathlib import Path
from types import SimpleNamespace
import pytest
from shared_platform import agent_prepared_source_successor as source
from shared_platform import agent_prepared_publication_adoption as adoption
from shared_platform import manual_common_creation as manual
from shared_platform import manual_common_successor as child
from shared_platform.release_store import ReleaseStore
from shared_platform.workbench_engine import WorkbenchEngine
from test_agent_prepared_adoption import prepared,_adopt
from test_manual_common_creation import intent
from test_agent_prepared_source_successor import git
from shared_platform.operations_runtime import runtime_manifest

@pytest.fixture
def second(prepared,monkeypatch):
    result=_adopt(prepared);engine,profile,facts=prepared
    record=adoption.verify(engine.get(result['task_id']),profile,offer='8412287877065586543',targets=['shopee:PH'],
        prepared_reference=facts['prepared_reference'],snapshot_digest=facts['snapshot_digest'])
    store=ReleaseStore(profile.root/'release.db');manual.initialize_schema(store.path)
    ledger=manual.ManualCommonCreateLedger(store.path);payload=intent()
    payload.update(owner_sid=record['owner_sid'],round1_digest=facts['snapshot_digest'],
        source_identity_binding_sha256=facts['source_identity_binding_sha256'],target_labels=record['targets'])
    parent=ledger.reserve(payload)
    monkeypatch.setattr(source,'source_evidence',lambda p,v,**kw:dict(old_version=v,new_version=source._version(p),changed_files={'synthetic':'digest'}))
    first_profile=SimpleNamespace(root=profile.root,data_root=profile.data_root,environment='stable',version='first-source',manifest_digest='first-manifest')
    first_engine=WorkbenchEngine(profile.data_root/'tasks.db',source._version(first_profile))
    first=source.continue_source(first_engine,first_profile,store,task_id=result['task_id'],expected_adoption_digest=result['adoption_digest'],actor_reader=object())
    with pytest.raises(manual.ManualCommonBlocked):ledger.create(parent['intent_id'],post_create=lambda _:(_ for _ in ()).throw(TimeoutError()),read_detail=lambda _:None)
    parent=ledger.read(parent['intent_id'])
    decision=dict(schema_version=child.DECISION_SCHEMA,action_class='AUTHORIZE_ONE_COMMON_CREATE_SUCCESSOR',actor='Kyle',
        maximum_new_create_attempts=1,combined_create_attempt_cap=2,original_unknown_retained=True,
        baseline_edit_attempt_cap_unchanged=1,same_task_and_preparation_root_required=True,
        candidate_content_changes_authorized=False,marketplace_approval=False,standing_authority=False,
        task_id=result['task_id'],offer_id=record['offer_id'],original_intent_id=parent['intent_id'],
        original_adoption_digest=result['adoption_digest'],round1_snapshot_digest=payload['round1_digest'],
        source_identity_binding_sha256=payload['source_identity_binding_sha256'],seller_sku=payload['seller_sku'],
        model_skus=payload['model_skus'],targets=payload['target_labels'],canonical_skus=sorted(facts['aliases']))
    pin=profile.root/'decision.json';pin.write_text(json.dumps(decision))
    reader=child.PinnedCommonSuccessorDecision(pin,manual.sha256(pin.read_bytes()).hexdigest())
    current=SimpleNamespace(root=profile.root,data_root=profile.data_root,environment='stable',version='second-source',manifest_digest='second-manifest')
    current_engine=WorkbenchEngine(profile.data_root/'tasks.db',source._version(current))
    monkeypatch.setattr(source,'verify_historical_source',lambda *a,**kw:None)
    from shared_platform import release_store
    monkeypatch.setattr(release_store,'default_release_store',lambda:store)
    return current_engine,current,store,result,record,parent,reader,first

def run(v):
    engine,profile,store,result,_,_,reader,_=v
    return source.continue_second_source(engine,profile,store,task_id=result['task_id'],expected_adoption_digest=result['adoption_digest'],actor_reader=object(),decision_reader=reader)

def test_second_link_keeps_original_adoption_v1_event_intent_and_checkpoint(second):
    engine,profile,store,result,original,parent,_,_=second
    before=engine.get(result['task_id']);ledger_bytes=store.path.read_bytes()
    with engine.transaction() as db:
        v1=tuple(db.execute('SELECT * FROM agent_prepared_source_successors').fetchone())
        original_row=tuple(db.execute('SELECT * FROM agent_prepared_publication_adoptions').fetchone())
        events=[tuple(x) for x in db.execute("SELECT * FROM workbench_events WHERE event_type='source_code_successor'")]
    record=run(second)
    assert record['original_manual_intent_sha256']==manual._digest(parent)
    assert engine.get(result['task_id'])['checkpoint']==before['checkpoint'] and store.path.read_bytes()==ledger_bytes
    assert engine.get(result['task_id'])['version']==source._version(profile)
    assert run(second)==record
    assert adoption.verify(engine.get(result['task_id']),profile,offer=original['offer_id'],targets=original['targets'],
        prepared_reference=original['facts']['prepared_reference'],snapshot_digest=original['facts']['snapshot_digest'])==original
    with engine.transaction() as db:
        assert tuple(db.execute('SELECT * FROM agent_prepared_source_successors').fetchone())==v1
        assert tuple(db.execute('SELECT * FROM agent_prepared_publication_adoptions').fetchone())==original_row
        assert [tuple(x) for x in db.execute("SELECT * FROM workbench_events WHERE event_type='source_code_successor'")]==events
        assert db.execute('SELECT count(*) FROM workbench_tasks').fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM workbench_events WHERE event_type='source_code_successor_v2'").fetchone()[0]==1
        with pytest.raises(sqlite3.IntegrityError):db.execute('DELETE FROM agent_prepared_source_successors_v2')

@pytest.mark.parametrize('change',['caller_reader','decision_pin','active','task_version','changed_paid','bad_adoption','missing_event','untrusted_actor'])
def test_second_link_refuses_unbound_or_active_state(second,monkeypatch,change):
    engine,profile,store,result,original,parent,reader,_=second
    if change=='caller_reader':second=list(second);second[6]={'approved':True}
    elif change=='decision_pin':reader.path.write_text('{}')
    elif change=='active':
        with engine.transaction() as db:db.execute("UPDATE workbench_execution SET worker='other',lease_token='held'")
    elif change=='task_version':
        with engine.transaction() as db:db.execute("UPDATE workbench_execution SET version_json='{}'")
    elif change=='changed_paid':
        facts=deepcopy(original['facts']);facts['paid_summary']['occupied']+=1
        monkeypatch.setattr(adoption,'_verify_sources',lambda *a,**kw:facts)
    elif change=='bad_adoption':result['adoption_digest']='f'*64
    elif change=='untrusted_actor':
        from shared_platform import r3_native_final_approval as actor
        monkeypatch.setattr(actor,'read_native_actor_identity',lambda _:dict(identity_verified=True,trust_mode='CALLER_JSON',owner_sid=original['owner_sid']))
    else:
        with engine.transaction() as db:db.execute("DELETE FROM workbench_events WHERE event_type='source_code_successor'")
    before=engine.get(result['task_id'])['version']
    with pytest.raises((adoption.AdoptionBlocked,manual.ManualCommonBlocked)):run(second)
    assert engine.get(result['task_id'])['version']==before

def test_real_git_historical_proof_stays_valid_after_another_technical_commit(tmp_path):
    git(tmp_path,'init');p=tmp_path/'shared_platform/agent_prepared_common_service.py';p.parent.mkdir();p.write_text('old')
    git(tmp_path,'add','.');git(tmp_path,'commit','-m','original')
    old=dict(code_version=git(tmp_path,'rev-parse','HEAD'),environment='stable',manifest_digest=runtime_manifest(tmp_path))
    p.write_text('first technical fix');git(tmp_path,'add','.');git(tmp_path,'commit','-m','first')
    new=dict(code_version=git(tmp_path,'rev-parse','HEAD'),environment='stable',manifest_digest=runtime_manifest(tmp_path))
    proof=dict(old_version=old,new_version=new,changed_files={'shared_platform/agent_prepared_common_service.py':manual.sha256(p.read_bytes()).hexdigest()})
    p.write_text('second technical fix');git(tmp_path,'add','.');git(tmp_path,'commit','-m','second')
    source.verify_historical_source(tmp_path,proof,allowed_files=source.ALLOWED_FILES)
    bad=deepcopy(proof);bad['changed_files']['shared_platform/agent_prepared_common_service.py']='f'*64
    with pytest.raises(adoption.AdoptionBlocked):source.verify_historical_source(tmp_path,bad,allowed_files=source.ALLOWED_FILES)

def test_actual_cli_exposes_120s_wait_without_retry(monkeypatch):
    import importlib.util
    path=Path(__file__).resolve().parents[1]/'skills/publish-approved-product/scripts/prepare_publication_execution.py'
    spec=importlib.util.spec_from_file_location('successor_cli',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    from shared_platform import publication_r3_image_bridge as bridge
    seen=[]
    monkeypatch.setattr(bridge,'request_publication_stage',lambda *a,**kw:(seen.append(kw) or (200,{'ok':True})))
    assert module.main(['--offer-id','8412287877065586543','--base-url','http://127.0.0.1:12345','--request-timeout','120'])==0
    assert len(seen)==1 and seen[0]['timeout']==120
