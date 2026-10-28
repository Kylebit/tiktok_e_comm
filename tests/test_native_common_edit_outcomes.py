"""Real signed client, owned transport: rejection survives SDK wrapping."""
import json
from hashlib import sha256
from types import SimpleNamespace
import urllib.error
import pytest
from modules.products import server
from modules.miaoshou import client
from shared_platform import native_common_edit_boundary as boundary
from test_round1_workspace_freeze import live
from test_native_common_technical_execution import _setup
from test_native_common_service_facts import _installed,_closed_signed_transport
from test_common_detail_observation import CONFIG,LocalResponse


@pytest.mark.parametrize('outcome',['business','http','network'])
def test_safe_edit_outcome_commits_before_raise_without_acceptance_or_retry(live,monkeypatch,tmp_path,outcome):
    store,plan,payload,_=_setup(live,monkeypatch,tmp_path)
    _installed(monkeypatch,tmp_path,store)
    calls=_closed_signed_transport(monkeypatch,store,payload)
    original=client.urllib.request.urlopen
    reply=dict(result='fail',code='VALIDATION',fieldPath='skuMap.price',
        message='price required "access_token":"unknown-never-retain" refresh-token=other-secret '+CONFIG['app_secret'],access_token='never-retain')
    wire=json.dumps(reply).encode()
    def closed(request,timeout):
        result=original(request,timeout)
        if request.full_url.endswith(boundary.EDIT_PATH):
            if outcome=='business':return LocalResponse(wire,url=request.full_url)
            if outcome=='network':raise urllib.error.URLError('token=never-retain')
            import io
            raise urllib.error.HTTPError(request.full_url,502,'bad',{},io.BytesIO(wire))
        return result
    monkeypatch.setattr(client.urllib.request,'build_opener',lambda *a:SimpleNamespace(open=closed))
    request=dict(offer_id=payload['product_id'],plan_id=plan['plan_id'],confirm_miaoshou_write=True)
    code,result=server._prepare_miaoshou_release(request)
    assert code==502 and result['state']=='UNKNOWN'
    with store._connect_readonly() as db:
        row=db.execute('SELECT * FROM release_target_failure_events').fetchone()
        assert row is not None
        evidence=json.loads(row['evidence_json'])
        assert evidence['outcome']==dict(business='BUSINESS_REJECTED',http='HTTP_ERROR',network='TRANSPORT_UNKNOWN')[outcome]
        assert evidence['zero_write_proven'] is False
        assert evidence['request_canonical_sha256']
        assert CONFIG['app_secret'] not in row['evidence_json'] and 'never-retain' not in row['evidence_json']
        assert not any(key in row['evidence_json'] for key in ('base64','x-sign','X-app-key'))
        assert db.execute('SELECT count(*) FROM release_target_submissions').fetchone()[0]==0
        assert db.execute('SELECT technical_execution_state FROM release_runs').fetchone()[0]=='UNKNOWN'
        if outcome=='business':
            assert evidence['provider_code']=='VALIDATION' and evidence['provider_field_path']=='skuMap.price'
            assert evidence['wire_sha256']==sha256(wire).hexdigest()
    before=list(calls)
    code,again=server._prepare_miaoshou_release(request)
    assert code==409 and again['recovery_reason']=='COMMON_TECHNICAL_ACCEPTED_EDIT_RECEIPT_MISSING'
    assert calls==before and calls.count(boundary.EDIT_PATH)==1
