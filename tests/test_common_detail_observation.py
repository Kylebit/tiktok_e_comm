"""Closed response fixtures; no network, installed reader or provider budget."""
from copy import deepcopy
import base64
import gzip
import hashlib
import json
import sqlite3
import io
from email.message import Message
from types import SimpleNamespace
import urllib.response
import pytest

from modules.miaoshou import client
from modules.products import release_adapters, server
from shared_platform.release_store import ReleaseStore, ImmutableReleaseError
from test_b4b_common_stage import context
from test_release_store import _plan

CONFIG={'app_id':'fixture-native-app-scope-20260930','app_secret':'fixture-observation-secret-never-persist'}


class LocalResponse:
    def __init__(self, body, encoding='identity', url=None, status=200):
        self.body=body; self.status=status; self.headers={'Content-Encoding':encoding}
        self.url=url or client.OPEN_BASE_URL+client.COMMON_DETAIL_OBSERVATION_PATH
    def geturl(self):return self.url
    def read(self, size=-1):return self.body if size<0 else self.body[:size]
    def __enter__(self):return self
    def __exit__(self,*args):return False


def install_response(monkeypatch, response):
    calls=[]
    def closed(request, timeout):
        assert request.full_url==client.OPEN_BASE_URL+client.COMMON_DETAIL_OBSERVATION_PATH
        assert request.get_method()=='POST' and timeout==30
        assert request.get_header('X-app-key')==CONFIG['app_id']
        calls.append(json.loads(request.data))
        return response
    monkeypatch.setattr(client.urllib.request,'urlopen',closed)
    monkeypatch.setattr(client.urllib.request,'build_opener',lambda *handlers:SimpleNamespace(open=closed))
    monkeypatch.setattr(client,'_wait_for_open_slot',lambda:None)
    return calls


def full_response_fixture(tmp_path, monkeypatch, compressed=False):
    _, _, _, request=context(tmp_path,monkeypatch)
    status, preview=server._preview_r3_common_stage(request)
    assert status==200
    payload=preview['common']['plan']['payload']
    detail=release_adapters._immutable_miaoshou_common_draft(payload)
    # The edit draft has selectedSkuKeys but is not an official-shaped detail:
    # the existing readback requires skuMap with each frozen number/logistics.
    # Supply those closed response fixture fields rather than relax its checks.
    facts=payload['product_facts']
    commercial={key.strip(';'):value for key,value in facts['sku_commercial_facts'].items()}
    assignment={row['variant_key'].strip(';'):row['model_sku'] for row in payload['sku_lineage']['assignment']['model_skus']}
    selected={row['key'].strip(';'):row for row in facts['selected_skus']}
    detail['skuMap']={key:{'itemNum':assignment[key],
        'specLabel':facts['sku_label_overrides'].get(key) or selected[key]['label'],
        'weight':float(commercial[key]['weight_kg']),
        **dict(zip(('packageLength','packageWidth','packageHeight'),map(float,commercial[key]['package_cm'])))}
        for key in assignment}
    body=json.dumps({'result':'success','data':{'editCommonCollectBoxDetail':detail}},ensure_ascii=False,indent=2).encode('utf-8')
    wire=gzip.compress(body,mtime=0) if compressed else body
    (tmp_path/'closed-common-detail.business.raw.json').write_bytes(body)
    (tmp_path/'closed-common-detail.wire.bin').write_bytes(wire)
    calls=install_response(monkeypatch,LocalResponse(wire,'gzip' if compressed else 'identity'))
    observer=client.NativeCommonDetailObserver(CONFIG)
    return payload,body,wire,calls,observer


@pytest.mark.parametrize('compressed',[False,True])
def test_native_actual_adapter_preserves_response_bytes_and_comparison(tmp_path,monkeypatch,compressed):
    payload,body,wire,calls,observer=full_response_fixture(tmp_path,monkeypatch,compressed)
    result=release_adapters.readback_miaoshou_common(payload,observation_reader=observer)
    (tmp_path/'closed-common-comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    assert result['verified'] is True and result['external_writes_performed']==[],result['field_diffs']
    packet=result['native_common_observation']
    assert base64.b64decode(packet['wire_base64'])==wire
    assert base64.b64decode(packet['business_base64'])==body
    assert packet['business_sha256']==hashlib.sha256(body).hexdigest()
    assert packet['wire_sha256']==hashlib.sha256(wire).hexdigest()
    assert packet['execution_authority'] is False
    assert packet['credential_scope_digest']==hashlib.sha256(('miaoshou-app-scope:'+CONFIG['app_id']).encode()).hexdigest()
    encoded=json.dumps(result)
    assert CONFIG['app_id'] not in encoded and CONFIG['app_secret'] not in encoded and 'x-sign' not in encoded
    assert calls==[{'commonCollectBoxDetailId':int(payload['product_id'])}]


def test_legacy_dict_api_and_uninstalled_adapter_keep_original_contract(tmp_path,monkeypatch):
    payload,body,wire,calls,_=full_response_fixture(tmp_path,monkeypatch)
    original=client.post_open(client.COMMON_DETAIL_OBSERVATION_PATH,
        {'commonCollectBoxDetailId':int(payload['product_id'])},cfg=CONFIG)
    assert original==json.loads(body) and type(original) is dict
    result=release_adapters.readback_miaoshou_common(payload,post=lambda *args:original)
    (tmp_path/'closed-common-comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    assert result['verified'] is True and 'native_common_observation' not in result,result['field_diffs']
    assert len(calls)==1


@pytest.mark.parametrize('damage',['redirect','status','encoding','bad_utf8','credential_echo','credential_echo_key','oversize'])
def test_native_reader_transport_or_sensitive_bytes_fail_closed(monkeypatch,damage):
    body=b'{"result":"success","data":{"editCommonCollectBoxDetail":{}}}'
    kwargs={}
    if damage=='redirect':kwargs['url']='https://untrusted.invalid/collect'
    if damage=='status':kwargs['status']=201
    if damage=='encoding':kwargs['encoding']='br'
    if damage=='bad_utf8':body=b'\xffbad-json'
    if damage=='credential_echo':
        # Escaped JSON must not hide a credential echo from the byte check.
        escaped=''.join('\\u'+format(ord(c),'04x') for c in CONFIG['app_secret'])
        body=('{"result":"success","data":{"credentialEcho":"'+escaped+'"}}').encode()
    if damage=='credential_echo_key':
        escaped=''.join('\\u'+format(ord(c),'04x') for c in CONFIG['app_secret'])
        body=('{"result":"success","data":{"'+escaped+'":"not-a-credential-value"}}').encode()
    if damage=='oversize':body=b' '*(client.COMMON_OBSERVATION_MAX_BYTES+1)
    install_response(monkeypatch,LocalResponse(body,**kwargs))
    with pytest.raises(ValueError,match='^COMMON_OBSERVER_'):
        client.NativeCommonDetailObserver(CONFIG).observe('123')


def pending_store(tmp_path,payload):
    store=ReleaseStore(tmp_path/'observations-release.db')
    plan=store.create_plan(_plan(product_id=payload['product_id'],targets=['miaoshou:COMMON']))
    store.approve_plan(plan['plan_id'],approved_by='Kyle',user_approved=True,confirmation_token=plan['confirmation_token'])
    run=store.start_run(plan['plan_id']);store.begin_target(run['run_id'],'miaoshou:COMMON')
    return store,plan,run


def test_actual_release_store_persists_stable_lineage_and_identical_replay(tmp_path,monkeypatch):
    payload,body,wire,calls,observer=full_response_fixture(tmp_path,monkeypatch,True)
    evidence=release_adapters.readback_miaoshou_common(payload,observation_reader=observer)
    (tmp_path/'closed-common-comparison.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    store,plan,run=pending_store(tmp_path,payload)
    store.record_target_success(run['run_id'],'miaoshou:COMMON',external_id=payload['product_id'],readback_evidence=evidence)
    with sqlite3.connect(store.path) as db:
        before=db.execute('SELECT evidence_json,evidence_digest FROM release_target_readbacks').fetchone()
    retained=json.loads(before[0]);lineage=retained['stored_common_lineage']
    assert lineage=={'schema_version':'stored-common-observation-lineage/v1','plan_id':plan['plan_id'],
        'run_id':run['run_id'],'target_label':'miaoshou:COMMON','attempt':1,'offer_id':payload['product_id'],
        'plan_payload_digest':plan['payload_digest'],'comparison_sha256':evidence['native_common_observation']['comparison_sha256'],'execution_authority':False}
    assert base64.b64decode(retained['native_common_observation']['business_base64'])==body
    assert hashlib.sha256(before[0].encode()).hexdigest()==before[1]
    store.record_target_success(run['run_id'],'miaoshou:COMMON',external_id=payload['product_id'],readback_evidence=evidence)
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT evidence_json,evidence_digest FROM release_target_readbacks').fetchone()==before
        assert db.execute('SELECT attempts FROM release_target_runs').fetchone()[0]==1
    assert len(calls)==1


@pytest.mark.parametrize('damage',['wire','comparison','identity','stored_lineage'])
def test_changed_observation_rejects_before_existing_readback_write(tmp_path,monkeypatch,damage):
    payload,_,_,_,observer=full_response_fixture(tmp_path,monkeypatch)
    evidence=release_adapters.readback_miaoshou_common(payload,observation_reader=observer)
    store,plan,run=pending_store(tmp_path,payload)
    broken=deepcopy(evidence)
    if damage=='wire':broken['native_common_observation']['wire_base64']=base64.b64encode(b'changed').decode()
    if damage=='comparison':broken['image_count']+=1
    if damage=='identity':broken['native_common_observation']['detail_id']='wrong-offer'
    if damage=='stored_lineage':broken['stored_common_lineage']={'plan_id':'caller-plan'}
    with pytest.raises((ValueError,ImmutableReleaseError),match='COMMON_OBSERVATION_'):
        store.record_target_success(run['run_id'],'miaoshou:COMMON',external_id=payload['product_id'],readback_evidence=broken)
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT count(*) FROM release_target_readbacks').fetchone()[0]==0
        assert db.execute('SELECT status,attempts FROM release_target_runs').fetchone()==('RUNNING',1)


def test_caller_observer_and_post_substitutions_are_refused(tmp_path,monkeypatch):
    payload,_,_,calls,observer=full_response_fixture(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='COMMON_SERVICE_OBSERVATION_READER_REQUIRED'):
        release_adapters.readback_miaoshou_common(payload,observation_reader={'official':True})
    with pytest.raises(ValueError,match='COMMON_SERVICE_OBSERVATION_READER_REQUIRED'):
        release_adapters.readback_miaoshou_common(payload,observation_reader=observer,post=lambda *args:{})
    assert calls==[]


@pytest.mark.parametrize('code',[301,302,303,307,308])
def test_native_signed_common_redirect_handler_refuses_before_second_request(monkeypatch,code):
    """Exercise urllib's actual error/redirect handler chain without sockets.

    The former urlopen implementation follows 301/302/303 POST redirects and
    forwards custom signature headers. Final-response URL validation happens
    only after that request. Every native redirect now reaches the refusing
    handler instead; the only request observed below is the original endpoint.
    """
    requests=[]
    native_builder=client.urllib.request.build_opener
    class ClosedHttpsHandler(client.urllib.request.HTTPSHandler):
        def https_open(self,request):
            requests.append((request.full_url,dict(request.header_items())))
            if len(requests)>1:
                assert request.full_url=='https://foreign-common-source.invalid/collect'
                assert request.get_header('X-app-key')==CONFIG['app_id']
                assert request.get_header('X-sign')
                raise ValueError('COMMON_OBSERVER_REDIRECT_FORWARDED_SIGNED_HEADERS')
            headers=Message()
            headers['Location']='https://foreign-common-source.invalid/collect'
            response=urllib.response.addinfourl(io.BytesIO(b''),headers,request.full_url,code=code)
            response.msg='fixture redirect'
            return response
    def closed_builder(*handlers):
        return native_builder(client.urllib.request.ProxyHandler({}),ClosedHttpsHandler(),*handlers)
    monkeypatch.setattr(client.urllib.request,'build_opener',closed_builder)
    # The original implementation calls urlopen; route that through the same
    # actual urllib chain so the regression demonstrates forwarding, not an
    # invented final geturl() result or a missing method/import.
    monkeypatch.setattr(client.urllib.request,'urlopen',lambda request,timeout:closed_builder().open(request,timeout=timeout))
    monkeypatch.setattr(client,'_wait_for_open_slot',lambda:None)
    with pytest.raises(ValueError,match='^COMMON_OBSERVER_REDIRECT_REFUSED$'):
        client.NativeCommonDetailObserver(CONFIG).observe('123')
    assert len(requests)==1
    assert requests[0][0]==client.OPEN_BASE_URL+client.COMMON_DETAIL_OBSERVATION_PATH
    assert requests[0][1]['X-app-key']==CONFIG['app_id']
    assert requests[0][1]['X-sign']
