"""Second source compatibility fix retains a real terminal read-only options GET."""
from dataclasses import replace
import hashlib
import json
import subprocess
import sys
import pytest

from test_native_unfrozen_source_successor import _owned,_register,_digest
from test_round1_initial_options import initial
from test_worker_category_parent_flow import _selection
from shared_platform import native_unfrozen_source_successor as successor
from shared_platform.operations_runtime import runtime_manifest
from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.worker_category_bridge import WorkerCategoryBridge
from shared_platform.worker_category_parent_flow import ParentR1CategoryFlow
from shared_platform.worker_category_facts_transport import VerifiedFactsCategoryTransport
from modules.products import server


def _case(tmp_path,monkeypatch):
    body,_,fake,domain=initial(tmp_path/'domain',monkeypatch)
    domain.path.parent.mkdir(parents=True,exist_ok=True)
    with domain._connect() as db:domain._ensure_schema(db)
    values=_owned(tmp_path/'source-case',offer=body['offer_id'],shops=body['requested_targets'])
    _register(values)
    engine,profile,*_=values
    engine.register_executor('worker',['publication'],engine.release)
    task_id=values[3]['task_id'];token=engine.claim(task_id,'worker')['lease_token']
    bridge=WorkerCategoryBridge(engine,domain,lambda action,request,origin:server._round1_category_initial_request(action,request,worker_origin=origin))
    flow=ParentR1CategoryFlow(bridge,VerifiedFactsCategoryTransport(engine,domain,worker_id='worker'),worker_id='worker')
    context={k:body[k] for k in ('context_digest','offer_id','product_center_revision','requested_targets','source_region','account_identity_digest')}
    options=flow.options(engine.get(task_id),lease_token=token,context=context)
    assert options['status']=='SUCCEEDED' and options['dispatched_this_call'] and len(fake.calls)==5
    from shared_platform.native_readonly_invocation import prepare_readonly_invocation
    from shared_platform.readonly_agent_config_guard import ReadonlyAgentConfigBlocked
    with pytest.raises(ReadonlyAgentConfigBlocked,match='readonly_native_binary_capability_unverified'):
        prepare_readonly_invocation(sys.executable,profile.root)
    cp={'native_preparation':{'product_center_revision':context['product_center_revision']},'facts_attempt':{
        'number':2,'state':'returned','output':str(profile.data_root/'artifacts'/task_id/'facts-2'),
        'inputs':'actual-packet-fingerprint','input_digest':_digest([]),
        'result':{'status':'blocked','reason':'readonly_native_binary_capability_unverified'}}}
    engine.record_checkpoint(task_id,token,cp);engine.fail(task_id,token,cp['facts_attempt']['result']['reason'])
    with engine.readonly_snapshot() as db:
        row=engine._row(db,task_id)
        history=successor._history(db,task_id)
    (profile.root/'shared_platform/source-fix.py').write_text('# actual second compatibility source\n')
    subprocess.run(['git','-C',str(profile.root),'add','--','shared_platform/source-fix.py'],check=True,capture_output=True)
    subprocess.run(['git','-C',str(profile.root),'-c','user.name=Owned fixture','-c','user.email=fixture@example.invalid',
        '-c','commit.gpgsign=false','commit','-qm','Second actual compatibility source'],check=True,capture_output=True)
    version=subprocess.check_output(['git','-C',str(profile.root),'rev-parse','HEAD'],text=True).strip()
    new=replace(profile,version=version,manifest_digest=runtime_manifest(profile.root))
    current=WorkbenchEngine(engine.store.path,dict(code_version=new.version,environment=new.environment,manifest_digest=new.manifest_digest))
    expected=dict(source_key='original-user-request',request_digest=row['request_digest'],from_release=engine.release,
        scope=json.loads(row['scope_json']),checkpoint_sha256=hashlib.sha256(row['checkpoint_json'].encode()).hexdigest(),history_sha256=_digest(history))
    return current,new,domain,task_id,expected,context,options,fake,row['checkpoint_json']


def test_real_second_link_reuses_original_options_without_get_and_capture_stays_current(tmp_path,monkeypatch):
    engine,profile,domain,tid,expected,context,options,fake,cp=_case(tmp_path,monkeypatch)
    record=successor.register_source_successor(engine,tid,profile=profile,release_store=domain,expected=expected)
    assert record['zero_evidence']['operations']['workbench_category_intents']==1
    assert record['zero_evidence']['confirmed_readonly_options']['intent']['status']=='SUCCEEDED'
    with engine.readonly_snapshot() as db:assert engine._row(db,tid)['checkpoint_json']==cp
    # Actual newly provided technical input, no product revision/old checkpoint rewrite.
    with engine.transaction() as db:
        engine._event(db,tid,'input_provided',{'source':'LOCAL_TECHNICAL_DEPENDENCY','actor':'root',
            'approval':False,'note':'Owned proven new executable dependency is now installed'})
        engine._state(db,tid,'queued','')
    engine.register_executor('worker',['publication'],engine.release)
    token=engine.claim(tid,'worker')['lease_token']
    bridge=WorkerCategoryBridge(engine,domain,lambda action,request,origin:server._round1_category_initial_request(action,request,worker_origin=origin))
    flow=ParentR1CategoryFlow(bridge,VerifiedFactsCategoryTransport(engine,domain,worker_id='worker'),worker_id='worker')
    task=engine.get(tid)
    reused=flow.options(task,lease_token=token,context=context)
    assert reused['projection']==options['projection'] and not reused['dispatched_this_call'] and len(fake.calls)==5
    from shared_platform.worker_category_agent_attempts import CategoryAgentAttemptLedger
    child=CategoryAgentAttemptLedger().reserve(engine,task=task,worker_id='worker',lease_token=token,
        stage='choice',input_binding={'options_request_id':reused['request_id'],
            'options_reference':reused['projection']['options_reference'],
            'options_digest':reused['projection']['options_digest']},
        output_path=str(profile.data_root/'artifacts'/tid/'facts-3'/'category-choice'))
    assert child['started_this_call']
    with engine.readonly_snapshot() as db:
        attempt=db.execute("SELECT * FROM workbench_category_agent_attempts WHERE task_id=? AND stage='choice'",(tid,)).fetchone()
        assert json.loads(attempt['release_json'])==engine.release
    captured=flow.capture(task,lease_token=token,selection=_selection(reused['projection']))
    assert captured['status']=='SUCCEEDED' and captured['dispatched_this_call'] and len(fake.calls)==7
    assert domain.category_worker_origin(captured['request_id'])['release']==engine.release
    assert engine.explicit_new_post_task_ids()==(tid,)


@pytest.mark.parametrize('change',['unknown','context','third-link'])
def test_registered_link_cannot_adopt_unknown_changed_context_or_third_source(tmp_path,monkeypatch,change):
    engine,profile,domain,tid,expected,context,options,fake,cp=_case(tmp_path,monkeypatch)
    successor.register_source_successor(engine,tid,profile=profile,release_store=domain,expected=expected)
    if change=='third-link':
        with pytest.raises(ValueError,match='SOURCE_SUCCESSOR_MULTIPLE_CHAINS_REJECTED'):
            successor.register_source_successor(engine,tid,profile=profile,release_store=domain,expected=expected)
        assert len(fake.calls)==5
        return
    with engine.transaction() as db:engine._state(db,tid,'queued','')
    engine.register_executor('worker',['publication'],engine.release)
    token=engine.claim(tid,'worker')['lease_token']
    if change=='unknown':
        with engine.transaction() as db:db.execute("UPDATE workbench_category_intents SET status='UNKNOWN' WHERE task_id=?",(tid,))
    else:context['product_center_revision']+=1
    bridge=WorkerCategoryBridge(engine,domain,lambda *a:pytest.fail('Never dispatch changed original options'))
    flow=ParentR1CategoryFlow(bridge,VerifiedFactsCategoryTransport(engine,domain,worker_id='worker'),worker_id='worker')
    with pytest.raises(ValueError):flow.options(engine.get(tid),lease_token=token,context=context)
    assert len(fake.calls)==5


@pytest.mark.parametrize('change',['unknown','capture','checkpoint'])
def test_second_link_rejects_unknown_capture_and_other_failure(tmp_path,monkeypatch,change):
    engine,profile,domain,tid,expected,context,options,fake,cp=_case(tmp_path,monkeypatch)
    with engine.transaction() as db:
        if change=='unknown':db.execute("UPDATE workbench_category_intents SET status='UNKNOWN' WHERE task_id=?",(tid,))
        elif change=='capture':
            db.execute("INSERT INTO workbench_category_intents SELECT request_id||'-bad',task_id,'capture',body_json,release_json,status,result_reference,options_digest FROM workbench_category_intents WHERE task_id=?",(tid,))
        else:
            data=json.loads(cp);data['facts_attempt']['result']['reason']='other_failure'
            raw=json.dumps(data);db.execute('UPDATE workbench_execution SET checkpoint_json=? WHERE task_id=?',(raw,tid))
            expected['checkpoint_sha256']=hashlib.sha256(raw.encode()).hexdigest()
    with pytest.raises(ValueError,match='SOURCE_SUCCESSOR_'):
        successor.register_source_successor(engine,tid,profile=profile,release_store=domain,expected=expected)
    assert len(fake.calls)==5
