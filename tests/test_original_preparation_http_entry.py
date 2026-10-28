"""Real maintenance HTTP framing; closed domain probe, durable target consumer."""
from copy import deepcopy
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from types import SimpleNamespace
import threading

import pytest

from test_prepared_target_reduction import prepared as retained_prepared
from test_prepared_target_reinstatement import restoration_input, restore, installed_consumer
from test_prepared_target_consumer import common_payload
from shared_platform.release_store import ReleaseStore
from shared_platform.operations_launch import maintenance_handler
from shared_platform.original_prepared_service import SCOPE


@pytest.fixture
def prepared(tmp_path,monkeypatch):
    from shared_platform.workbench_store import WorkbenchStore
    monkeypatch.setattr(WorkbenchStore,'_next_id',lambda *_:'TASK-20261006-001')
    return retained_prepared.__wrapped__(tmp_path)


@pytest.fixture
def entry(prepared, tmp_path, monkeypatch):
    from modules.products import server
    from shared_platform import original_prepared_continuation as retained
    from shared_platform.original_prepared_continuation import OriginalPreparedTaskContinuation
    from shared_platform.native_sole_final_service import NativeSoleFinalService
    inputs=restoration_input(prepared);successor=restore(prepared,inputs)
    consumer=installed_consumer(prepared,inputs[0],successor,tmp_path,monkeypatch)
    binding=object.__new__(OriginalPreparedTaskContinuation)
    binding.engine=prepared[0];binding.service_profile=SimpleNamespace(root=tmp_path)
    binding.original_profile=SimpleNamespace(version=prepared[0].release['code_version'])
    binding.pins=SimpleNamespace(task_id=prepared[1],offer_id='4039931504',
        prepared_reference=consumer.pins.prepared_reference,r2_identity_json=consumer.pins.r2_identity_json)
    binding.release_store=ReleaseStore(tmp_path/'release.db')
    payload=common_payload(inputs[0])
    payload.update(plan_id='closed-common-plan',seller_sku='0989',product_package_id='closed:product',
                   content_package_id='closed:content')
    payload['r3_stage_binding']['native_preparation_source']={
        'offer_id':binding.pins.offer_id,'prepared_reference':binding.pins.prepared_reference,
        'round1_snapshot_digest':payload['r3_stage_binding']['round1_snapshot_digest'],
        'targets':inputs[0]['original_scope']['shops'],
    }
    plan=binding.release_store.create_plan(payload)
    reads=[]
    # Source/R1/R2 producer seams remain covered by the concrete retained-source
    # tests; HTTP tests isolate delegation/framing, with a real SQLite consumer.
    binding.verify_sources=lambda:reads.append('source')
    binding.read_documents=lambda:reads.append('retained-r1-r2') or {}
    monkeypatch.setattr(retained,'_INSTALLED',binding)
    final=object.__new__(NativeSoleFinalService)
    final._operations=SimpleNamespace(original_continuation=binding,engine=binding.engine,
                                      profile=binding.original_profile)
    final._closed=False;final._stopping=False
    monkeypatch.setattr(server,'_NATIVE_FINAL_SERVICE',final)
    calls=[]
    class Probe(BaseHTTPRequestHandler):
        def _json(self,status,payload):
            raw=json.dumps(payload).encode();self.send_response(status)
            self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
        def _read_json(self):return json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        def do_POST(self):
            calls.append((self.path,self._read_json()))
            self._json(200,{'ok':True,'closed_local_domain_probe':True,'merchant_writes':0})
        def log_message(self,*args):pass
    http=ThreadingHTTPServer(('127.0.0.1',0),maintenance_handler(Probe,native_service_scope=SCOPE))
    http.native_final_review=final
    http.native_service_capability={'status':'REGISTERED','scope':SCOPE,'offer_id':binding.pins.offer_id,
        'original_task_id':binding.pins.task_id,'original_task_release':binding.engine.release,
        'original_executor_only':True,'new_task_worker_enabled':False,'historical_task_scan':False}
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    def post(path,body,headers=None):
        conn=HTTPConnection('127.0.0.1',http.server_port,timeout=4)
        raw=body if isinstance(body,str) else json.dumps(body)
        own={'Content-Type':'application/json','Origin':'http://127.0.0.1:'+str(http.server_port)}
        own.update(headers or {})
        try:
            conn.request('POST',path,raw,own);response=conn.getresponse()
            return response.status,json.loads(response.read())
        finally:conn.close()
    yield SimpleNamespace(http=http,post=post,calls=calls,binding=binding,consumer=consumer,reads=reads,
                          final=final,plan=plan,payload=payload)
    http.shutdown();http.server_close();thread.join()


def test_original_common_preview_reaches_existing_domain_boundary(entry):
    body={'offer_id':'4039931504','release_stage':'R3_COMMON','publication_targets':['miaoshou:COMMON']}
    status,value=entry.post('/api/product-workspace/r3-common/preview',body)
    assert status==200,value
    assert entry.calls==[('/api/product-workspace/r3-common/preview',body)]
    assert value['merchant_writes']==0 and entry.reads


def test_original_final_candidate_preview_reaches_existing_domain_boundary(entry):
    status,value=entry.post('/api/product-workspace/r3-marketplace/preview',{'offer_id':'4039931504'})
    assert status==200,value
    assert len(entry.calls)==1 and value['merchant_writes']==0


def commit_body(entry):
    return {'offer_id':'4039931504','release_stage':'R3_COMMON','publication_targets':['miaoshou:COMMON'],
        'plan_id':entry.plan['plan_id'],'confirmation_token':entry.plan['confirmation_token'],
        'confirm_miaoshou_write':True}


def test_real_stored_common_plan_and_durable_fifteen_consumer_delegate_once(entry):
    before=entry.binding.release_store.path.read_bytes()
    body=commit_body(entry)
    status,value=entry.post('/api/product-workspace/miaoshou-draft/commit',body)
    assert status==200,value
    assert entry.calls==[('/api/product-workspace/miaoshou-draft/commit',body)]
    assert value['merchant_writes']==0
    assert entry.binding.release_store.path.read_bytes()==before
    assert len(entry.consumer.read_effective_scope('4039931504')['shops'])==15


@pytest.mark.parametrize('drift',['missing-plan','token','offer','targets','native-source','r2-identity','market-targets'])
def test_common_commit_rejects_real_plan_or_source_drift_without_delegation(entry,drift):
    body=commit_body(entry)
    if drift=='missing-plan':body['plan_id']='absent'
    elif drift=='token':body['confirmation_token']='different'
    elif drift=='offer':body['offer_id']='other'
    else:
        payload=deepcopy(entry.payload);payload['plan_id']='closed-drift-'+drift;payload['seller_sku']='0990'
        if drift=='targets':payload['targets']=['ozon:RU']
        elif drift=='native-source':payload['r3_stage_binding']['native_preparation_source']['prepared_reference']='foreign'
        elif drift=='r2-identity':payload['r3_stage_binding']['r2_identity']['identity_digest']='changed'
        else:payload['r3_stage_binding']['marketplace_targets'].remove('ozon:RU')
        plan=entry.binding.release_store.create_plan(payload)
        body.update(plan_id=plan['plan_id'],confirmation_token=plan['confirmation_token'])
    before=entry.binding.release_store.path.read_bytes()
    status,_=entry.post('/api/product-workspace/miaoshou-draft/commit',body)
    assert status==409 and entry.calls==[]
    assert entry.binding.release_store.path.read_bytes()==before


@pytest.mark.parametrize('drift',[None,'stage','targets','r2-identity'])
def test_actual_registered_r2_gate_accepts_only_common_singleton_commit(entry,monkeypatch,drift):
    from modules.products import server
    from shared_platform import publication_r2_review as review, publication_r3_image_bridge as bridge
    identity=json.loads(entry.binding.pins.r2_identity_json)
    monkeypatch.setattr(review,'review_runtime_root',lambda _:entry.binding.service_profile.root)
    monkeypatch.setattr(review,'review_view',lambda offer_id,**_: {'offer_id':offer_id,
        'r2_consumer':{'status':'PASSED','identity':identity}})
    monkeypatch.setattr(server,'_load_r2_documents_for_service',lambda _: {'closed_retained_producer':True})
    monkeypatch.setattr(bridge,'validate_r2_identity',lambda _:identity if drift!='r2-identity' else {})
    body=commit_body(entry)
    if drift=='stage':body['release_stage']='FINAL_MARKETPLACE'
    if drift=='targets':body['publication_targets']=['miaoshou:COMMON','ozon:RU']
    assert server._registered_r3_post_allowed('/api/product-workspace/miaoshou-draft/commit',body) is (drift is None)


@pytest.mark.parametrize('body',[
    {'offer_id':'different','release_stage':'R3_COMMON','publication_targets':['miaoshou:COMMON']},
    {'offer_id':'4039931504','release_stage':'R3_COMMON','publication_targets':['tiktok:LH_MY']},
    {'offer_id':'4039931504','release_stage':'R3_COMMON','publication_targets':['miaoshou:COMMON'],'user_approved':True},
    {'offer_id':'4039931504','release_stage':'R3_COMMON','publication_targets':['miaoshou:COMMON'],'task_id':'other'},
])
def test_original_common_refuses_scope_or_caller_approval_drift(entry,body):
    status,_=entry.post('/api/product-workspace/r3-common/preview',body)
    assert status==409 and entry.calls==[]


@pytest.mark.parametrize('path',[
    '/api/product-workspace/r3-marketplace/approve','/api/product-workspace/r3-marketplace/resume-binding',
    '/api/product-workspace/publish-tiktok','/api/product-workspace/publish-shopee-global',
    '/api/product-workspace/publish-ozon','/api/orbit/tasks/old/retry',
])
def test_preparation_does_not_open_merchant_or_legacy_decision_routes(entry,path):
    status,_=entry.post(path,{'offer_id':'4039931504','user_approved':True})
    assert status==409 and entry.calls==[]


@pytest.mark.parametrize('mutation', ['missing-final','closed-final','other-engine','missing-capability'])
def test_scope_label_never_replaces_startup_installation(entry,mutation):
    if mutation=='missing-final':entry.http.native_final_review=None
    elif mutation=='closed-final':entry.final._closed=True
    elif mutation=='other-engine':entry.final._operations.engine=object()
    else:entry.http.native_service_capability={}
    status,_=entry.post('/api/product-workspace/r3-common/preview',
        {'offer_id':'4039931504','release_stage':'R3_COMMON','publication_targets':['miaoshou:COMMON']})
    assert status==409 and not entry.calls


def test_query_and_duplicate_json_identity_fail_closed(entry):
    for path,body in [('/api/product-workspace/r3-common/preview?offer_id=other',{}),
       ('/api/product-workspace/r3-common/preview','{"offer_id":"4039931504","offer_id":"other","release_stage":"R3_COMMON","publication_targets":["miaoshou:COMMON"]}')]:
        status,_=entry.post(path,body)
        assert status in {400,409} and not entry.calls


def test_wrong_origin_does_not_delegate(entry):
    status,_=entry.post('/api/product-workspace/r3-common/preview',
       {'offer_id':'4039931504','release_stage':'R3_COMMON','publication_targets':['miaoshou:COMMON']},
       {'Origin':'http://foreign.invalid'})
    assert status==403 and not entry.calls


def test_original_startup_ready_lists_exact_preparation_routes(entry,monkeypatch,tmp_path):
    from contextlib import contextmanager
    from pathlib import Path
    from shared_platform import operations_launch as launch, original_prepared_service as original
    from shared_platform import native_service_lifecycle as lifetime, bounded_web_server
    from domains.supply_chain_operations import captured_serving
    from scripts import stable_runtime_bootstrap as bootstrap
    from core import config as settings, db as catalog
    from shared_platform import operations_service, catalog_images
    from shared_platform.operations_runtime import RuntimeProfile
    import sqlite3
    paths={name:tmp_path/name for name in ('settings','catalog_database','catalog_weight_overrides',
        'release_store_path','report_store_path','original_profit_asset_root','ozon_data_root')}
    paths['operations_data_root']=tmp_path
    profile=RuntimeProfile(tmp_path,tmp_path,'stable','closed-service','closed-manifest')
    config={'native_service_scope':SCOPE,'manifest_digest':profile.manifest_digest,
            'r3_config_root':str(tmp_path),'port':0,'ready_path':str(tmp_path/'closed-ready.json')}
    operations=SimpleNamespace(worker=SimpleNamespace(close=lambda:None))
    monkeypatch.setattr(launch,'os',SimpleNamespace(environ={},getpid=lambda:123))
    monkeypatch.setattr(captured_serving,'deployment_capture',lambda _:None)
    monkeypatch.setattr(bootstrap,'prebind_workbench_store',lambda _:tmp_path/'closed-workbench.db')
    monkeypatch.setattr(launch,'preflight_deployment',lambda *_:paths)
    monkeypatch.setattr(launch,'bind_review_paths',lambda _:None)
    monkeypatch.setattr(RuntimeProfile,'capture',lambda _:profile)
    monkeypatch.setattr(bootstrap,'configure_stable_runtime',lambda *_,**__: {})
    monkeypatch.setattr(launch,'verify_workbench_store_pin',lambda _:None)
    monkeypatch.setattr(settings,'CONFIG_PATH',settings.CONFIG_PATH)
    monkeypatch.setattr(settings,'FALLBACK_CONFIG_PATHS',settings.FALLBACK_CONFIG_PATHS)
    from modules.catalog import weight_overrides
    from shared_platform import original_profit_reports
    monkeypatch.setattr(weight_overrides,'PATH',weight_overrides.PATH)
    monkeypatch.setattr(original_profit_reports,'ASSET_ROOT',original_profit_reports.ASSET_ROOT)
    monkeypatch.setattr(settings,'load_settings',lambda: {})
    monkeypatch.setattr(catalog,'connect_readonly',lambda:sqlite3.connect(':memory:'))
    monkeypatch.setattr(catalog_images,'ImageCache',lambda *_:None)
    monkeypatch.setattr(operations_service,'get_runtime',lambda *_:operations)
    binding=entry.binding
    @contextmanager
    def installed(*_):yield {'binding':binding}
    monkeypatch.setattr(original,'installed_original_service',installed)
    class ClosedHTTP:
        server_port=0
        def __init__(self,*_):pass
        def serve_forever(self):pass
        def server_close(self):pass
    monkeypatch.setattr(bounded_web_server,'BoundedThreadingHTTPServer',ClosedHTTP)
    launch.serve(config,tmp_path)
    ready=json.loads(Path(config['ready_path']).read_bytes())
    assert set(original.PREPARATION_POST_PATHS)<=set(ready['allowed_local_posts'])
    assert set(ready['allowed_local_posts'])-set(lifetime.POST_PATHS)=={
        '/api/catalog/cost','/api/orbit/tasks',*original.PREPARATION_POST_PATHS}
    assert ready['native_service']['COMMON_release_receipt']=='NOT_ISSUED'
    assert ready['native_service']['new_task_worker_enabled'] is False
