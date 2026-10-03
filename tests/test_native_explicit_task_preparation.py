"""Owned Windows paths and genuine task/R1 writers; no provider or paid agent."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import threading

import pytest

from shared_platform.native_task_preparation import (
    ExplicitNewTaskWorker, NativeR1FilesystemBoundary, R1FilesystemUnavailable)
from shared_platform.operations_runtime import RuntimeProfile
from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.workbench_http import dispatch
from shared_platform import operations_runtime, publication_rounds, release_control
from shared_platform import workbench_publication_native as native
from test_round1_workspace_freeze import live, prepared
from test_round1_auto_freeze import complete_source, public_settings


def _engine(root):
    profile=RuntimeProfile(root,root/'data/operations/stable','preview','owned-new-post')
    engine=WorkbenchEngine(profile.data_root/'tasks.db',{
        'code_version':profile.version,'environment':profile.environment,'manifest_digest':''})
    return engine,profile


def _payload(key='explicit-new-task',offer='123'):
    return {'template':'publication','source_key':key,
             'scope':{'offer_id':offer,'shops':['tiktok:LH_MY']}}


def _prepared_parent_source(live):
    complete_source(live)
    # Capture against the exact order consumed by native._scope, before the
    # immutable receipt is produced. This never sorts a receipt while reading.
    actual_dashboard = release_control.build_release_dashboard
    def dashboard(*args, **kwargs):
        result = actual_dashboard(*args, **kwargs)
        original_labels = result['publication_scope']['selected_labels']
        result['publication_scope']['selected_labels'] = sorted(
            original_labels)
        assert len(result['publication_scope']['selected_labels']) == len(original_labels)
        assert set(result['publication_scope']['selected_labels']) == set(original_labels)
        return result
    with pytest.MonkeyPatch.context() as capture:
        capture.setattr(release_control, 'build_release_dashboard', dashboard)
        packet, request = prepared(live)
    # Retain the actual producer's MY read context before the native producer
    # rebuilds this input. The old three-field image proposal has no region;
    # selecting a different region would correctly reject the MY receipt.
    path = publication_rounds.report_dir(live['offer'])/'first-review.json'
    path.write_text(json.dumps(packet['packet'],ensure_ascii=False),encoding='utf-8')
    assert json.loads(path.read_text(encoding='utf-8')) == packet['packet']
    assert packet['packet']['category_review_context']['source_region'] == 'MY'
    return packet, request


def test_creation_fact_is_transaction_owned_and_idempotent_requests_do_not_adopt_history(tmp_path):
    engine,profile=_engine(tmp_path)
    original=engine.create(_payload('historical'))
    worker=ExplicitNewTaskWorker(engine,profile,NativeR1FilesystemBoundary(profile))
    try:
        code,body=dispatch(engine,'POST','/api/orbit/tasks',
            {**_payload('historical'),'created_this_post':True},create_handler=worker.create)
        assert code==201 and body['task']['task_id']==original['task_id']
        assert not worker.owns(original['task_id'])
        assert not any(e['event_type']=='explicit_new_post_preparation'
                       for e in engine.store.events(original['task_id']))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:dispatch(engine,'POST','/api/orbit/tasks',
                _payload(),create_handler=worker.create),range(2)))
        ids={body['task']['task_id'] for code,body in results if code==201}
        assert len(ids)==1 and worker.owns(next(iter(ids)))
        events=engine.store.events(next(iter(ids)))
        records=[e for e in events if e['event_type']=='explicit_new_post_preparation']
        assert len(records)==1 and records[0]['detail']['scope']==_payload()['scope']|{'skus':[]}
        assert 'created_this_post' not in json.dumps(results)
    finally:worker.close()


def test_worker_reads_only_new_post_ids_and_never_scans_dashboard(tmp_path,monkeypatch):
    engine,profile=_engine(tmp_path)
    old=engine.create(_payload('old'))
    worker=ExplicitNewTaskWorker(engine,profile,NativeR1FilesystemBoundary(profile))
    calls=[];done=threading.Event()
    def exact_adapter(engine,task,token,profile):
        calls.append(task['task_id'])
        engine.observe_reconciliation(task['task_id'],token,'Owned preparation observation only')
        done.set()
    worker.adapters['publication']=exact_adapter
    monkeypatch.setattr(engine,'dashboard',lambda:pytest.fail('No historical/dashboard scan'))
    try:
        worker.start()
        new=worker.create(_payload('new'))
        assert done.wait(5)
        worker.create(_payload('new'))
        assert calls==[new['task_id']]
        assert engine.get(old['task_id'])['execution_state']=='queued'
        assert engine.get(new['task_id'])['execution_state']=='reconciliation_required'
        assert len([e for e in engine.store.events(new['task_id']) if e['event_type']=='claimed'])==1
        assert worker.status()['historical_task_scan'] is False
    finally:worker.close()


def test_nonpublication_request_is_saved_but_not_given_a_new_business_executor(tmp_path):
    engine,profile=_engine(tmp_path)
    worker=ExplicitNewTaskWorker(engine,profile,NativeR1FilesystemBoundary(profile))
    grant_types={'explicit_new_post_preparation','explicit_new_post_profit_readonly',
                 'explicit_new_post_delisting'}
    payload={'template':'delisting','source_key':'no-new-business-grant',
             'scope':{'skus':['1234'],'shops':['tiktok:LH_MY']}}
    try:
        # Ordinary registration and a later idempotent POST do not adopt it.
        old=engine.create(payload)
        old_events=engine.store.events(old['task_id'])
        assert not worker.owns(old['task_id'])
        reused=worker.create(payload)
        assert reused['task_id']==old['task_id']
        assert not worker.owns(reused['task_id'])
        assert reused['execution_state']=='executor_offline'
        assert engine.store.events(old['task_id'])==old_events
        assert not any(e['event_type'] in grant_types for e in old_events)
        assert engine.explicit_new_post_task_ids()==tuple()
        assert engine.explicit_new_post_profit_task_ids()==tuple()
        assert engine.explicit_new_post_delisting_task_ids()==tuple()

        # A genuinely new exact delisting POST has its separate admission.
        new_payload={**payload,'source_key':'explicit-new-delisting-only'}
        new=worker.create(new_payload)
        assert new['task_id']!=old['task_id'] and worker.owns(new['task_id'])
        assert new['scope']==payload['scope'] and new['version']==engine.release
        events=engine.store.events(new['task_id'])
        grants=[e for e in events if e['event_type'] in grant_types]
        assert len(grants)==1 and grants[0]['event_type']=='explicit_new_post_delisting'
        assert set(grants[0]['detail'])=={'request_digest','source_key','scope','release'}
        assert grants[0]['detail']['scope']==new['scope']
        assert grants[0]['detail']['source_key']==new_payload['source_key']
        assert grants[0]['detail']['release']==engine.release
        with engine.transaction() as connection:
            row=engine._row(connection,new['task_id'])
            assert grants[0]['detail']['request_digest']==row['request_digest']
        assert engine.explicit_new_post_task_ids()==tuple()
        assert engine.explicit_new_post_profit_task_ids()==tuple()
        assert engine.explicit_new_post_delisting_task_ids()==(new['task_id'],)
        assert worker.create(new_payload)['task_id']==new['task_id']
        assert engine.store.events(new['task_id'])==events
        assert not worker.owns(old['task_id'])

        # Admission does not assert configured capability or execute a task.
        status=worker.status()
        assert status['scope']=='EXPLICIT_NEW_POST_PUBLICATION_ONLY'
        assert status['delisting_scope']=='EXPLICIT_NEW_POST_DELIST_EXACT_SCOPE'
        assert status['profit_scope']=='EXPLICIT_NEW_POST_PROFIT_READONLY'
        assert status['delisting_executor_connected'] is False
        assert status['delisting_readiness']=='NATIVE_DELIST_SERVICE_BINDING_REQUIRED'
        assert status['profit_executor_connected'] is False
        assert status['running'] is False and status['state']=='stopped'
        assert worker.thread.ident is None and not worker.thread.is_alive()
        assert worker.inflight=={} and status['inflight_count']==0
        for task_id in (old['task_id'],new['task_id']):
            current=engine.get(task_id)
            assert current['execution_state']=='executor_offline'
            assert current['worker'] is None and current['last_claimed_at'] is None
            assert not current['executor_connected'] and current['checkpoint']=={}
            with engine.transaction() as connection:
                row=engine._row(connection,task_id)
                assert row['external_started']==0 and row['lease_token'] is None
            assert not any(e['event_type'] in {'claimed','external_action_started'}
                           for e in engine.store.events(task_id))
    finally:worker.close()


def test_windows_directory_handles_block_parent_rename_until_parent_writer_releases(tmp_path):
    engine,profile=_engine(tmp_path)
    boundary=NativeR1FilesystemBoundary(profile)
    parent=tmp_path/'owned-parent';leaf=parent/'reports';leaf.mkdir(parents=True)
    moved=tmp_path/'moved-parent'
    with boundary._pin([leaf]):
        with pytest.raises(OSError):os.rename(parent,moved)
        (leaf/'parent-only.json').write_text('{"parent_written":true}',encoding='utf-8')
        assert json.loads((leaf/'parent-only.json').read_text())=={'parent_written':True}
    os.rename(parent,moved)
    assert (moved/'reports/parent-only.json').is_file()


def test_actual_junction_ancestor_is_rejected_before_any_parent_output(tmp_path):
    import subprocess
    engine,profile=_engine(tmp_path)
    destination=tmp_path/'destination';destination.mkdir()
    link=tmp_path/'junction'
    result=subprocess.run(['cmd.exe','/d','/c','mklink','/J',str(link),str(destination)],
                          capture_output=True,timeout=10)
    assert result.returncode==0,(result.stdout,result.stderr)
    try:
        with pytest.raises(R1FilesystemUnavailable,match='REPARSE_OR_LINK'):
            with NativeR1FilesystemBoundary(profile)._pin([link/'reports']):
                pytest.fail('Reparse ancestor cannot reach the parent writer')
        assert not (destination/'reports').exists()
    finally:os.rmdir(link)


@pytest.mark.parametrize('damage',['lease','release'])
def test_stale_task_identity_cannot_create_a_parent_output_directory(tmp_path,damage):
    engine,profile=_engine(tmp_path)
    engine.register_executor('owned-worker',['publication'],engine.release)
    task=engine.create(_payload())
    token=engine.claim(task['task_id'],'owned-worker')['lease_token']
    if damage=='lease':token='not-the-owned-lease'
    else:task={**task,'version':{**task['version'],'code_version':'another-source'}}
    with pytest.raises(ValueError):
        with NativeR1FilesystemBoundary(profile).hold(engine,task,token,profile):
            pytest.fail('Stale task cannot enter parent filesystem boundary')
    assert not (profile.data_root/'artifacts'/task['task_id']).exists()


def test_real_native_parent_prepares_freezes_and_rereads_owner_without_global_marker(live,monkeypatch):
    packet,_=_prepared_parent_source(live)  # Actual closed category capture and immutable producer.
    engine,profile=_engine(live['root'])
    task,created=engine.create_for_explicit_post({'template':'publication','source_key':'actual-parent-r1',
        'scope':{'offer_id':live['offer'],'shops':packet['packet']['target_selection']['requested']}})
    assert created is True
    engine.register_executor('owned-parent',['publication'],engine.release)
    token=engine.claim(task['task_id'],'owned-parent')['lease_token']
    assert operations_runtime._R1_FILESYSTEM_BOUNDARY_VERIFIED is False
    boundary=NativeR1FilesystemBoundary(profile)
    assert boundary.verified_for(engine,task,token,profile) is False
    assert boundary.run(engine,engine.get(task['task_id']),token,profile) is True
    current=engine.get(task['task_id'])
    assert current['current_step']=='images' and current['steps'][0]['state']=='completed',current
    frozen=native.read_frozen(current,profile)
    assert frozen==current['steps'][0]['checkpoint']['native_r1']
    stored=json.loads((publication_rounds.report_dir(live['offer'])/'round1-approved-snapshot.json').read_text(encoding='utf-8'))
    assert stored['snapshot_digest']==frozen['snapshot_digest'] and stored['human_approval'] is False
    assert not current['required_action']
    with engine.transaction() as db:
        assert db.execute('SELECT external_started FROM workbench_execution WHERE task_id=?',
                          (current['task_id'],)).fetchone()[0]==0
    assert boundary.verified_for(engine,current,token,profile) is False
    assert operations_runtime._R1_FILESYSTEM_BOUNDARY_VERIFIED is False


def test_unknown_started_facts_attempt_is_not_relaunched_inside_real_parent_pin(live,monkeypatch):
    engine,profile=_engine(live['root'])
    task=engine.create(_payload('unknown-owned-session',live['offer']))
    engine.register_executor('owned-parent',['publication'],engine.release)
    token=engine.claim(task['task_id'],'owned-parent')['lease_token']
    engine.record_checkpoint(task['task_id'],token,{'facts_attempt':{'state':'started','number':1}})
    from shared_platform import operations_publication_prepare as preparation
    monkeypatch.setattr(preparation,'_current',lambda *_:pytest.fail('Unknown facts session must not restart'))
    NativeR1FilesystemBoundary(profile).run(engine,engine.get(task['task_id']),token,profile)
    current=engine.get(task['task_id'])
    assert current['execution_state']=='reconciliation_required'
    assert current['checkpoint']['facts_attempt']=={'state':'started','number':1}
    assert not current['required_action']


def test_real_http_new_post_runs_parent_r1_and_duplicate_post_keeps_original_receipt(live,monkeypatch):
    from http.client import HTTPConnection
    import time
    packet,_=_prepared_parent_source(live)
    engine,profile=_engine(live['root'])
    runtime=live['operations_runtime']
    runtime.engine=engine;runtime.profile=profile
    worker=ExplicitNewTaskWorker(engine,profile,NativeR1FilesystemBoundary(profile))
    runtime.new_task_worker=worker
    payload={'template':'publication','source_key':'actual-http-new-parent',
             'scope':{'offer_id':live['offer'],'shops':packet['packet']['target_selection']['requested']}}
    def post():
        connection=HTTPConnection('127.0.0.1',live['port'],timeout=10)
        try:
            connection.request('POST','/api/orbit/tasks',json.dumps(payload).encode(),
                               {'Content-Type':'application/json'})
            response=connection.getresponse()
            assert response.status==201
            return json.loads(response.read())['task']
        finally:connection.close()
    try:
        worker.start();original=post()
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            current=engine.get(original['task_id'])
            if current['steps'][0]['state']=='completed':break
            time.sleep(.01)
        assert current['steps'][0]['state']=='completed',current
        snapshot=publication_rounds.report_dir(live['offer'])/'round1-approved-snapshot.json'
        before=snapshot.read_bytes()
        duplicate=post()
        assert duplicate['task_id']==original['task_id']
        assert snapshot.read_bytes()==before
        assert native.read_frozen(engine.get(original['task_id']),profile)
        events=engine.store.events(original['task_id'])
        assert len([e for e in events if e['event_type']=='explicit_new_post_preparation'])==1
        assert len([e for e in events if e['event_type']=='claimed'])==1
        assert not any(e['event_type']=='user_action_required' for e in events)
        assert operations_runtime._R1_FILESYSTEM_BOUNDARY_VERIFIED is False
    finally:
        worker.close();del runtime.new_task_worker


def test_restart_restores_only_exact_service_post_grants_and_original_version(tmp_path,monkeypatch):
    engine,profile=_engine(tmp_path)
    old=engine.create(_payload('old-before-new-post'))
    task,created=engine.create_for_explicit_post(_payload('durable-explicit-post'))
    assert created
    monkeypatch.setattr(engine,'dashboard',lambda:pytest.fail('Recovery cannot scan dashboard'))
    restarted=WorkbenchEngine(engine.store.path,engine.release)
    worker=ExplicitNewTaskWorker(restarted,profile,NativeR1FilesystemBoundary(profile))
    try:
        assert worker.owns(task['task_id']) and not worker.owns(old['task_id'])
        duplicate=worker.create(_payload('durable-explicit-post'))
        assert duplicate['task_id']==task['task_id']
        assert len([e for e in restarted.store.events(task['task_id'])
                    if e['event_type']=='explicit_new_post_preparation'])==1
    finally:worker.close()
    other=WorkbenchEngine(engine.store.path,{**engine.release,'code_version':'new-source'})
    assert other.explicit_new_post_task_ids()==()
    with engine.transaction() as db:
        row=db.execute("SELECT id,detail_json FROM workbench_events WHERE task_id=? "
                       "AND event_type='explicit_new_post_preparation'",(task['task_id'],)).fetchone()
        changed=json.loads(row['detail_json']);changed['source_key']='caller-substituted-key'
        db.execute('UPDATE workbench_events SET detail_json=? WHERE id=?',(json.dumps(changed),row['id']))
    with pytest.raises(ValueError,match='NATIVE_NEW_POST_RECOVERY_GRANT_CHANGED'):
        restarted.explicit_new_post_task_ids()


def test_event_recovery_creates_no_grant_table_and_completed_task_is_not_restored(tmp_path):
    engine,profile=_engine(tmp_path)
    engine.create(_payload('legacy-only'))
    assert engine.explicit_new_post_task_ids()==()
    with engine.transaction() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='workbench_new_post_preparation_grants'").fetchone()
    task,_=engine.create_for_explicit_post(_payload('terminal-new-post'))
    with engine.transaction() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='workbench_new_post_preparation_grants'").fetchone()
        db.execute("UPDATE workbench_execution SET state='completed' WHERE task_id=?",(task['task_id'],))
    assert engine.explicit_new_post_task_ids()==()


def test_real_partial_snapshot_recovers_original_durable_post_without_global_flag_or_second_cas(live,monkeypatch):
    from shared_platform import immutable_approval_files as files
    from shared_platform import operations_publication_prepare as preparation
    from modules.sourcing import new_product_workbench as workbench
    packet,_=_prepared_parent_source(live)
    engine,profile=_engine(live['root'])
    task,_=engine.create_for_explicit_post({'template':'publication','source_key':'recover-real-parent',
        'scope':{'offer_id':live['offer'],'shops':packet['packet']['target_selection']['requested']}})
    engine.register_executor('owned-parent',['publication'],engine.release)
    token=engine.claim(task['task_id'],'owned-parent')['lease_token']
    original=files.persist_immutable_bytes
    def interrupted(path,content,*,root):
        if path.name=='round1-approved-snapshot.json':raise OSError('Owned interrupted snapshot')
        return original(path,content,root=root)
    monkeypatch.setattr(files,'persist_immutable_bytes',interrupted)
    boundary=NativeR1FilesystemBoundary(profile)
    assert boundary.run(engine,engine.get(task['task_id']),token,profile)
    stopped=engine.get(task['task_id'])
    assert stopped['execution_state']=='reconciliation_required' and stopped['checkpoint']['r1_auto_intent']
    assert not (publication_rounds.report_dir(live['offer'])/'round1-approved-snapshot.json').exists()
    monkeypatch.setattr(files,'persist_immutable_bytes',original)
    monkeypatch.setattr(workbench,'save_state',lambda *_args,**_kwargs:pytest.fail('No second technical Product Center CAS'))
    monkeypatch.setattr(preparation,'_current',lambda *_:pytest.fail('No new facts or agent request during recovery'))
    restarted=WorkbenchEngine(engine.store.path,engine.release)
    recovered=NativeR1FilesystemBoundary(profile)
    assert recovered.recover(restarted,restarted.get(task['task_id']),profile) is True
    current=restarted.get(task['task_id'])
    assert current['execution_state']=='queued' and native.read_frozen(current,profile)
    assert len([e for e in restarted.store.events(task['task_id']) if e['event_type']=='r1_technical_recovered'])==1
    assert not current['required_action'] and operations_runtime._R1_FILESYSTEM_BOUNDARY_VERIFIED is False
    assert recovered.verified_for(restarted,current,None,profile) is False


def test_restarted_worker_keeps_started_unknown_without_relaunch(live,monkeypatch):
    from shared_platform import operations_publication_prepare as preparation
    engine,profile=_engine(live['root'])
    task,_=engine.create_for_explicit_post(_payload('durable-started-unknown',live['offer']))
    engine.register_executor('original-worker',['publication'],engine.release)
    token=engine.claim(task['task_id'],'original-worker')['lease_token']
    engine.record_checkpoint(task['task_id'],token,{'facts_attempt':{'state':'started','number':1}})
    engine.observe_reconciliation(task['task_id'],token,'Original facts session unknown')
    restarted=WorkbenchEngine(engine.store.path,engine.release)
    worker=ExplicitNewTaskWorker(restarted,profile,NativeR1FilesystemBoundary(profile))
    original_get=restarted.get;seen=threading.Event()
    def observe_exact(task_id):
        value=original_get(task_id);seen.set();return value
    monkeypatch.setattr(restarted,'get',observe_exact)
    monkeypatch.setattr(restarted,'claim',lambda *_args,**_kwargs:pytest.fail('Unknown session must not claim'))
    monkeypatch.setattr(preparation,'_current',lambda *_:pytest.fail('Unknown session must not prepare'))
    try:
        worker.start();assert seen.wait(5)
        assert worker.owns(task['task_id']) and task['task_id'] not in worker.r1_recovery_seen
    finally:worker.close()
    current=original_get(task['task_id'])
    assert current['execution_state']=='reconciliation_required'
    assert current['checkpoint']['facts_attempt']=={'state':'started','number':1}
    assert len([e for e in restarted.store.events(task['task_id']) if e['event_type']=='claimed'])==1


def test_service_path_bindings_allow_configured_other_drive_without_writing_it(tmp_path):
    from pathlib import PureWindowsPath
    profile=RuntimeProfile(tmp_path,PureWindowsPath('D:/OrbitHive/runtime/operations-owned'),
                           'preview','owned-path-semantics')
    boundary=NativeR1FilesystemBoundary(profile)
    assert boundary._configured_local_directory('D:/OrbitHive/runtime/operations-owned/artifacts/TASK-20261003-1')
    assert boundary._configured_local_directory('D:/OrbitHive/business/release',
                                                release_parent='D:/OrbitHive/business/release')
    for bad in ('D:/outside','D:/OrbitHive/runtime/operations-owned/../other','//remote/share/root','relative/path'):
        with pytest.raises(R1FilesystemUnavailable,match='CONFIGURED_LOCAL_DIRECTORY'):
            boundary._configured_local_directory(bad)


@pytest.mark.parametrize('leaf',['first-review.json','first-review.json.tmp'])
def test_real_parent_rejects_preexisting_hardlinked_output_without_touching_outside(live,leaf):
    engine,profile=_engine(live['root'])
    task,_=engine.create_for_explicit_post(_payload('hardlinked-output-'+leaf,live['offer']))
    engine.register_executor('owned-parent',['publication'],engine.release)
    token=engine.claim(task['task_id'],'owned-parent')['lease_token']
    outside=live['root']/'outside-original.txt';outside.write_bytes(b'original outside bytes')
    directory=publication_rounds.report_dir(live['offer']);directory.mkdir(parents=True,exist_ok=True)
    target=directory/leaf
    assert target.is_relative_to(live['root'])
    target.unlink(missing_ok=True)  # Replace only this owned fixture leaf with a real link.
    os.link(outside,target)
    with pytest.raises(R1FilesystemUnavailable,match='FILE_REPARSE_OR_MULTILINK'):
        with NativeR1FilesystemBoundary(profile).hold(engine,engine.get(task['task_id']),token,profile):
            pytest.fail('A linked output cannot reach the real parent producer')
    assert outside.read_bytes()==b'original outside bytes' and target.stat().st_nlink==2


def test_atomic_parent_writers_never_overwrite_or_delete_a_preexisting_temporary_link(live):
    from modules.sourcing import new_product_workbench as workbench
    from shared_platform import round1_workspace
    builder=round1_workspace._module(native._server(_engine(live['root'])[1]))
    outside=live['root']/'outside-temp.txt';outside.write_bytes(b'untouched')
    target=live['root']/'parent-output.json'
    temporary=target.with_suffix('.json.tmp');os.link(outside,temporary)
    with pytest.raises(FileExistsError):builder._write_text_atomic(target,'replacement')
    assert temporary.is_file() and outside.read_bytes()==b'untouched' and not target.exists()
    temporary.unlink();builder._write_text_atomic(target,'real original writer')
    assert target.read_text(encoding='utf-8')=='real original writer'
    state=live['root']/'parent-state.json'
    state_temp=state.with_name(f'.{state.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    os.link(outside,state_temp)
    with pytest.raises(FileExistsError):workbench._write_json_atomic(state,{'state':'replacement'})
    assert state_temp.is_file() and outside.read_bytes()==b'untouched' and not state.exists()
    state_temp.unlink();workbench._write_json_atomic(state,{'state':'original writer'})
    assert json.loads(state.read_text(encoding='utf-8'))=={'state':'original writer'}
