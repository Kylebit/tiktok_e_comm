import hashlib
import pytest

from modules.products import server
from shared_platform import common_config_recovery as recovery
from shared_platform import operations_domain_guard as guard
from shared_platform import operations_publication_common as common
from test_operations_publication_common import setup, claim
from test_b4b_common_stage import approve


def failed(tmp_path, monkeypatch):
    engine, task, profile, transport, request, store = setup(
        tmp_path,monkeypatch,synthetic_technical_authority=True)
    monkeypatch.setattr(guard,'engine_for',lambda root:engine)
    exact=approve(request)
    # Exercise real original server/guard; fail before the first transport.
    from modules.miaoshou import client
    monkeypatch.setattr(client,'post_open',lambda *a,**kw: (_ for _ in ()).throw(FileNotFoundError(recovery.ERROR)))
    common.run(engine,engine.get(task),claim(engine,task),profile,server_module=server)
    run=store.get_run('release-run:'+store.get_plan(exact['plan_id'])['payload_digest'][:24])
    assert run['targets'][0]['error']==recovery.ERROR
    assert engine.get(task)['execution_state']=='reconciliation_required'
    # Source admission fixture only; production hashes/version remain pinned.
    root=tmp_path/'audited-code';root.mkdir();(root/'client.txt').write_text('audited-test-code')
    monkeypatch.setattr(recovery,'SOURCES',{'client.txt':hashlib.sha256(b'audited-test-code').hexdigest()})
    monkeypatch.setattr(recovery,'VERSION','fixture')
    args=dict(store=store,engine=engine,task_id=task,run_id=run['run_id'],source_root=root,verified_by='independent-test')
    return args,transport,request,profile


def test_recovery_preserves_attempt_then_real_common_second_attempt(tmp_path,monkeypatch):
    args,transport,request,profile=failed(tmp_path,monkeypatch)
    engine,store,task=args['engine'],args['store'],args['task_id']
    proof=recovery.reconcile_historical_config_failure(**args)
    assert recovery.reconcile_historical_config_failure(**args)==proof
    target=store.get_run(args['run_id'])['targets'][0]
    assert target['status']=='FAILED' and target['attempts']==1 and target['readback'] is None
    assert len(target['failure_events'])==1
    assert store.common_reconciliation_reference(request['offer_id']) is None
    assert engine.get(task)['execution_state']=='queued'
    from modules.miaoshou import client
    monkeypatch.setattr(client,'post_open',transport.post)
    # The narrow fixture has no marketplace configuration. The worker now
    # records a failed task instead of propagating that failure as ValueError.
    assert common.run(engine,engine.get(task),claim(engine,task),profile,server_module=server) is False
    assert engine.get(task)['execution_state']=='waiting_domain'
    target=store.get_run(args['run_id'])['targets'][0]
    assert target['status']=='SUCCEEDED' and target['attempts']==2
    assert len(target['failure_events'])==1 and transport.mutations==1
    with engine.transaction() as db:
        rows=db.execute('SELECT operation_id,readback_ref FROM workbench_domain_operations').fetchall()
    assert len(rows)==2
    assert any(row['readback_ref'].startswith('local-not-dispatched:') for row in rows)
    assert any(row['operation_id'].endswith(':attempt:2') for row in rows)


@pytest.mark.parametrize('damage',['source','version','error','external_id','attempt','submission'])
def test_recovery_rejects_unproved_outcome(tmp_path,monkeypatch,damage):
    args,transport,request,profile=failed(tmp_path,monkeypatch)
    if damage=='source':(args['source_root']/'client.txt').write_text('changed')
    elif damage=='version':monkeypatch.setattr(recovery,'VERSION','different')
    else:
        with args['store']._transaction() as db:
            if damage=='submission':
                # Any existing readback, even empty/unverified, blocks this path.
                db.execute('INSERT INTO release_target_readbacks VALUES(?,?,?,?,?)',(args['run_id'],'miaoshou:COMMON','{}','digest','now'))
            else:
                field={'error':'error','external_id':'external_id','attempt':'attempts'}[damage]
                value={'error':'network timeout','external_id':'123','attempt':2}[damage]
                db.execute(f'UPDATE release_target_runs SET {field}=? WHERE run_id=?',(value,args['run_id']))
    with pytest.raises(ValueError):recovery.reconcile_historical_config_failure(**args)
    assert args['engine'].get(args['task_id'])['execution_state']=='reconciliation_required'
    assert transport.mutations==0


def test_typed_initial_config_failure_records_zero_request_evidence(tmp_path,monkeypatch):
    args,transport,request,profile=failed(tmp_path,monkeypatch)
    from modules.products.release_adapters import write_miaoshou_common_from_plan
    from modules.miaoshou.client import MiaoshouLocalConfigMissing
    plan=args['store'].get_plan(args['store'].get_run(args['run_id'])['plan_id'])
    def missing(*a,**kw):raise MiaoshouLocalConfigMissing(recovery.ERROR)
    with pytest.raises(MiaoshouLocalConfigMissing) as captured:
        write_miaoshou_common_from_plan(plan['payload'],post=missing)
    assert captured.value.external_write_evidence['request_attempted'] is False
    assert captured.value.external_write_evidence['write_outcome']=='not_dispatched'


def test_second_config_failure_cannot_reuse_first_attempt_proof(tmp_path,monkeypatch):
    args,transport,request,profile=failed(tmp_path,monkeypatch)
    recovery.reconcile_historical_config_failure(**args)
    engine,task,store=args['engine'],args['task_id'],args['store']
    from modules.miaoshou import client
    def still_missing(*a,**kw):raise client.MiaoshouLocalConfigMissing(recovery.ERROR)
    monkeypatch.setattr(client,'post_open',still_missing)
    common.run(engine,engine.get(task),claim(engine,task),profile,server_module=server)
    target=store.get_run(args['run_id'])['targets'][0]
    assert target['attempts']==2 and target['status']=='FAILED'
    assert target['failure_events'][-1]['evidence']['schema_version']=='common-initial-config-failure/v1'
    assert engine.get(task)['execution_state']=='reconciliation_required'
    with pytest.raises(ValueError):recovery.reconcile_historical_config_failure(**args)
    with engine.transaction() as db:
        second=db.execute("SELECT state FROM workbench_domain_operations WHERE operation_id LIKE '%:attempt:2'").fetchone()
    assert second['state']=='inflight'
    assert transport.mutations==0
