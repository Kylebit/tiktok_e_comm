"""Formal serve entry with owned stores/HTTP and CLOSED provider transports."""
from types import SimpleNamespace
from pathlib import Path
from http.client import HTTPConnection
import json
import sqlite3
import threading
import sys

import pytest

from modules.products import server
from shared_platform import native_service_lifecycle as lifetime, operations_launch as launch
from shared_platform import native_common_edit_boundary as boundary, native_sole_final_service as channel
from shared_platform import native_common_technical_execution as technical, native_sole_final_decision as final
from shared_platform.operations_runtime import RuntimeProfile
from shared_platform.native_task_preparation import ExplicitNewTaskWorker
from shared_platform.bounded_web_server import BoundedThreadingHTTPServer
from test_native_r2_service_consumers import live, images_task, _complete_images
from test_round1_workspace_freeze import live as original_live
from test_round1_auto_freeze import public_settings
from test_native_common_technical_execution import _install_owned
from test_native_common_service_facts import _installed, _closed_signed_transport
from test_native_sole_final_service import _registry


def _post(http, path, body, *, foreign=False):
    connection = HTTPConnection('127.0.0.1', http.server_port, timeout=40)
    try:
        origin = 'http://127.0.0.1:' + str(http.server_port)
        connection.request('POST', path, json.dumps(body).encode(),
            {'Content-Type':'application/json', 'Origin': 'http://foreign.invalid' if foreign else origin})
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally: connection.close()


def _serve_seams(v, monkeypatch, tmp_path):
    """Unrelated deployment/catalog seams only; native lifecycle/Handler are real."""
    from core import config as settings, db as catalog
    from scripts import stable_runtime_bootstrap as bootstrap
    from shared_platform import operations_service
    from domains.supply_chain_operations import captured_serving
    from shared_platform.catalog_images import ImageCache
    paths = {'operations_data_root':v['profile'].data_root}
    for key in ('settings','catalog_database','catalog_weight_overrides','release_store_path',
                'report_store_path','original_profit_asset_root','ozon_data_root'):
        paths[key] = tmp_path / key
    # Keep the same public rates used by the original R1 producer. Replacing
    # load_settings with None loses pricing facts during frozen-status rechecks.
    public_configuration = settings.load_settings()
    assert type(public_configuration) is dict and public_configuration['exchange_rates']
    paths['settings'].write_text(json.dumps(public_configuration), encoding='utf-8')
    operations = SimpleNamespace(engine=v['engine'], profile=v['profile'],
                                 worker=v['worker'], worker_enabled=False)
    config = {'code_root':str(v['profile'].root), 'manifest_digest':v['profile'].manifest_digest,
        'port':0, 'r3_config_root':str(v['profile'].root), 'settings':str(paths['settings']),
        'ready_path':str(tmp_path/'launcher-ready.json'), 'native_service_scope':lifetime.SCOPE}
    monkeypatch.setattr(captured_serving, 'deployment_capture', lambda _:None)
    monkeypatch.setattr(bootstrap, 'prebind_workbench_store', lambda _:tmp_path/'unused-workbench.db')
    monkeypatch.setattr(launch, 'preflight_deployment', lambda *_:paths)
    monkeypatch.setattr(launch, 'bind_review_paths', lambda _:None)
    monkeypatch.setattr(bootstrap, 'configure_stable_runtime', lambda *a,**k:{})
    monkeypatch.setattr(launch, 'verify_workbench_store_pin', lambda _:None)
    monkeypatch.setattr(RuntimeProfile, 'capture', lambda _:v['profile'])
    monkeypatch.setattr(catalog, 'connect_readonly', lambda:sqlite3.connect(':memory:'))
    monkeypatch.setattr('shared_platform.catalog_images.ImageCache', lambda *_:None)
    monkeypatch.setattr(operations_service, 'get_runtime', lambda http,_:setattr(http,'operations_runtime',operations) or operations)
    # No background run/scan in the test; actual create/lease/producer and final
    # executor are exercised below. Original legacy worker remains disabled.
    monkeypatch.setattr(ExplicitNewTaskWorker, 'start', lambda self:None)
    return config, operations


def _configured_market(signed_config):
    """Original full COMMON/paid policy plus explicit closed market budgets."""
    from test_b4b_release_compiler import policy, INCIDENTS
    from shared_platform.publication_runtime_config import diagnose
    path = signed_config.root / signed_config.policy
    original = json.loads(path.read_bytes())
    original['write_budgets'] = policy(40)['write_budgets']
    path.write_text(json.dumps(original), encoding='utf-8')
    (signed_config.root / signed_config.incidents).write_text(json.dumps(INCIDENTS), encoding='utf-8')
    configuration, configured = diagnose(signed_config)
    assert configuration['status'] == 'CONFIGURED' and configured
    assert original['miaoshou']['maximum_confirmed_writes_per_product'] == 1
    assert original['paid_models']['maximum_confirmed_requests_per_product'] == 40
    assert original['review_contract']['sole_human_gate'] == 'FINAL_MARKETPLACE_PUBLISH'


def test_formal_serve_registers_exact_new_task_and_one_native_decision_then_closes(images_task, monkeypatch, tmp_path):
    from shared_platform import publication_r3_image_bridge as bridge
    from test_b4b_release_compiler import policy, INCIDENTS
    from shared_platform.ozon_runtime_credentials import PinnedOzonCredentials
    from shared_platform.ozon_stock_warehouse_decision import resolve_warehouse_decision
    v=images_task
    task=_complete_images(v,monkeypatch)
    config,operations=_serve_seams(v,monkeypatch,tmp_path)
    config['agent_executable']=sys.executable
    _install_owned(v['live']['store'],tmp_path)
    signed_config,_=_installed(monkeypatch,tmp_path,v['live']['store'])
    _configured_market(signed_config)
    monkeypatch.setattr(server,'R3_STARTUP_CONFIG',signed_config)
    with technical._existing_transaction(v['live']['store']) as db:
        for statement in final.TABLES+final.TRIGGERS:db.execute(statement)
    old=operations.engine.create({'template':'profit','source_key':'old-unrelated', 'scope':{'month':'2026-08'}})
    prior = {key:getattr(server,key) for key in ('_COMMON_STANDING_POLICY_READER',
        '_COMMON_DETAIL_OBSERVER_FACTORY','_COMMON_SIGNING_CONTEXT_CONFIG','_NATIVE_FINAL_SERVICE')}
    prior_boundary=boundary._ACTIVE
    original_serve=BoundedThreadingHTTPServer.serve_forever
    observed=[]
    def drive(http):
        installed=http.native_final_review
        assert type(installed) is channel.NativeSoleFinalService
        assert installed._operations is operations and not operations.worker_enabled
        worker=operations.new_task_worker
        assert not worker.owns(old['task_id'])
        assert Path(__import__('os').environ['ORBIT_OPERATIONS_AGENT_EXECUTABLE'])==Path(sys.executable).resolve()
        documents=server._load_r2_documents_for_service(task['scope']['offer_id'])
        code,view=server._preview_r3_common_stage({'offer_id':task['scope']['offer_id'],
            'release_stage':'R3_COMMON','publication_targets':['miaoshou:COMMON']})
        assert code==200,view
        common=view['common']['plan']
        assert installed.store.get_plan(common['plan_id']) is None
        pinned=PinnedOzonCredentials('12345','owned-secret','a'*64)
        monkeypatch.setattr('shared_platform.ozon_runtime_credentials.required_pinned_ozon_credentials', lambda: pinned)
        warehouse=resolve_warehouse_decision(offer_id=task['scope']['offer_id'],round1=documents['round1_snapshot'],
            pinned=pinned,
            post_bound=lambda *a,**k:{'warehouses':[{'warehouse_id':71,'status':'active','is_kgt':False}]})
        (v['profile'].root/'reports/product-preparation'/task['scope']['offer_id']/'ozon-warehouse-readback.json').write_text(json.dumps(warehouse),encoding='utf-8')
        writes=_closed_signed_transport(monkeypatch,installed.store,common['payload'])
        from shared_platform import operations_publication_common as workflow
        workflow.run(v['engine'],task,v['token'],v['profile'])
        persisted=installed.store.get_plan(common['plan_id'])
        assert all(persisted[key]==common[key] for key in ('plan_id','payload_digest','payload'))
        assert persisted['status']=='PENDING_APPROVAL' and persisted['approval'] is None
        code,completed=server._prepare_miaoshou_release({'offer_id':task['scope']['offer_id'],
            'plan_id':common['plan_id'],'confirm_miaoshou_write':True})
        assert code==200 and completed['native_run']['state']=='CONFIRMED_WRITE',completed
        material=bridge.build_marketplace_review_material(documents,installed.store.get_plan(common['plan_id']),
            installed.store.get_run(completed['native_run']['run_id']),policy=policy(40),incidents=INCIDENTS,
            ozon_stock_decision=warehouse['decision'],category_store=installed.store)
        market=installed.store.create_plan(material['payload'])
        # COMMON preparation alone does not return the original worker lease.
        # Enter the real release adapter so it verifies the native rounds and
        # binds the original final-review action before the independent actor.
        from shared_platform import operations_publication
        release_adapter,_=operations_publication.bindings()
        current=v['engine'].get(task['task_id'])
        release_adapter(v['engine'],current,v['token'],v['profile'])
        waiting=v['engine'].get(task['task_id'])
        assert waiting['execution_state']=='waiting_user' and waiting['current_step']=='release'
        assert waiting['worker'] is None
        with pytest.raises(ValueError,match='stale or invalid lease'):
            v['engine'].heartbeat(task['task_id'],v['token'])
        assert waiting['required_action']['kind']=='review'
        assert waiting['required_action']['receipt_binding']['native_r1']==current['checkpoint']['native_r1']
        calls=_registry(monkeypatch)
        listener=threading.Thread(target=lambda:original_serve(http),daemon=True)
        listener.start()
        try:
            payload={'template':'publication','source_key':'formal-explicit-post', 'scope':task['scope']}
            code,created=_post(http,'/api/orbit/tasks',payload)
            assert code==201,created
            assert worker.owns(created['task']['task_id']) and not worker.owns(old['task_id'])
            events=operations.engine.store.events(created['task']['task_id'])
            assert sum(row['event_type']=='explicit_new_post_preparation' for row in events)==1
            code,repeated=_post(http,'/api/orbit/tasks',payload)
            assert repeated['task']['task_id']==created['task']['task_id']
            code,review=_post(http,channel.PREFIX+'prepare',{'plan_id':market['plan_id']})
            assert code==200,review
            code,approved=_post(http,channel.PREFIX+'decision',{'nonce':review['nonce'],'review_digest':review['review_digest']})
            assert code==202 and approved['approval_saved'],approved
            installed._execution_thread.join(timeout=40)
            assert not installed._execution_thread.is_alive()
            assert len(calls)==len(market['targets'])
            code,again=_post(http,channel.PREFIX+'resume',{'decision_id':approved['decision']['decision_id']})
            assert code==200 and len(calls)==len(market['targets']),again
            assert _post(http,channel.PREFIX+'decision',{},foreign=True)[0]==403
            assert _post(http,'/api/product-workspace/publish-ozon',{})[0]==409
            assert writes.count(boundary.EDIT_PATH)==1
            assert not worker.owns(old['task_id'])
            observed.append(installed)
        finally:
            http.shutdown();listener.join(timeout=5)
            assert not listener.is_alive()
    monkeypatch.setattr(BoundedThreadingHTTPServer,'serve_forever',drive)
    monkeypatch.setenv('ORBIT_OPERATIONS_AGENT_EXECUTABLE','ambient-untrusted.exe')
    launch.serve(config,v['profile'].root)
    assert observed[0]._closed and getattr(operations,'new_task_worker',None) is None
    assert all(getattr(server,key) is value for key,value in prior.items())
    assert boundary._ACTIVE is prior_boundary
    ready=json.loads(Path(config['ready_path']).read_bytes())
    assert ready['worker_enabled'] is ready['provider_executors_initialized'] is False
    assert ready['native_service']['status']=='REGISTERED'
    assert ready['execution_mode']=='scoped-native'
    assert ready['native_service']['agent_readiness']=='FIXED_EXECUTABLE_PRESENT_UNVERIFIED'
    assert set(ready['allowed_local_posts'])=={'/api/catalog/cost','/api/orbit/tasks',*lifetime.POST_PATHS}


@pytest.mark.parametrize('stage',['policy','observer','signing','worker-start','actor-start'])
def test_partial_native_initialization_restores_every_owned_stage(monkeypatch, stage):
    """Factory fault seams validate cleanup, not business/provider authority."""
    from shared_platform import native_task_preparation, release_store
    baseline={name:object() for name in ('_COMMON_STANDING_POLICY_READER','_COMMON_DETAIL_OBSERVER_FACTORY',
        '_COMMON_SIGNING_CONTEXT_CONFIG','_NATIVE_FINAL_SERVICE')}
    app=SimpleNamespace(**baseline,R3_STARTUP_CONFIG=object())
    http=SimpleNamespace()
    operations=SimpleNamespace(profile=SimpleNamespace(data_root=Path('E:/unused-owned')))
    closed=[]
    def factory(name,field):
        def install(config):
            setattr(app,field,object())
            if stage==name:raise ValueError('OWNED_STARTUP_FAILURE')
        return install
    for name,field in zip(('policy','observer','signing'),list(baseline)[:3]):
        setattr(app,'_install_service_common_'+{'policy':'standing_policy','observer':'detail_observer','signing':'signing_context'}[name],factory(name,field))
    before=boundary._ACTIVE
    marker=object()
    monkeypatch.setattr(boundary,'install_service_boundary',lambda config:(before,marker))
    monkeypatch.setattr(boundary,'restore_service_boundary',lambda old,new:closed.append(('boundary',old is before,new is marker)))
    def install_worker(runtime):
        worker=SimpleNamespace(close=lambda:closed.append('worker'))
        runtime.new_task_worker=worker
        if stage=='worker-start':raise ValueError('OWNED_STARTUP_FAILURE')
        return worker
    monkeypatch.setattr(native_task_preparation,'install_explicit_new_task_preparation',install_worker)
    class FaultActor:
        def __init__(self,*a,**k):pass
        def start(self):raise ValueError('OWNED_STARTUP_FAILURE')
        def close(self):closed.append('actor')
    monkeypatch.setattr(channel,'NativeSoleFinalService',FaultActor)
    from contextlib import contextmanager
    @contextmanager
    def existing():
        yield SimpleNamespace(execute=lambda _:None)
    monkeypatch.setattr(release_store,'default_release_store',lambda:SimpleNamespace(_connect_readonly=existing))
    monkeypatch.setattr(technical,'_schema_installed',lambda _:True)
    monkeypatch.setattr(final,'check_schema',lambda _:None)
    with pytest.raises(ValueError,match='^OWNED_STARTUP_FAILURE$'):
        with lifetime.installed_native_services(app,http,operations):pytest.fail('Fault must not reach service')
    assert all(getattr(app,key) is value for key,value in baseline.items())
    assert not hasattr(http,'native_final_review') and not hasattr(operations,'new_task_worker')
    if stage in {'worker-start','actor-start'}:assert 'worker' in closed
    if stage=='actor-start':assert closed[0]=='actor' and closed[1]=='worker'
    if stage in {'worker-start','actor-start'}:assert closed[-1]==('boundary',True,True)


def test_formal_scope_missing_fixed_executable_keeps_readonly_but_rejects_new_tasks(images_task, monkeypatch, tmp_path):
    v=images_task
    config,operations=_serve_seams(v,monkeypatch,tmp_path)
    _install_owned(v['live']['store'],tmp_path)
    signed,_=_installed(monkeypatch,tmp_path,v['live']['store'])
    monkeypatch.setattr(server,'R3_STARTUP_CONFIG',signed)
    with technical._existing_transaction(v['live']['store']) as db:
        for statement in final.TABLES+final.TRIGGERS:db.execute(statement)
    original_serve=BoundedThreadingHTTPServer.serve_forever
    def drive(http):
        assert type(http.native_final_review) is channel.NativeSoleFinalService
        assert not operations.worker_enabled
        before=operations.new_task_worker.status()['explicit_new_task_count']
        listener=threading.Thread(target=lambda:original_serve(http),daemon=True)
        listener.start()
        try:
            code,value=_post(http,'/api/orbit/tasks',{'template':'publication',
                'source_key':'missing-fixed-executable','scope':v['task']['scope']})
            assert code==409 and value['error']=='NATIVE_FIXED_AGENT_EXECUTABLE_REQUIRED',value
            assert value['external_writes_performed']==[]
            assert operations.new_task_worker.status()['explicit_new_task_count']==before
            assert _post(http,channel.PREFIX+'prepare',{'plan_id':'absent-original-plan'})[0]==409
        finally:
            http.shutdown();listener.join(timeout=5)
            assert not listener.is_alive()
    monkeypatch.setattr(BoundedThreadingHTTPServer,'serve_forever',drive)
    monkeypatch.setenv('ORBIT_OPERATIONS_AGENT_EXECUTABLE',sys.executable)
    launch.serve(config,v['profile'].root)
    ready=json.loads(Path(config['ready_path']).read_bytes())
    assert ready['native_service']['new_task_readiness']=='BLOCKED'
    assert ready['native_service']['agent_readiness']=='NATIVE_FIXED_AGENT_EXECUTABLE_REQUIRED'
    assert ready['worker_enabled'] is False and ready['native_service']['historical_task_scan'] is False
    assert 'ORBIT_OPERATIONS_AGENT_EXECUTABLE' not in __import__('os').environ


@pytest.mark.parametrize('cleanup',['ordinary','worker-close-error'])
def test_formal_registration_failure_closes_listener_worker_and_removes_stale_ready(images_task, monkeypatch, tmp_path, cleanup):
    from contextlib import contextmanager
    v=images_task
    config,operations=_serve_seams(v,monkeypatch,tmp_path)
    Path(config['ready_path']).write_text('{"native_service":{"status":"REGISTERED"}}')
    captured=[]
    original_init=BoundedThreadingHTTPServer.__init__
    def capture(self,*args,**kwargs):
        original_init(self,*args,**kwargs);captured.append(self)
    monkeypatch.setattr(BoundedThreadingHTTPServer,'__init__',capture)
    @contextmanager
    def failed(*args):
        raise ValueError('OWNED_FATAL_REGISTRATION_FAILURE')
        yield
    monkeypatch.setattr(lifetime,'installed_native_services',failed)
    monkeypatch.setattr(BoundedThreadingHTTPServer,'serve_forever',lambda _:pytest.fail('Failed startup must not serve'))
    if cleanup=='worker-close-error':
        original_close=operations.worker.close
        def failed_close():
            original_close()
            raise ValueError('OWNED_WORKER_CLOSE_FAILURE')
        monkeypatch.setattr(operations.worker,'close',failed_close)
    expected='OWNED_WORKER_CLOSE_FAILURE' if cleanup=='worker-close-error' else 'OWNED_FATAL_REGISTRATION_FAILURE'
    with pytest.raises(ValueError,match='^'+expected+'$'):
        launch.serve(config,v['profile'].root)
    assert len(captured)==1 and captured[0].socket.fileno()==-1
    assert operations.worker.stop_event.is_set()
    assert not Path(config['ready_path']).exists()
    if cleanup=='worker-close-error':monkeypatch.setattr(operations.worker,'close',original_close)


@pytest.mark.parametrize('unknown',['actor','worker','worker-close'])
def test_unknown_retirement_keeps_original_owner_and_blocks_replacement(monkeypatch, unknown):
    from contextlib import contextmanager
    from shared_platform import native_task_preparation, release_store
    retained=[]
    monkeypatch.setattr(lifetime,'_RETIREMENT_UNKNOWN',retained)
    baseline={name:object() for name in ('_COMMON_STANDING_POLICY_READER','_COMMON_DETAIL_OBSERVER_FACTORY',
        '_COMMON_SIGNING_CONTEXT_CONFIG','_NATIVE_FINAL_SERVICE')}
    app=SimpleNamespace(**baseline,R3_STARTUP_CONFIG=object())
    for name in ('standing_policy','detail_observer','signing_context'):
        setattr(app,'_install_service_common_'+name,lambda _:None)
    @contextmanager
    def existing():yield SimpleNamespace(execute=lambda _:None)
    monkeypatch.setattr(release_store,'default_release_store',lambda:SimpleNamespace(_connect_readonly=existing))
    monkeypatch.setattr(technical,'_schema_installed',lambda _:True)
    monkeypatch.setattr(final,'check_schema',lambda _:None)
    monkeypatch.setattr(boundary,'install_service_boundary',lambda _:(None,object()))
    monkeypatch.setattr(boundary,'restore_service_boundary',lambda *a:None)
    def worker_close():
        if unknown=='worker-close':raise RuntimeError('OWNED_WORKER_RETIREMENT_UNKNOWN')
    worker=SimpleNamespace(close=worker_close,thread=SimpleNamespace(is_alive=lambda:unknown=='worker'),inflight={})
    def install(runtime):runtime.new_task_worker=worker;return worker
    monkeypatch.setattr(native_task_preparation,'install_explicit_new_task_preparation',install)
    class OwnedActor:
        def __init__(self,*a,**k):self.supervisor=object();self.client=object()
        def start(self):return self
        def close(self):
            if unknown=='actor':raise RuntimeError('OWNED_ACTOR_RETIREMENT_UNKNOWN')
    monkeypatch.setattr(channel,'NativeSoleFinalService',OwnedActor)
    http=SimpleNamespace();operations=SimpleNamespace(profile=SimpleNamespace(data_root=Path('E:/unused-owned')))
    with pytest.raises(RuntimeError,match='RETIREMENT_UNKNOWN'):
        with lifetime.installed_native_services(app,http,operations) as value:owner=value['final']
    original=owner if unknown=='actor' else worker
    assert retained==[original]
    assert http.native_service_retirement_unknown is operations.native_service_retirement_unknown is original
    assert all(getattr(app,key) is value for key,value in baseline.items())
    assert not hasattr(http,'native_final_review') and not hasattr(operations,'new_task_worker')
    with pytest.raises(ValueError,match='^NATIVE_SERVICE_RETIREMENT_UNKNOWN$'):
        with lifetime.installed_native_services(app,SimpleNamespace(),SimpleNamespace()):pytest.fail('No replacement')


def test_missing_installed_schema_fails_before_any_native_registration(live, monkeypatch):
    from shared_platform import release_store
    monkeypatch.setattr(release_store,'default_release_store',lambda:live['store'])
    before=live['store'].path.read_bytes()
    app=SimpleNamespace()
    with pytest.raises(ValueError,match='^COMMON_TECHNICAL_SCHEMA_NOT_INSTALLED$'):
        with lifetime.installed_native_services(app,SimpleNamespace(),SimpleNamespace()):pytest.fail('Schema missing')
    assert live['store'].path.read_bytes()==before


@pytest.mark.parametrize('stage',['catalog','image-cache','runtime'])
def test_formal_early_setup_failure_closes_listener_and_never_leaves_ready(images_task, monkeypatch, tmp_path, stage):
    from core import db as catalog
    from shared_platform import catalog_images, operations_service
    v=images_task;config,operations=_serve_seams(v,monkeypatch,tmp_path)
    Path(config['ready_path']).write_text('{"native_service":{"status":"REGISTERED"}}')
    captured=[];original_init=BoundedThreadingHTTPServer.__init__
    def capture(self,*args,**kwargs):original_init(self,*args,**kwargs);captured.append(self)
    monkeypatch.setattr(BoundedThreadingHTTPServer,'__init__',capture)
    def fail(*args):raise ValueError('OWNED_EARLY_SETUP_FAILURE')
    if stage=='catalog':monkeypatch.setattr(catalog,'connect_readonly',fail)
    elif stage=='image-cache':monkeypatch.setattr(catalog_images,'ImageCache',fail)
    else:
        def partial(http,root):
            http.operations_runtime=operations
            fail()
        monkeypatch.setattr(operations_service,'get_runtime',partial)
    monkeypatch.setattr(BoundedThreadingHTTPServer,'serve_forever',lambda _:pytest.fail('No listener serving after setup failure'))
    with pytest.raises(ValueError,match='^OWNED_EARLY_SETUP_FAILURE$'):launch.serve(config,v['profile'].root)
    assert len(captured)==1 and captured[0].socket.fileno()==-1
    assert not Path(config['ready_path']).exists()
    if stage=='runtime':assert operations.worker.stop_event.is_set()
