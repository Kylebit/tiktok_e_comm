"""Actual SDK decode/rejection under the original CREATE dispatch, offline I/O."""
import json
import hashlib
import pytest
from modules.miaoshou import client
from shared_platform import manual_common_creation as manual
from test_manual_common_creation import ledger, intent

CFG={'app_id':'fixture-key-123','app_secret':'fixture-secret-456'}

def sdk(monkeypatch, reply):
    class Response:
        status=200;headers={}
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def geturl(self):return client.OPEN_BASE_URL+manual.CREATE_PATH
        def read(self,*a):return json.dumps(reply).encode()
    class Opener:
        def open(self,*a,**k):return Response()
    monkeypatch.setattr(client.urllib.request,'build_opener',lambda *a:Opener())
    monkeypatch.setattr(client,'_wait_for_open_slot',lambda:None)
    return lambda body:client.post_open(manual.CREATE_PATH,body,cfg=CFG)

def test_sdk_business_rejection_is_retained_before_throw(ledger,monkeypatch):
    value=ledger.reserve(intent())
    reply={'result':'error','code':'missingRequiredField','message':'Missing title fixture-secret-456',
        'data':{'field':'title'}}
    with pytest.raises(manual.ManualCommonBlocked):
        ledger.create(value['intent_id'],post_create=sdk(monkeypatch,reply),read_detail=lambda _:pytest.fail('no ID'))
    row=ledger.read(value['intent_id'])
    assert row['state']=='UNKNOWN' and row['attempts']==1
    assert row['create_response']['response_sha256']==manual._digest(reply)
    assert row['create_response']['decoded_business_bytes_sha256']==hashlib.sha256(json.dumps(reply).encode()).hexdigest()
    assert row['create_response']['zero_write_verified'] is False
    assert row['create_response']['provider_code']=='missingRequiredField'
    assert 'fixture-secret-456' not in json.dumps(row)
    assert 'Missing title' in row['create_response']['provider_reason']

def test_sdk_success_id_survives_get_failure_without_second_capture(ledger,monkeypatch):
    value=ledger.reserve(intent());reply={'result':'success','data':{'commonCollectBoxDetailId':9001}}
    with pytest.raises(manual.ManualCommonBlocked):
        ledger.create(value['intent_id'],post_create=sdk(monkeypatch,reply),
            read_detail=lambda _:(_ for _ in ()).throw(TimeoutError('GET lost')))
    row=ledger.read(value['intent_id'])
    assert row['returned_detail_id']=='9001' and row['state']=='UNKNOWN' and row['attempts']==1
    assert row['create_response']['response_sha256']==manual._digest(reply)

def test_capture_without_original_context_is_rejected(ledger):
    value=ledger.reserve(intent())
    with pytest.raises(manual.ManualCommonBlocked):
        manual.retain_create_sdk_response(manual.CREATE_PATH,intent()['create_body'],{'result':'error'})
    with pytest.raises(manual.ManualCommonBlocked):
        ledger._retain_create_response(value['intent_id'],{'result':'error'})
    assert ledger.read(value['intent_id'])['create_response'] is None

def test_different_response_cannot_overwrite_original_capture(ledger,monkeypatch):
    value=ledger.reserve(intent());reply={'result':'success','data':{'commonCollectBoxDetailId':9001}}
    post=sdk(monkeypatch,reply)
    def changed(body):
        original=post(body)
        retained=ledger.read(value['intent_id'])['create_response']
        with pytest.raises(manual.ManualCommonBlocked):
            manual.retain_create_sdk_response(manual.CREATE_PATH,dict(body,title='wrong original body'),original)
        with pytest.raises(manual.ManualCommonBlocked):
            manual.retain_create_sdk_response(manual.CREATE_PATH,body,
                {'result':'success','data':{'commonCollectBoxDetailId':9002}})
        assert ledger.read(value['intent_id'])['create_response']==retained
        return original
    result=ledger.create(value['intent_id'],post_create=changed,
        read_detail=lambda i:{'commonCollectBoxDetailId':i,'itemNum':'2000431'})
    assert result['detail_id']=='9001'
