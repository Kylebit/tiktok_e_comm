"""Real native graph/actor/HTTP and original durable executor, owned fixtures.

The marketplace registry doubles below prove execution mechanics only. They
are not official provider grants or successful external publication evidence.
All COMMON preparation/signing/READ/claim facts use the original closed chain.
"""
from dataclasses import replace
from http.server import ThreadingHTTPServer
import json
import threading
import http.client as owned_http_client
from types import SimpleNamespace

import pytest

from modules.products import server,release_adapters
from shared_platform import native_sole_final_service as channel
from shared_platform import native_sole_final_execution as execution
from shared_platform import native_sole_final_decision as final
from shared_platform.native_windows_actor import NativeActorServiceConfig
from shared_platform.final_review_server_admission import ApprovalBlocked
from shared_platform import release_store
from domains.channel_operations.release_executor import AdapterExecutionResult
from test_round1_workspace_freeze import live
from test_native_sole_final_decision import _completed
from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.operations_runtime import RuntimeProfile,OperationsWorker


def _installed(live,monkeypatch,tmp_path,*,enabled=True):
    old,market,common,run,config,calls=_completed(live,monkeypatch,tmp_path)
    original_operations=server._NATIVE_FINAL_SERVICE._operations
    engine,profile=original_operations.engine,original_operations.profile
    assert profile.root.resolve()==live['root'].resolve()
    assert engine.release=={'code_version':profile.version,'environment':profile.environment,
                           'manifest_digest':profile.manifest_digest}
    worker=OperationsWorker(engine,profile,{})
    operations=SimpleNamespace(profile=profile,engine=engine,worker=worker,worker_enabled=False)
    installed=channel.NativeSoleFinalService(old.store,
        NativeActorServiceConfig(tmp_path/'native-final-service','Kyle'),
        new_decision_execution_enabled=enabled,operations=operations).start()
    original_close=installed.close
    def close_owned():
        try:
            return original_close()
        finally:
            worker.close()
    monkeypatch.setattr(installed,'close',close_owned)
    monkeypatch.setattr(server,'_NATIVE_FINAL_SERVICE',installed)
    assert installed._operations.engine is original_operations.engine
    assert installed._operations.profile is original_operations.profile
    return installed,market,common,run,calls


def _registry(monkeypatch,*,unknown=False,verified=False,started=None,proceed=None):
    original=release_adapters.production_adapter_registry()
    calls=[]
    def dispatch(request):
        calls.append(request)
        if started is not None and len(calls)==1:
            started.set()
            assert proceed.wait(timeout=20),'owned transport was not released'
        if unknown:
            raise RuntimeError('owned connection dropped after target claim')
        if verified:
            return AdapterExecutionResult(True,True,'owned readback contract',
                external_reference='owned:'+request.target_label,
                readback_evidence={'source':'OWNED_UNIT_READBACK','target_label':request.target_label,
                                   'status':'SUCCEEDED','verified':True})
        # Original per-target submission storage validates and preserves the
        # attempted outcome; it must never be labelled official success here.
        return AdapterExecutionResult(True,False,'owned asynchronous acceptance',
            external_reference='owned:'+request.target_label,
            readback_evidence={'source':'OWNED_UNIT_TRANSPORT','status':'PROCESSING','accepted':True},
            submission_accepted=True)
    registry={name:replace(item,execute=dispatch,blocker=None,
        automatic_first_attempt_mode='ENABLED') for name,item in original.items()}
    monkeypatch.setattr(release_adapters,'production_adapter_registry',lambda:registry)
    monkeypatch.setattr(server,'_r3_marketplace_business_execution_gate',lambda:(409,{
        'ok':False,'error':'MARKETPLACE_BUSINESS_EXECUTION_DISABLED','external_writes_performed':[]}))
    return calls


def _http(installed):
    http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
    http.native_final_review=installed
    http.operations_runtime=installed._operations
    thread=threading.Thread(target=http.serve_forever,name='owned-native-final-http')
    thread.start()
    return http,thread


def _post(http,path,body,*,origin=True):
    # This is the actual owned Handler listener, not the closed provider seam.
    assert type(http) is ThreadingHTTPServer and http.RequestHandlerClass is server.Handler
    host,port=http.server_address
    assert host=='127.0.0.1' and type(port) is int and 0<port<65536
    assert http.socket.getsockname()==http.server_address
    assert type(path) is str and path.startswith(channel.PREFIX)
    address='http://127.0.0.1:'+str(port)
    headers={'Content-Type':'application/json'}
    if origin:headers['Origin']=address
    connection=owned_http_client.HTTPConnection(host,port,timeout=40)
    try:
        connection.request('POST',path,body=json.dumps(body).encode(),headers=headers)
        response=connection.getresponse()
        return response.status,json.loads(response.read())
    finally:connection.close()


def test_real_handler_nonce_decision_automatically_claims_original_executor_and_never_repeats(live,monkeypatch,tmp_path):
    installed,market,common,run,common_calls=_installed(live,monkeypatch,tmp_path)
    calls=_registry(monkeypatch)
    http,thread=_http(installed)
    try:
        code,prepared=_post(http,channel.PREFIX+'prepare',{'plan_id':market['plan_id']})
        assert code==200 and prepared['manifest']['targets']
        code,outcome=_post(http,channel.PREFIX+'decision',{'nonce':prepared['nonce'],'review_digest':prepared['review_digest']})
        assert code==202 and outcome['approval_saved'] is True,outcome
        installed._execution_thread.join(timeout=40)
        assert not installed._execution_thread.is_alive(),installed._execution_result
        assert installed._execution_result[0]==200,installed._execution_result
        assert {r.target_label for r in calls}==set(market['targets'])
        assert all(r.plan_id==market['plan_id'] for r in calls)
        assert len(calls)==len(market['targets'])
        count=len(calls)
        code,repeated=_post(http,channel.PREFIX+'resume',{'decision_id':outcome['decision']['decision_id']})
        assert len(calls)==count and code==200,repeated
        actual=installed.store.get_run('release-run:'+market['payload_digest'][:24])
        assert all(r['attempts']==1 and r['status']=='SUBMITTED_UNVERIFIED' for r in actual['targets'])
        assert all(not r.get('readback') for r in actual['targets'])
        with installed._operations.engine.transaction() as db:
            task=db.execute('SELECT * FROM workbench_execution WHERE template=?',('publication',)).fetchone()
            assert task['state']=='waiting_domain'
            assert json.loads(task['action_json'])['kind']=='observe'
            progress=json.loads(task['checkpoint_json'])['native_final_decision']
            assert progress['decision_id']==outcome['decision']['decision_id']
            assert {row['target_label'] for row in progress['target_results']}==set(market['targets'])
            assert progress['official_readback_complete'] is False
        assert installed.store.get_plan(common['plan_id'])['status']==release_store.PLAN_PENDING_APPROVAL
        assert common_calls.count('/open/v1/product/common_collect_box/common_collect_box/edit_common_collect_box_detail')==1
    finally:
        http.shutdown();http.server_close();thread.join(timeout=5);installed.close()
        assert not thread.is_alive()


def test_actual_native_get_and_operations_receipt_share_durable_targets_without_ipc_or_provider(live,monkeypatch,tmp_path):
    installed,market,*_=_installed(live,monkeypatch,tmp_path)
    try:
        before=installed.store.path.read_bytes()
        monkeypatch.setattr(installed.supervisor,'endpoint',lambda **_:pytest.fail('GET must not touch helper'))
        monkeypatch.setattr(installed.supervisor,'request_carrier',lambda **_:pytest.fail('GET must not issue identity'))
        code,view=server._publication_stages_for_request({'offer_id':market['product_id'],'plan_id':market['plan_id']})
        assert code==200 and view['marketplace']['native_final_review']['review']['targets']==tuple(market['targets'])
        assert view['marketplace']['preview']['review_manifest']['targets']
        assert installed.store.path.read_bytes()==before
        assert view['marketplace']['native_final_review']['execution_authority'] is False
    finally:installed.close()


def test_approval_returns_before_transport_and_home_task_leaves_human_wait_without_a_second_dispatch(live,monkeypatch,tmp_path):
    installed,market,*_=_installed(live,monkeypatch,tmp_path)
    started,proceed=threading.Event(),threading.Event()
    calls=_registry(monkeypatch,started=started,proceed=proceed)
    http,thread=_http(installed)
    try:
        code,prepared=_post(http,channel.PREFIX+'prepare',{'plan_id':market['plan_id']})
        assert code==200,prepared
        body={'nonce':prepared['nonce'],'review_digest':prepared['review_digest']}
        code,first=_post(http,channel.PREFIX+'decision',body)
        assert code==202 and first['approval_saved'] is True,first
        assert started.wait(timeout=15)
        assert installed._execution_thread.is_alive() and not proceed.is_set()
        with installed._operations.engine.transaction() as db:
            task=db.execute("SELECT * FROM workbench_execution WHERE template='publication'").fetchone()
            assert task['state']=='waiting_domain'
            assert json.loads(task['action_json'])['kind']=='observe'
            assert db.execute('SELECT status FROM workbench_tasks WHERE task_id=?',(task['task_id'],)).fetchone()[0]=='in_progress'
        code,repeated=_post(http,channel.PREFIX+'decision',body)
        assert code==202 and repeated['decision']['decision_id']==first['decision']['decision_id'],repeated
        assert len(calls)==1
        code,view=server._publication_stages_for_request({'offer_id':market['product_id']})
        assert code==200 and view['marketplace']['native_execution_in_progress'] is True
        assert view['marketplace']['final_review_available'] is False
    finally:
        proceed.set()
        if installed._execution_thread is not None:installed._execution_thread.join(timeout=40)
        http.shutdown();http.server_close();thread.join(timeout=5);installed.close()


def test_original_task_completes_only_after_every_owned_target_has_verified_readback(live,monkeypatch,tmp_path):
    installed,market,*_=_installed(live,monkeypatch,tmp_path)
    calls=_registry(monkeypatch,verified=True)
    try:
        prepared=installed.prepare(market['plan_id'])
        decision=installed.decide(nonce=prepared['nonce'],review_digest=prepared['review_digest'])
        code,queued=installed.submit(decision['decision_id'])
        assert code==202,queued
        installed._execution_thread.join(timeout=40)
        assert not installed._execution_thread.is_alive()
        assert installed._execution_result[0]==200,installed._execution_result
        assert installed._execution_result[1]['completed'] is True,installed._execution_result
        assert {call.target_label for call in calls}==set(market['targets'])
        with installed._operations.engine.transaction() as db:
            task=db.execute("SELECT * FROM workbench_execution WHERE template='publication'").fetchone()
            assert task['state']=='completed' and task['action_json'] is None
            assert all(step['state']=='completed' for step in json.loads(task['steps_json']))
            assert json.loads(task['checkpoint_json'])['native_final_decision']['official_readback_complete'] is True
        code,view=server._publication_stages_for_request({'offer_id':market['product_id']})
        assert code==200 and all(row['official_success'] is True for row in view['marketplace']['target_results'])
    finally:installed.close()


def test_real_common_domain_observation_releases_original_task_to_the_only_final_review(live,monkeypatch,tmp_path):
    from shared_platform import operations_publication_common as common
    from shared_platform import operations_publication
    installed,market,common_plan,run,calls=_installed(live,monkeypatch,tmp_path)
    try:
        engine,profile=installed._operations.engine,installed._operations.profile
        with engine.transaction() as db:
            task_id=db.execute("SELECT task_id FROM workbench_execution WHERE template='publication'").fetchone()[0]
        original=engine.get(task_id)
        assert original['execution_state']=='waiting_domain'
        writes=calls.count('/open/v1/product/common_collect_box/common_collect_box/edit_common_collect_box_detail')
        assert common.observe(engine,original,profile,server_module=server) is True
        queued=engine.get(task_id)
        assert queued['execution_state']=='queued'
        token=engine.claim(task_id,'native-source-worker',ttl=300)['lease_token']
        current=engine.get(task_id)
        run_task,_=operations_publication.bindings()
        run_task(engine,current,token,profile)
        waiting=engine.get(task_id)
        assert waiting['execution_state']=='waiting_user'
        assert waiting['required_action']['kind']=='review'
        assert waiting['required_action']['label']=='审核最终发布包'
        assert calls.count('/open/v1/product/common_collect_box/common_collect_box/edit_common_collect_box_detail')==writes==1
        assert installed.store.get_plan(common_plan['plan_id'])['status']==release_store.PLAN_PENDING_APPROVAL
        with installed.store._connect_readonly() as db:
            assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0]==0
    finally:installed.close()


@pytest.fixture
def preview_native_images(live,monkeypatch):
    # Compose only this negative's genuine current prepared-owner fixture.
    # The other historical service fixtures retain their original contracts.
    from test_native_r2_service_consumers import live as proposed_live
    from test_native_parent_images import images_task
    from test_round1_auto_freeze import public_settings
    public_settings.__wrapped__(monkeypatch)
    proposed = proposed_live.__wrapped__(live, monkeypatch)
    original = images_task.__wrapped__(proposed, monkeypatch)
    try:
        yield next(original)
    finally:
        original.close()


def test_native_common_workflow_does_not_dispatch_from_preview_profile(preview_native_images,monkeypatch,tmp_path):
    from shared_platform import operations_publication_common as common
    from test_native_r2_service_consumers import _complete_images, _runtime
    from test_native_parent_images import FixtureClient
    v=preview_native_images
    task=_complete_images(v,monkeypatch)
    installed=_runtime(v,monkeypatch,tmp_path,enabled=True)
    try:
        engine=installed._operations.engine
        code,view=server._preview_r3_common_stage({'offer_id':task['scope']['offer_id'],
            'release_stage':'R3_COMMON','publication_targets':['miaoshou:COMMON']})
        assert code==200
        plan=view['common']['plan']
        binding=common._verified_binding(task,view['common'],v['profile'])
        # The service's exact profile is now preview; its genuine source was
        # read with the original stable owner before this deliberate drift.
        profile=replace(v['profile'],environment='preview')
        installed._operations.profile=profile
        assert profile.environment=='preview'
        persisted=installed.store.create_plan(plan['payload'])
        assert all(persisted[key]==plan[key] for key in ('plan_id','payload_digest','payload'))
        from modules.miaoshou import client
        calls=[]
        def forbidden(*args,**kwargs):
            calls.append((args,kwargs))
            pytest.fail('Preview must not enter the COMMON transport')
        monkeypatch.setattr(client,'post_open',forbidden)
        paid_before=list(FixtureClient.calls)
        before=list(calls)
        database=installed.store.path.read_bytes()
        with pytest.raises(ApprovalBlocked,match='NATIVE_COMMON_TASK_STABLE_RUNTIME_REQUIRED'):
            installed.prepare_common_for_task(engine,task,'not-a-lease',profile,binding,plan)
        assert calls==before
        assert FixtureClient.calls==paid_before
        assert installed.store.get_plan(plan['plan_id'])['status']==release_store.PLAN_PENDING_APPROVAL
        assert installed.store.path.read_bytes()==database
        with installed.store._connect_readonly() as db:
            assert db.execute('SELECT COUNT(*) FROM release_runs').fetchone()[0]==0
    finally:installed.close()


def test_real_unknown_target_claim_cannot_be_dispatched_again_after_restart(live,monkeypatch,tmp_path):
    installed,market,*_=_installed(live,monkeypatch,tmp_path)
    calls=_registry(monkeypatch,unknown=True)
    try:
        prepared=installed.prepare(market['plan_id'])
        approved=installed.decide(nonce=prepared['nonce'],review_digest=prepared['review_digest'])
        code,first=installed.resume(approved['decision_id'])
        count=len(calls);assert count>0
        code,second=installed.resume(approved['decision_id'])
        assert len(calls)==count
        assert not second.get('completed')
        with installed.store._connect_readonly() as db:
            assert db.execute('SELECT COUNT(*) FROM release_runs WHERE plan_id=?',(market['plan_id'],)).fetchone()[0]==1
    finally:installed.close()


def test_new_native_scope_cannot_be_opened_with_caller_actor_or_legacy_tokens(live,monkeypatch,tmp_path):
    installed,market,*_=_installed(live,monkeypatch,tmp_path)
    try:
        prepared=installed.prepare(market['plan_id'])
        installed.decide(nonce=prepared['nonce'],review_digest=prepared['review_digest'])
        before=installed.store.path.read_bytes()
        with pytest.raises(ApprovalBlocked,match='SERVICE_CONSUMER_REQUIRED'):
            execution.execute_decision({'identity_verified':True},'caller-decision')
        with pytest.raises(release_store.ReleaseAuthorizationError,match='native sole final decision requires'):
            installed.store.start_run(market['plan_id'])
        assert installed.store.path.read_bytes()==before
        assert execution._ACTIVE.get() is None
    finally:installed.close()


@pytest.mark.parametrize('damage',['wrong-origin','extra-actor','wrong-review'])
def test_real_http_rejects_caller_identity_origin_and_changed_display(live,monkeypatch,tmp_path,damage):
    installed,market,*_=_installed(live,monkeypatch,tmp_path)
    http,thread=_http(installed)
    try:
        code,prepared=_post(http,channel.PREFIX+'prepare',{'plan_id':market['plan_id']})
        assert code==200,prepared
        body={'nonce':prepared['nonce'],'review_digest':prepared['review_digest']}
        if damage=='extra-actor':body['approved_by']='Kyle'
        if damage=='wrong-review':body['review_digest']='sha256:'+'0'*64
        before=installed.store.path.read_bytes()
        code,rejected=_post(http,channel.PREFIX+'decision',body,origin=damage!='wrong-origin')
        assert code==409 and rejected['execution_authority'] is False
        assert installed.store.path.read_bytes()==before
        with installed.store._connect_readonly() as db:
            assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0]==0
    finally:http.shutdown();http.server_close();thread.join(timeout=5);installed.close()


def test_uninstalled_native_runtime_retains_decision_and_does_not_dispatch_or_request_new_approval(live,monkeypatch,tmp_path):
    installed,market,*_=_installed(live,monkeypatch,tmp_path,enabled=False)
    calls=_registry(monkeypatch)
    monkeypatch.setattr(server,'_r3_marketplace_business_execution_gate',lambda:(409,{
        'ok':False,'error':'MARKETPLACE_BUSINESS_EXECUTION_DISABLED','external_writes_performed':[]}))
    try:
        value=installed.prepare(market['plan_id'])
        approved=installed.decide(nonce=value['nonce'],review_digest=value['review_digest'])
        code,result=installed.resume(approved['decision_id'])
        assert code==409 and result['error']=='NATIVE_NEW_DECISION_EXECUTION_NOT_INSTALLED' and calls==[]
        assert installed.store.get_plan(market['plan_id'])['status']==release_store.PLAN_APPROVED
        with installed.store._connect_readonly() as db:
            assert db.execute('SELECT COUNT(*) FROM native_sole_final_decisions').fetchone()[0]==1
            assert db.execute('SELECT COUNT(*) FROM release_runs WHERE plan_id=?',(market['plan_id'],)).fetchone()[0]==0
    finally:installed.close()
