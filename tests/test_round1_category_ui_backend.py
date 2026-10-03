import io,json
from types import SimpleNamespace
from urllib.parse import urlencode
from copy import deepcopy
import pytest
from modules.products import server
from modules.shopee import oneclick_release as shopee
from shared_platform import release_store
from test_round1_category_observations import context, capture
COUNTS={}

def get(action,data):
    raw=(f'GET /api/product-workspace/round1-category/{action}?{urlencode(data)} HTTP/1.1\r\nHost: 127.0.0.1:8765\r\nConnection: close\r\n\r\n').encode()
    class Connection:
        def __init__(self):self.output=io.BytesIO()
        def makefile(self,*a,**k):return io.BytesIO(raw)
        def sendall(self,value):self.output.write(value)
    connection=Connection();server.Handler(connection,('127.0.0.1',40001),SimpleNamespace(server_address=('127.0.0.1',8765)))
    head,body=connection.output.getvalue().split(b'\r\n\r\n',1)
    return int(head.split(b' ')[1]),json.loads(body)

def query(data):
    return dict(offer_id=data['offer_id'],product_center_revision=data['product_center_revision'],
                requested_targets=json.dumps(data['requested_targets']),source_region=data['source_region'])

def prepared(tmp_path,monkeypatch):
    data,preview,fake,store=context(tmp_path,monkeypatch)
    metadata_reads=[]
    credentials=shopee._prepare_transport_factory('MY').credentials
    def local(region):metadata_reads.append(region);return credentials
    monkeypatch.setattr(shopee,'_current_credentials',local)
    status,result=get('context',query(data));assert status==200,result
    body=dict(data,schema_version='round1-category-capture-request/v2',request_id='request1',context_digest=result['context_digest'])
    return body,preview,fake,store,metadata_reads

def post(body):
    from test_round1_category_observer_http import request
    return request('capture',body)

def test_context_route_is_local_and_exposes_intent_required(tmp_path,monkeypatch):
    data,_,fake,_=context(tmp_path,monkeypatch)
    monkeypatch.setattr(shopee,'_current_credentials',lambda _:fake.transport().credentials)
    status,result=get('context',query(data))
    assert status==200
    assert result['category_intent']['status']=='INTENT_REQUIRED'
    assert fake.calls==[]

def test_correlated_capture_before_get_atomic_result_and_repeat(tmp_path,monkeypatch):
    body,_,fake,store,reads=prepared(tmp_path,monkeypatch)
    original=fake.merchant_get
    def get_official(path,params):
        row=store.category_capture_request(body['request_id'],body['offer_id'],server._ROUND1_CATEGORY_INSTANCE)
        assert row['status']=='IN_PROGRESS' and row['observer_reference'] is None
        return original(path,params)
    fake.merchant_get=get_official
    status,result=post(body);assert status==200 and result['status']=='SUCCEEDED',result
    count=len(fake.calls);meta=len(reads)
    assert count==2
    assert post(body)==(200,result)
    assert len(fake.calls)==count and len(reads)==meta
    status,readback=get('capture-status',dict(offer_id=body['offer_id'],request_id=body['request_id']))
    assert status==200 and readback==result
    assert store.round1_category_observation(result['observer_reference']) is not None
    COUNTS['correlated_capture']={'official_gets':len(fake.calls),'metadata_reads':len(reads),'repeat_additional_gets':len(fake.calls)-count,'status_additional_gets':0}

def test_same_id_changed_input_conflicts_without_get(tmp_path,monkeypatch):
    body,_,fake,_,_=prepared(tmp_path,monkeypatch)
    assert post(body)[1]['status']=='SUCCEEDED'
    changed=dict(body,category_id=202)
    assert post(changed)[0]==409 and len(fake.calls)==2

def test_restart_in_progress_is_unknown_and_never_replayed(tmp_path,monkeypatch):
    body,_,fake,store,_=prepared(tmp_path,monkeypatch)
    assert store.begin_category_capture(body,'previous-process')
    restarted=release_store.ReleaseStore(store.path)
    monkeypatch.setattr(release_store,'default_release_store',lambda:restarted)
    assert post(body)[1]['status']=='UNKNOWN'
    assert get('capture-status',dict(offer_id=body['offer_id'],request_id=body['request_id']))[1]['status']=='UNKNOWN'
    assert fake.calls==[]
    COUNTS['restart_unknown']={'official_gets':len(fake.calls)}

def test_history_does_not_make_account_ready(tmp_path,monkeypatch):
    data,_,fake,_=context(tmp_path,monkeypatch);capture(data)
    def absent(_):raise shopee.ShopeeOneClickPreDispatchError('synthetic missing')
    monkeypatch.setattr(shopee,'_current_credentials',absent)
    status,result=get('context',query(data))
    assert status==200 and result['source_account']['readiness']=='UNPREPARED'
    assert result['observations'][0]['status']=='REUSABLE'
    assert len(fake.calls)==2

def test_stale_context_fails_and_retry_does_not_get(tmp_path,monkeypatch):
    body,preview,fake,_,_=prepared(tmp_path,monkeypatch)
    preview['review']['title']='changed'
    status,result=post(body);assert status==200 and result['status']=='FAILED'
    assert post(body)==(200,result) and fake.calls==[]

def test_uncertain_failure_remains_unknown(tmp_path,monkeypatch):
    body,_,fake,_,_=prepared(tmp_path,monkeypatch)
    from modules.shopee import global_plan_candidate as channel
    monkeypatch.setattr(channel,'capture_round1_category',lambda *a,**k:(_ for _ in ()).throw(TimeoutError()))
    assert post(body)[1]['status']=='UNKNOWN'
    assert post(body)[1]['status']=='UNKNOWN' and fake.calls==[]

def test_no_implicit_region_or_raw_registration(tmp_path,monkeypatch):
    body,_,fake,_,_=prepared(tmp_path,monkeypatch)
    for changed in [dict(body,source_region=None),dict(body,observation={}),dict(body,root='foreign')]:
        assert post(changed)[0]==400
    q=query(body);q.pop('source_region')
    assert get('context',q)[0]==400 and fake.calls==[]

def test_atomic_rollback_cannot_leave_unlinked_observation(tmp_path,monkeypatch):
    body,_,fake,store,_=prepared(tmp_path,monkeypatch)
    store.begin_category_capture(body,server._ROUND1_CATEGORY_INSTANCE)
    import sqlite3
    with sqlite3.connect(store.path) as connection:
        connection.execute("CREATE TRIGGER reject_result BEFORE UPDATE ON round1_category_capture_requests BEGIN SELECT RAISE(ABORT, 'fixture rollback'); END")
    from modules.shopee.global_plan_candidate import capture_round1_category
    review=server._round1_category_review(body)
    observed=capture_round1_category(review,source_region='MY',category_id=101,
                                    selected_attributes=body['selected_attributes'],account_identity_digest=body['account_identity_digest'])
    with pytest.raises(sqlite3.IntegrityError):
        store.finish_category_capture(body['request_id'],server._ROUND1_CATEGORY_INSTANCE,observation=observed)
    assert store.round1_category_observation(observed['observer_reference']) is None
    assert store.category_capture_request(body['request_id'],body['offer_id'],server._ROUND1_CATEGORY_INSTANCE)['status']=='IN_PROGRESS'

def test_concurrent_same_request_has_one_capture(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    body,_,fake,_,_=prepared(tmp_path,monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:server._round1_category_request('capture',deepcopy(body)),range(2)))
    assert all(status==200 for status,_ in results)
    assert all(result['status'] in {'IN_PROGRESS','SUCCEEDED'} for _,result in results)
    assert len(fake.calls)==2

def test_readonly_context_does_not_create_database(tmp_path,monkeypatch):
    body,_,fake,store,_=prepared(tmp_path,monkeypatch)
    assert not store.path.exists()
    for _ in range(2):assert get('context',query(body))[0]==200
    assert not store.path.exists() and fake.calls==[]
    COUNTS['context_only']={'official_gets':len(fake.calls),'database_created':store.path.exists()}

def test_metadata_unknown_does_not_expose_credentials(tmp_path,monkeypatch):
    body,_,_,_,_=prepared(tmp_path,monkeypatch)
    def broken(_):raise RuntimeError('synthetic private detail')
    monkeypatch.setattr(shopee,'_current_credentials',broken)
    _,result=get('context',query(body))
    assert result['source_account']['readiness']=='UNKNOWN'
    encoded=json.dumps(result)
    assert 'private detail' not in encoded and 'token' not in encoded

def test_index_exact_targets_revision_and_invalidation(tmp_path,monkeypatch):
    body,preview,_,store,_=prepared(tmp_path,monkeypatch)
    result=post(body)[1]
    assert len(get('context',query(body))[1]['observations'])==1
    changed=deepcopy(body);changed['requested_targets'].reverse()
    assert get('context',query(changed))[1]['observations']==[]
    store.invalidate_round1_category_observation(result['observer_reference'],'SOURCE_REVOKED')
    assert get('context',query(body))[1]['observations'][0]['status']=='INVALIDATED'

def test_actual_loopback_handler_v2(tmp_path,monkeypatch):
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.request import Request,build_opener,ProxyHandler
    body,_,fake,_,_=prepared(tmp_path,monkeypatch)
    http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
    thread=Thread(target=http.serve_forever,daemon=True);thread.start()
    try:
        req=Request(f'http://127.0.0.1:{http.server_port}/api/product-workspace/round1-category/capture',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
        with build_opener(ProxyHandler({})).open(req,timeout=5) as response:
            assert json.loads(response.read())['status']=='SUCCEEDED'
        assert len(fake.calls)==2
    finally:
        http.shutdown();http.server_close();thread.join(timeout=5)

def test_nonfinite_intent_rejected_without_request_or_provider(tmp_path,monkeypatch):
    body,_,fake,store,_=prepared(tmp_path,monkeypatch)
    body['selected_attributes']=[float('nan')]
    assert post(body)[0]==400 and not store.path.exists() and fake.calls==[]
