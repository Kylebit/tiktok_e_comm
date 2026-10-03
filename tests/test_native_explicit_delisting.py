"""Owned HTTP/worker/ledger/Skill, with only official provider calls closed."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import threading
import time

import pytest

from modules.products import server
from shared_platform.bounded_web_server import BoundedThreadingHTTPServer
from shared_platform.native_delisting_preparation import NativeDelistingServiceConfig, DELIST_SCOPE, bindings
from shared_platform.native_task_preparation import install_explicit_new_task_preparation
from shared_platform.operations_runtime import OperationsWorker
from shared_platform.workbench_engine import WorkbenchEngine
from owned_native_parent_profile import owned_profile
from test_native_scoped_service_launcher import _post

SOURCE=Path(__file__).resolve().parents[1]
SOURCE_PATHS=(
    'shared_platform/native_delisting_preparation.py',
    'shared_platform/workbench_delisting_adapter.py',
    'shared_platform/workbench_delisting_receipt.py',
    'shared_platform/operations_http.py',
    'shared_platform/operations_domain_guard.py',
    'shared_platform/workbench_engine.py',
    'skills/delist-products-by-sku/scripts/delist_products_by_sku.py',
    'skills/delist-products-by-sku/SKILL.md',
    'modules/shopee/sync.py',
    'modules/shopee/client.py',
    'modules/shopee/auth.py',
    'core/config.py',
    'core/api_client.py',
)


def _runtime(tmp_path,monkeypatch,*,configured=True,clock=None,platform='shopee'):
    root=tmp_path/'owned-delist-source';root.mkdir()
    for name in SOURCE_PATHS:
        destination=root/name;destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes((SOURCE/name).read_bytes())
        assert destination.read_bytes()==(SOURCE/name).read_bytes()
    tokens=root/'owned-shopee-tokens.json'
    tokens.write_text(json.dumps({'sync_shop_ids':{'PH':101},'shops':{'101':{
        'access_token':'owned-token','expire_at':int(time.time())+7200,'region':'PH'}}}),encoding='utf-8')
    settings={'shopee':{'enabled':True,'environment':'live','partner_id':1,
                       'partner_key':'owned-secret','token_file':str(tokens)}}
    if platform=='tiktok':
        tokens=root/'owned-tiktok-token.json'
        tokens.write_text(json.dumps({'access_token':'owned-token','access_token_expire_in':int(time.time())+7200,
                         'authorized_shops':[{'id':'501','region':'PH','cipher':'owned-cipher'}]}),encoding='utf-8')
        settings={'token_file':str(tokens),'database':str(root/'owned-catalog.db'),
                  'app_key':'owned-app','app_secret':'owned-secret',
                  'api':{'rate_limit_retries':5,'rate_limit_backoff_sec':[0]}}
        monkeypatch.delenv('ORBIT_CATALOG_DATABASE',raising=False)
    elif platform=='ozon':
        directory=root/'owned-ozon-catalog';directory.mkdir()
        (directory/'all_products_attrs.json').write_text('[]',encoding='utf-8')
        settings={'ozon':{'client_id':'owned-client','api_key':'owned-key','data_dir':str(directory)}}
        for name in ('ORBIT_OZON_CREDENTIALS_PATH','ORBIT_OZON_CREDENTIALS_SHA256','ORBIT_OZON_EXPECTED_ACCOUNT_ID'):
            monkeypatch.delenv(name,raising=False)
    (root/'config').mkdir(exist_ok=True)
    path=root/'config/settings.json';path.write_text(json.dumps(settings),encoding='utf-8')
    from core import config
    monkeypatch.setattr(config,'CONFIG_PATH',path)
    monkeypatch.setattr(config,'FALLBACK_CONFIG_PATHS',[])
    monkeypatch.setattr(config,'_cache',None)
    monkeypatch.setattr(config,'_cache_source',None)
    config.load_settings()
    profile=owned_profile(root)
    engine=WorkbenchEngine(profile.data_root/'tasks.db',{
        'code_version':profile.version,'environment':profile.environment,'manifest_digest':profile.manifest_digest},
        **({'clock':clock} if clock else {}))
    monkeypatch.setenv('ORBIT_OPERATIONS_DATA_ROOT',str(profile.data_root))
    monkeypatch.setenv('ORBIT_OPERATIONS_ENV','stable')
    monkeypatch.setenv('ORBIT_OPERATIONS_PROFILE',str(root/'absent-profile.json'))
    monkeypatch.delenv('ORBIT_SHOPEE_TOKEN_PATH',raising=False)
    runtime=SimpleNamespace(engine=engine,profile=profile,worker=OperationsWorker(engine,profile,{}),worker_enabled=False)
    if configured:runtime.native_delisting_config=NativeDelistingServiceConfig.capture(profile,root)
    return runtime,tokens


def _payload(key='new-delist'):
    return {'template':'delisting','source_key':key,'scope':{'skus':['0975'],'shops':['shopee:PH']}}


def _transport(monkeypatch,*,unknown=False,mixed=False,before_write=None):
    from modules.shopee import client,sync,auth,shops
    calls=[];current={'status':'NORMAL'}
    def get(path,shop_id,token,params=None,**kwargs):
        assert shop_id==101 and token=='owned-token'
        calls.append(('READ',path))
        if path.endswith('get_item_list'):
            return {'response':{'item':[{'item_id':501}],'has_next_page':False}}
        if path.endswith('get_item_base_info'):
            return {'response':{'item_list':[{'item_id':501,'item_sku':'0975','item_name':'Owned product',
                    'has_model':True,'item_status':current['status']}]}}
        if path.endswith('get_model_list'):
            rows=[{'model_id':1,'model_sku':'0975'}]
            if mixed:rows.append({'model_id':2,'model_sku':'0999'})
            return {'response':{'model':rows}}
        raise AssertionError(path)
    def post(path,shop_id,token,body,**kwargs):
        assert path=='/api/v2/product/unlist_item' and shop_id==101
        assert body=={'item_list':[{'item_id':501,'unlist':True}]}
        if before_write:before_write()
        calls.append(('WRITE',path))
        if unknown:raise TimeoutError('owned lost response')
        current['status']='UNLIST';return {'response':{}}
    monkeypatch.setattr(client,'shop_get',get)
    monkeypatch.setattr(sync,'shop_get',get)
    monkeypatch.setattr(client,'shop_post',post)
    monkeypatch.setattr(auth,'refresh_token',lambda *a,**k:pytest.fail('refresh is not authorized'))
    monkeypatch.setattr(shops,'refresh_shop_regions',lambda **k:pytest.fail('registry discovery is not authorized'))
    return calls


def _wait(engine,tid,states,timeout=35):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        task=engine.get(tid)
        if task['execution_state'] in states:return task
        time.sleep(.05)
    pytest.fail(str(engine.get(tid)))


@pytest.mark.parametrize('outcome',['complete','unknown','mixed'])
def test_real_new_post_worker_uses_original_exact_plan_execute_and_receipt_without_history_replay(tmp_path,monkeypatch,outcome):
    runtime,tokens=_runtime(tmp_path,monkeypatch)
    engine=runtime.engine;old=engine.create(_payload('old-no-grant'))
    assert engine.explicit_new_post_delisting_task_ids()==()
    old_events=copy.deepcopy(engine.store.events(old['task_id']))
    calls=_transport(monkeypatch,unknown=outcome=='unknown',mixed=outcome=='mixed')
    worker=install_explicit_new_task_preparation(runtime)
    handler=BoundedThreadingHTTPServer(('127.0.0.1',0),server.Handler);handler.operations_runtime=runtime
    thread=threading.Thread(target=handler.serve_forever,daemon=True);thread.start()
    try:
        code,response=_post(handler,'/api/orbit/tasks',_payload())
        assert code==201
        tid=response['task']['task_id'];assert tid!=old['task_id']
        current=_wait(engine,tid,{'completed','reconciliation_required','failed'})
        old_current=engine.get(old['task_id'])
        assert old_current['execution_state']=='queued' and old_current['worker'] is None
        assert old_current['steps'][0]['state']=='pending'
        assert engine.store.events(old['task_id'])==old_events
        assert current['scope']==engine._scope('delisting',_payload()['scope'])
        writes=[x for x in calls if x[0]=='WRITE']
        if outcome=='complete':
            assert current['execution_state']=='completed'
            assert [step['state'] for step in current['steps']]==['completed']*3
            assert len(writes)==1
            assert current['result_url'].endswith('/delisting-receipt')
        elif outcome=='unknown':
            assert current['execution_state']=='reconciliation_required' and len(writes)==1
            assert current['current_step']=='delist'
            assert len([e for e in engine.store.events(tid) if e['event_type']=='external_action_started'])==1
        else:
            assert current['execution_state']=='failed' and writes==[]
            assert current['current_step']=='identify'
        before=copy.deepcopy(engine.store.events(tid));count=len(calls)
        duplicate=_post(handler,'/api/orbit/tasks',_payload())
        assert duplicate[0]==201 and duplicate[1]['task']['task_id']==tid
        worker.wake();time.sleep(.2)
        assert engine.store.events(tid)==before and len(calls)==count
        assert len([e for e in before if e['event_type']=='explicit_new_post_delisting'])==1
        if outcome=='complete':
            worker.close();runtime.worker.close()
            handler.shutdown();handler.server_close();thread.join(5)
            assert not worker.thread.is_alive() and not thread.is_alive()
            assert all(future.done() for future in worker.inflight.values())
            assert handler.socket.fileno()==-1
            from shared_platform.workbench_delisting_receipt import build_receipt
            receipt=build_receipt(engine.read_delisting_receipt_inputs(tid),runtime.profile)
            assert receipt['summary']['confirmed']==1 and receipt['summary']['status']=='COMPLETE_VERIFIED_RECEIPT'
        if outcome=='unknown':
            worker.close();runtime.new_task_worker=None
            replacement=install_explicit_new_task_preparation(runtime)
            replacement.wake();time.sleep(.2)
            assert len(calls)==count and engine.get(tid)['execution_state']=='reconciliation_required'
            assert replacement.status()['scope']=='EXPLICIT_NEW_POST_PUBLICATION_ONLY'
            assert replacement.status()['delisting_scope']==DELIST_SCOPE
    finally:
        runtime.new_task_worker.close();runtime.worker.close()
        handler.shutdown();handler.server_close();thread.join(5)


def test_missing_fixed_service_binding_blocks_new_task_before_any_provider_read(tmp_path,monkeypatch):
    runtime,_=_runtime(tmp_path,monkeypatch,configured=False)
    calls=_transport(monkeypatch)
    worker=install_explicit_new_task_preparation(runtime)
    try:
        task=worker.create(_payload())
        failed=_wait(runtime.engine,task['task_id'],{'failed'})
        events=[e for e in runtime.engine.store.events(task['task_id']) if e['event_type']=='execution_failed']
        assert events[0]['detail']['reason']=='NATIVE_DELIST_SERVICE_BINDING_REQUIRED'
        assert calls==[] and failed['current_step']=='identify'
        assert worker.status()['delisting_executor_connected'] is False
    finally:worker.close();runtime.worker.close()


@pytest.mark.parametrize('change',['scope','settings','lease','token'])
def test_original_identified_task_rechecks_scope_settings_lease_and_token_before_mutation(tmp_path,monkeypatch,change):
    now=[1000.0];runtime,tokens=_runtime(tmp_path,monkeypatch,clock=lambda:now[0])
    engine=runtime.engine;engine.register_executor('owned',['delisting'],engine.release)
    task,created=engine.create_for_explicit_post(_payload());assert created
    claim=engine.claim(task['task_id'],'owned',ttl=10);token=claim['lease_token']
    calls=_transport(monkeypatch)
    run=bindings(engine,runtime.profile,runtime.native_delisting_config)
    run(engine,engine.get(task['task_id']),token,runtime.profile)
    current=engine.get(task['task_id']);assert current['current_step']=='delist'
    original=copy.deepcopy(current)
    original_events=copy.deepcopy(engine.store.events(task['task_id']))
    if change=='scope':
        current=copy.deepcopy(current);current['scope']['skus']=['0999']
        code='NATIVE_DELIST_TASK_SCOPE_CHANGED'
    elif change=='settings':
        runtime.native_delisting_config.settings_path.write_text('{}',encoding='utf-8')
        code='NATIVE_DELIST_FIXED_BINDING_CHANGED'
    elif change=='lease':now[0]+=11;code='stale or invalid lease'
    else:
        value=json.loads(tokens.read_bytes());value['shops']['101']['expire_at']=0
        tokens.write_text(json.dumps(value),encoding='utf-8');code='NATIVE_DELIST_ORIGINAL_SOURCE_CHANGED'
    with pytest.raises(ValueError,match=code):run(engine,current,token,runtime.profile)
    assert not [x for x in calls if x[0]=='WRITE']
    after=engine.get(task['task_id'])
    if change=='lease':
        assert after['steps'][1]['state']=='pending'
    else:
        assert after['execution_state']=='running' and after['steps'][1]['state']=='running'
        assert after['current_step']==original['current_step']=='delist'
        for field in ('task_id','version','scope','checkpoint','worker','last_executor','last_claimed_at'):
            assert after[field]==original[field]
        assert engine.store.events(task['task_id'])==original_events
        with engine.transaction() as connection:
            held=engine._lease(connection,task['task_id'],token)
            assert held['task_id']==task['task_id'] and held['worker']==original['worker']=='owned'


def test_internal_shopee_page_boundary_rechecks_real_lease_before_second_provider_read(tmp_path,monkeypatch):
    now=[1000.0];runtime,_=_runtime(tmp_path,monkeypatch,clock=lambda:now[0])
    engine=runtime.engine;engine.register_executor('owned',['delisting'],engine.release)
    task,_=engine.create_for_explicit_post(_payload());claim=engine.claim(task['task_id'],'owned',ttl=10)
    from modules.shopee import sync
    calls=[]
    def page(path,*args,**kwargs):
        calls.append(path);now[0]+=11
        return {'response':{'item':[{'item_id':501}],'has_next_page':True,'next_offset':100}}
    monkeypatch.setattr(sync,'shop_get',page)
    run=bindings(engine,runtime.profile,runtime.native_delisting_config)
    with pytest.raises(ValueError,match='stale or invalid lease'):
        run(engine,engine.get(task['task_id']),claim['lease_token'],runtime.profile)
    assert calls==['/api/v2/product/get_item_list']


def test_old_task_cannot_acquire_new_delist_source_or_provider_authority(tmp_path,monkeypatch):
    runtime,_=_runtime(tmp_path,monkeypatch)
    engine=runtime.engine;engine.register_executor('owned',['delisting'],engine.release)
    task=engine.create(_payload('old'))
    claim=engine.claim(task['task_id'],'owned',ttl=10)
    calls=_transport(monkeypatch)
    with pytest.raises(ValueError,match='NATIVE_DELIST_NEW_POST_GRANT_REQUIRED'):
        bindings(engine,runtime.profile,runtime.native_delisting_config)(engine,engine.get(task['task_id']),claim['lease_token'],runtime.profile)
    assert calls==[]
    assert not [e for e in engine.store.events(task['task_id']) if e['event_type']=='native_delisting_source_bound']


def test_bound_tiktok_transport_never_refreshes_and_rechecks_lease_at_each_call(tmp_path,monkeypatch):
    now=[1000.0];runtime,_=_runtime(tmp_path,monkeypatch,clock=lambda:now[0],platform='tiktok')
    engine=runtime.engine;engine.register_executor('owned',['delisting'],engine.release)
    payload=_payload();payload['scope']['shops']=['tiktok:LH_PH']
    task,_=engine.create_for_explicit_post(payload);claim=engine.claim(task['task_id'],'owned',ttl=10)
    from shared_platform.native_delisting_preparation import _Calls
    from core import api_client,auth
    calls=[]
    class Response:
        status=200
        headers={}
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self):return b'{"code":0,"data":{}}'
    def wire(request,**kwargs):
        assert kwargs['attempts']==1 and kwargs['allow_curl_fallback'] is False
        assert request.get_header('X-tts-access-token')=='owned-token'
        calls.append((request.get_method(),api_client.urllib.parse.urlparse(request.full_url).path))
        return Response()
    monkeypatch.setattr(api_client,'urlopen_retry',wire)
    monkeypatch.setattr(auth,'access_token',lambda:pytest.fail('native calls must not refresh'))
    monkeypatch.setattr(auth,'refresh_access_token',lambda **kw:pytest.fail('native calls must not refresh'))
    bound=_Calls(runtime.native_delisting_config,engine,engine.get(task['task_id']),claim['lease_token'],runtime.profile)
    bound.tiktok_detail(bound.tiktok_token(),'owned-cipher','501')
    assert calls==[('GET','/product/202309/products/501')]
    now[0]+=11
    with pytest.raises(ValueError,match='stale or invalid lease'):
        bound.tiktok_request('POST','/product/202309/products/deactivate','owned-token',body={'product_ids':['501']})
    assert len(calls)==1


@pytest.mark.parametrize('outcome',['get-rate-limit','get-transport-unknown','post-transport-unknown','get-expired-credential'])
def test_tiktok_original_request_loop_has_no_second_wire_or_refresh_after_native_result(tmp_path,monkeypatch,outcome):
    import io
    from urllib.error import HTTPError,URLError
    now=[1000.0];runtime,_=_runtime(tmp_path,monkeypatch,clock=lambda:now[0],platform='tiktok')
    engine=runtime.engine;engine.register_executor('owned',['delisting'],engine.release)
    payload=_payload();payload['scope']['shops']=['tiktok:LH_PH']
    task,_=engine.create_for_explicit_post(payload);claim=engine.claim(task['task_id'],'owned',ttl=10)
    from shared_platform.native_delisting_preparation import _Calls
    from core import api_client,auth
    bound=_Calls(runtime.native_delisting_config,engine,engine.get(task['task_id']),claim['lease_token'],runtime.profile)
    calls=[]
    class Response:
        status=200
        headers={}
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self):return b'{"code":105002}'
    def wire(request,**kwargs):
        assert kwargs['attempts']==1 and kwargs['allow_curl_fallback'] is False
        calls.append(request.get_method());now[0]+=11
        if outcome=='get-rate-limit':
            raise HTTPError(request.full_url,429,'owned rate limit',{},io.BytesIO(b'{"code":36009002}'))
        if outcome.endswith('transport-unknown'):
            raise URLError(TimeoutError('owned lost response'))
        return Response()
    monkeypatch.setattr(api_client,'urlopen_retry',wire)
    monkeypatch.setattr(api_client.time,'sleep',lambda seconds:pytest.fail('native request must return without retry sleep'))
    monkeypatch.setattr(auth,'refresh_access_token',lambda **kw:pytest.fail('refresh is not authorized'))
    monkeypatch.setattr(auth,'access_token',lambda:pytest.fail('refresh is not authorized'))
    method='POST' if outcome.startswith('post-') else 'GET'
    body={'product_ids':['501']} if method=='POST' else None
    path='/product/202309/products/deactivate' if method=='POST' else '/authorization/202309/shops'
    if outcome=='get-expired-credential':
        result=bound.tiktok_request(method,path,'owned-token',body=body)
        assert result['error_kind']=='credentials_expired' and result['code']==105002
    else:
        kind='rate_limited' if outcome=='get-rate-limit' else 'transport_outcome_unknown'
        with pytest.raises(api_client.APIRequestError,match=kind):
            bound.tiktok_request(method,path,'owned-token',body=body)
    assert calls==[method]
    with pytest.raises(ValueError,match='stale or invalid lease'):bound.check()
    current=engine.get(task['task_id'])
    assert current['execution_state']=='queued' and current['steps'][0]['state']=='pending'
    assert not [e for e in engine.store.events(task['task_id']) if e['event_type']=='external_action_started']


def test_tiktok_original_signed_wire_rejects_actual_lease_expiry_before_dispatch(tmp_path,monkeypatch):
    now=[1000.0];runtime,_=_runtime(tmp_path,monkeypatch,clock=lambda:now[0],platform='tiktok')
    engine=runtime.engine;engine.register_executor('owned',['delisting'],engine.release)
    payload=_payload();payload['scope']['shops']=['tiktok:LH_PH']
    task,_=engine.create_for_explicit_post(payload);claim=engine.claim(task['task_id'],'owned',ttl=10)
    from shared_platform.native_delisting_preparation import _Calls
    from core import api_client,auth
    bound=_Calls(runtime.native_delisting_config,engine,engine.get(task['task_id']),claim['lease_token'],runtime.profile)
    original_sign=api_client.sign;signatures=[];wires=[]
    def expire_after_sign(*args,**kwargs):
        value=original_sign(*args,**kwargs);signatures.append(value);now[0]+=11;return value
    monkeypatch.setattr(api_client,'sign',expire_after_sign)
    monkeypatch.setattr(api_client,'urlopen_retry',lambda *a,**kw:wires.append(a) or pytest.fail('lost lease must not dispatch'))
    monkeypatch.setattr(auth,'refresh_access_token',lambda **kw:pytest.fail('refresh is not authorized'))
    with pytest.raises(ValueError,match='stale or invalid lease'):
        bound.tiktok_request('GET','/authorization/202309/shops','owned-token')
    assert len(signatures)==1 and wires==[]


def test_ozon_original_configured_junction_cannot_be_resolved_into_a_trusted_source(tmp_path,monkeypatch):
    import _winapi
    runtime,_=_runtime(tmp_path,monkeypatch,platform='ozon',configured=False)
    root=runtime.profile.root;directory=root/'owned-ozon-catalog';alias=root/'owned-ozon-alias'
    _winapi.CreateJunction(str(directory),str(alias))
    try:
        path=root/'config/settings.json';settings=json.loads(path.read_bytes())
        settings['ozon']['data_dir']=str(alias);path.write_text(json.dumps(settings),encoding='utf-8')
        from core import config
        monkeypatch.setattr(config,'_cache',None);monkeypatch.setattr(config,'_cache_source',None)
        config.load_settings()
        runtime.native_delisting_config=NativeDelistingServiceConfig.capture(runtime.profile,root)
        engine=runtime.engine;engine.register_executor('owned',['delisting'],engine.release)
        payload=_payload();payload['scope']['shops']=['ozon:RU']
        task,_=engine.create_for_explicit_post(payload);claim=engine.claim(task['task_id'],'owned',ttl=10)
        from shared_platform.native_delisting_preparation import _Calls
        from modules.ozon import client
        monkeypatch.setattr(client,'ozon_post',lambda *a,**kw:pytest.fail('untrusted catalog path must not dispatch'))
        with pytest.raises(ValueError,match='NATIVE_DELIST_FIXED_ROOT_INVALID'):
            _Calls(runtime.native_delisting_config,engine,engine.get(task['task_id']),claim['lease_token'],runtime.profile)
        assert not [e for e in engine.store.events(task['task_id']) if e['event_type']=='native_delisting_source_bound']
        assert directory.is_dir()
    finally:
        alias.rmdir()


def test_original_ozon_archive_and_readback_use_bound_catalog_and_guard(tmp_path,monkeypatch):
    runtime,_=_runtime(tmp_path,monkeypatch,platform='ozon')
    engine=runtime.engine;engine.register_executor('owned',['delisting'],engine.release)
    payload=_payload();payload['scope']['shops']=['ozon:RU']
    task,_=engine.create_for_explicit_post(payload);claim=engine.claim(task['task_id'],'owned',ttl=10)
    from shared_platform.native_delisting_preparation import _Calls,_Skill
    from shared_platform.workbench_delisting_adapter import _module
    from modules.ozon import client
    # No ambient credential owner is installed by this owned fixture.
    calls=[]
    def post(path,body,**kwargs):
        calls.append((path,body))
        if path=='/v1/product/archive':return {'result':True}
        assert path=='/v3/product/info/list' and body=={'offer_id':['0975']}
        return {'items':[{'id':501,'offer_id':'0975','is_archived':True}]}
    monkeypatch.setattr(client,'ozon_post',post)
    bound=_Calls(runtime.native_delisting_config,engine,engine.get(task['task_id']),claim['lease_token'],runtime.profile)
    original=_Skill(_module(runtime.profile.root),bound)
    row={'status':'READY','executable':True,'platform':'ozon','target_label':'ozon:RU',
         'product_id':'501','offer_id':'0975','requested_skus':['0975'],'all_product_skus':['0975'],'current_status':'ACTIVE'}
    result=original.original._execute_one(row,call_guard=bound.check,tiktok_runtime=bound)
    assert result['verified'] is True and result['external_write_count']==1
    assert [path for path,_ in calls]==['/v1/product/archive','/v3/product/info/list']
    (Path(bound.data_sources['ozon']['directory'])/'all_products_attrs.json').write_text('[{}]',encoding='utf-8')
    with pytest.raises(ValueError,match='NATIVE_DELIST_OZON_CATALOG_SOURCE_CHANGED'):bound.check()
    assert len(calls)==2


def test_unavailable_tiktok_store_cannot_borrow_other_account_or_refresh(tmp_path,monkeypatch):
    runtime,_=_runtime(tmp_path,monkeypatch,platform='tiktok')
    engine=runtime.engine;engine.register_executor('owned',['delisting'],engine.release)
    payload=_payload();payload['scope']['shops']=['tiktok:HB_PH']
    task,_=engine.create_for_explicit_post(payload);claim=engine.claim(task['task_id'],'owned',ttl=10)
    from core import api_client,auth
    monkeypatch.setattr(api_client,'request',lambda *a,**k:pytest.fail('unavailable target must not read or write'))
    monkeypatch.setattr(auth,'access_token',lambda:pytest.fail('refresh is not authorized'))
    with pytest.raises(ValueError,match='NATIVE_DELIST_OFFICIAL_TARGET_UNAVAILABLE'):
        bindings(engine,runtime.profile,runtime.native_delisting_config)(engine,engine.get(task['task_id']),claim['lease_token'],runtime.profile)
    assert not [e for e in engine.store.events(task['task_id']) if e['event_type']=='external_action_started']


def test_shopee_original_wire_boundary_is_one_attempt_and_rejects_lease_loss_after_signing(tmp_path,monkeypatch):
    now=[1000.0];runtime,_=_runtime(tmp_path,monkeypatch,clock=lambda:now[0])
    engine=runtime.engine;engine.register_executor('owned',['delisting'],engine.release)
    task,_=engine.create_for_explicit_post(_payload());claim=engine.claim(task['task_id'],'owned',ttl=10)
    from shared_platform.native_delisting_preparation import _Calls
    from modules.shopee import client
    bound=_Calls(runtime.native_delisting_config,engine,engine.get(task['task_id']),claim['lease_token'],runtime.profile)
    calls=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self):return b'{"response":{}}'
    def wire(request,**kwargs):
        assert kwargs['attempts']==1 and kwargs['allow_curl_fallback'] is False
        calls.append(request.get_method());return Response()
    monkeypatch.setattr(client,'urlopen_retry',wire)
    client.shop_get('/api/v2/product/get_item_list',101,'owned-token',call_guard=bound.check)
    assert calls==['GET']
    actual_sign=client.sign_shop
    def sign_then_expire(*args,**kwargs):
        result=actual_sign(*args,**kwargs);now[0]+=11;return result
    monkeypatch.setattr(client,'sign_shop',sign_then_expire)
    with pytest.raises(ValueError,match='stale or invalid lease'):
        client.shop_post('/api/v2/product/unlist_item',101,'owned-token',
                         {'item_list':[{'item_id':501,'unlist':True}]},call_guard=bound.check)
    assert calls==['GET']
