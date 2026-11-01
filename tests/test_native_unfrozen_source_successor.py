"""Original explicit-task identity survives one local, pre-freeze source fix.

Owned Git, SQLite and real worker/origin consumers; no provider/paid edges.
"""
from dataclasses import replace
import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest

from owned_native_parent_profile import owned_profile
from shared_platform.operations_runtime import runtime_manifest
from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.release_store import ReleaseStore
from shared_platform.native_task_preparation import ExplicitNewTaskWorker, NativeR1FilesystemBoundary


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def _owned(tmp_path, *, offer='4000001234', shops=None):
    tmp_path.mkdir(parents=True,exist_ok=True)
    root = tmp_path / 'source'
    root.mkdir()
    old = owned_profile(root)
    release = lambda p: dict(code_version=p.version, environment=p.environment, manifest_digest=p.manifest_digest)
    engine = WorkbenchEngine(old.data_root/'tasks.db', release(old))
    task, created = engine.create_for_explicit_post({'template':'publication','source_key':'original-user-request',
        'scope':{'offer_id':offer,'shops':shops or ['tiktok:LH_MY']}})
    assert created
    history = engine.store.events(task['task_id'])
    with engine.readonly_snapshot() as db:
        checkpoint = engine._row(db, task['task_id'])['checkpoint_json']
    (root/'shared_platform/source-fix.py').write_text('# owned pricing source fix\n')
    def git(*args):
        return subprocess.check_output(['git','-C',str(root),'-c','user.name=Owned source fixture',
            '-c','user.email=fixture@example.invalid',*args],text=True).strip()
    git('add','--','shared_platform/source-fix.py')
    git('-c','commit.gpgsign=false','commit','-qm','Owned actual source fix')
    new = replace(old, version=git('rev-parse','HEAD'), manifest_digest=runtime_manifest(root))
    current = WorkbenchEngine(engine.store.path, release(new))
    store = ReleaseStore(tmp_path/'release.db')
    with store._connect() as db: store._ensure_schema(db)
    history_root = tmp_path/'history'
    history_root.mkdir()
    (root/'config').mkdir(exist_ok=True)
    (root/'config/product_publication_paid_history.local.json').write_text(json.dumps({
        'schema_version':'product-paid-history-roots/v1','service_root':str(root),'paid_history_root':str(history_root)}))
    expected = {'source_key':'original-user-request','request_digest':None,'from_release':release(old),
        'scope':task['scope'],'checkpoint_sha256':hashlib.sha256(checkpoint.encode()).hexdigest(),
        'history_sha256':None}
    with current.readonly_snapshot() as db:
        expected['request_digest'] = current._row(db,task['task_id'])['request_digest']
        events = [dict(r) for r in db.execute('SELECT id,event_type,detail_json,created_at FROM workbench_events WHERE task_id=? ORDER BY id',(task['task_id'],))]
    expected['history_sha256'] = _digest(events)
    return current,new,store,task,history,checkpoint,expected


def _register(values):
    from shared_platform.native_unfrozen_source_successor import register_source_successor
    engine,profile,store,task,*_,expected=values
    return register_source_successor(engine, task['task_id'], profile=profile, release_store=store, expected=expected)


def test_one_registered_source_fix_enters_real_worker_without_rewriting_original_post(tmp_path):
    values=_owned(tmp_path)
    engine,profile,store,task,history,checkpoint,_=values
    assert engine.explicit_new_post_task_ids()==()
    record=_register(values)
    assert record['execution_authority'] is False
    assert engine.explicit_new_post_task_ids()==(task['task_id'],)
    worker=ExplicitNewTaskWorker(engine,profile,NativeR1FilesystemBoundary(profile))
    try:
        assert worker.owns(task['task_id'])
        with engine.readonly_snapshot() as db:
            row=engine._row(db,task['task_id'])
            assert row['checkpoint_json']==checkpoint
            assert json.loads(row['version_json'])==engine.release
        events=engine.store.events(task['task_id'])
        assert events[1:]==history  # Store projection returns newest event first.
        assert len([e for e in events if e['event_type']=='explicit_new_post_preparation'])==1
        assert len(events)==len(history)+1
        # Later ordinary facts/domain events do not retroactively demand zero.
        engine.register_executor('actual-next-worker',['publication'],engine.release)
        token=engine.claim(task['task_id'],'actual-next-worker')['lease_token']
        engine.record_checkpoint(task['task_id'],token,{'native_preparation':{'prepared_reference':'next-real-stage'}})
        assert engine.explicit_new_post_task_ids()==(task['task_id'],)
    finally:worker.close()


def test_actual_v4_origin_consumer_accepts_lineage_then_requires_real_frozen_inputs(tmp_path,monkeypatch):
    values=_owned(tmp_path)
    _register(values)
    engine,profile,store,task,*_=values
    worker=ExplicitNewTaskWorker(engine,profile,NativeR1FilesystemBoundary(profile))
    engine.register_executor('next-worker',['publication'],engine.release)
    token=engine.claim(task['task_id'],'next-worker')['lease_token']
    # Only an owned owner-reference at this seam; never claim a frozen graph.
    reference='owned-admission-boundary-not-a-frozen-snapshot'
    engine.record_checkpoint(task['task_id'],token,{'native_preparation':{'prepared_reference':reference}})
    from shared_platform.native_sole_final_service import NativeSoleFinalService
    from shared_platform.native_windows_actor import NativeActorServiceConfig
    from shared_platform.native_new_task_v4_targets import preparation_scope
    from shared_platform import workbench_publication_native as native
    operations=SimpleNamespace(engine=engine,profile=profile,new_task_worker=worker)
    runtime=NativeSoleFinalService(store,NativeActorServiceConfig(tmp_path/'native-owner','Owned fixture'),operations=operations)
    monkeypatch.setattr(store,'get_plan',lambda _: {'payload':{'r3_stage_binding':{'native_preparation_source':{
        'offer_id':task['scope']['offer_id'],'prepared_reference':reference}}}})
    calls=[]
    def require_real_frozen(*args):
        calls.append(True)
        raise ValueError('OWNED_FROZEN_BOUNDARY_STILL_REQUIRED')
    monkeypatch.setattr(native,'read_frozen',require_real_frozen)
    plan={'product_id':task['scope']['offer_id'],'payload':{'r3_marketplace_binding':{'common_plan_id':'owned-common'}}}
    try:
        with pytest.raises(ValueError,match='OWNED_FROZEN_BOUNDARY_STILL_REQUIRED'):
            preparation_scope(runtime,plan)
        assert calls==[True]  # Both real origin consumers accepted the same chain.
        with engine.transaction() as db:
            db.execute('INSERT INTO workbench_events(task_id,event_type,detail_json,created_at) '
                'SELECT task_id,event_type,detail_json,created_at FROM workbench_events WHERE task_id=? AND event_type=?',
                (task['task_id'],'native_unfrozen_source_successor'))
        from shared_platform.final_review_server_admission import ApprovalBlocked
        with pytest.raises(ApprovalBlocked,match='MULTIPLE_CHAINS_REJECTED'):
            preparation_scope(runtime,plan)
        assert calls==[True]
    finally:worker.close()


@pytest.mark.parametrize('change',['checkpoint','scope','history','paid','category','lease','frozen'])
def test_registration_rejects_any_changed_prefreeze_proof(tmp_path,change):
    values=_owned(tmp_path)
    engine,profile,store,task,*_,expected=values
    if change in {'checkpoint','scope','history','lease'}:
        with engine.transaction() as db:
            if change=='checkpoint':db.execute('UPDATE workbench_execution SET checkpoint_json=? WHERE task_id=?',('{"native_r1":{}}',task['task_id']))
            if change=='scope':db.execute('UPDATE workbench_execution SET scope_json=? WHERE task_id=?',('{"offer_id":"different","shops":[],"skus":[]}',task['task_id']))
            if change=='history':engine._event(db,task['task_id'],'unexpected_observation',{})
            if change=='lease':db.execute('UPDATE workbench_execution SET worker=? WHERE task_id=?',('other',task['task_id']))
    elif change=='category':
        with engine.transaction() as db:
            db.execute('CREATE TABLE workbench_category_intents(task_id TEXT)')
            db.execute('INSERT INTO workbench_category_intents VALUES(?)',(task['task_id'],))
    else:
        directory=profile.root/'reports/product-preparation'/task['scope']['offer_id']
        directory.mkdir(parents=True)
        if change=='frozen':(directory/'round1-approved-snapshot.json').write_text('{}')
        else:
            (directory/'paid-requests').mkdir()
            (directory/'paid-requests/events.jsonl').write_text('{"state":"UNKNOWN"}\n')
    with pytest.raises(ValueError,match='SOURCE_SUCCESSOR_'):_register(values)
    with engine.readonly_snapshot() as db:
        assert json.loads(engine._row(db,task['task_id'])['version_json'])==expected['from_release']


def test_second_registration_and_tampered_lineage_are_rejected(tmp_path):
    values=_owned(tmp_path)
    _register(values)
    engine,profile,store,task,*_=values
    with pytest.raises(ValueError,match='SOURCE_SUCCESSOR_'):_register(values)
    with engine.transaction() as db:
        row=db.execute("SELECT id,detail_json FROM workbench_events WHERE task_id=? AND event_type='native_unfrozen_source_successor'",(task['task_id'],)).fetchone()
        bad=json.loads(row['detail_json']);bad['record']['scope']['shops']=['ozon:RU']
        db.execute('UPDATE workbench_events SET detail_json=? WHERE id=?',(json.dumps(bad),row['id']))
    with pytest.raises(ValueError,match='SOURCE_SUCCESSOR_'):engine.explicit_new_post_task_ids()
