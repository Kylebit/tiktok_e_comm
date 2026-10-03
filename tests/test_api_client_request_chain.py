"""Actual API/auth/transport entry chain with synthetic lowest HTTPS I/O only."""
from email.message import Message
import io
import json
import traceback
from email.utils import formatdate
from pathlib import Path
from types import SimpleNamespace
import urllib.error
import urllib.request
from urllib.response import addinfourl

import pytest

from core import api_client as api,auth,http_retry


@pytest.fixture
def chain(monkeypatch):
    state=SimpleNamespace(http=[],auth_saves=[],sleeps=[],policies=[],response=None,retries=1)
    settings=lambda:{'app_key':'synthetic-app','app_secret':'synthetic-secret'}
    monkeypatch.setattr(api,'load_settings',settings);monkeypatch.setattr(auth,'load_settings',settings)
    monkeypatch.setattr(api,'config_get',lambda *a:{'rate_limit_retries':state.retries,'rate_limit_backoff_sec':[0]})
    monkeypatch.setattr(auth,'load_token',lambda:{'access_token':'synthetic-old','refresh_token':'synthetic-refresh','access_token_expire_in':1,'refresh_token_expire_in':99999})
    monkeypatch.setattr(auth,'save_token',lambda value:state.auth_saves.append(value.copy()))
    clock=SimpleNamespace(time=lambda:1000,sleep=state.sleeps.append)
    monkeypatch.setattr(api,'time',clock);monkeypatch.setattr(auth,'time',clock);monkeypatch.setattr(http_retry,'time',clock)
    monkeypatch.setattr(urllib.request,'getproxies',lambda:{})
    def lowest(handler,req):
        token=req.get_header('X-tts-access-token')
        kind='auth' if req.host=='auth.tiktok-shops.com' else 'api'
        state.http.append({'kind':kind,'token':token,'method':req.get_method(),'data':req.data,'url':req.full_url})
        if kind=='auth':status,body,headers=200,{'code':0,'data':{'access_token':'synthetic-fresh','access_token_expire_in':9999}},{}
        else:status,body,headers=state.response(req,state)
        hdrs=Message()
        for key,value in headers.items():hdrs[key]=value
        raw=body if isinstance(body,bytes) else json.dumps(body).encode()
        result=addinfourl(io.BytesIO(raw),hdrs,req.full_url,status);result.msg='synthetic';return result
    monkeypatch.setattr(urllib.request.HTTPSHandler,'https_open',lowest)
    return state


def events(state):
    return [(row['kind'],row['token']) for row in state.http]


def test_root_old_expired_then_sandbox_chain_has_one_budget_and_one_refresh(chain,record_property):
    chain.response=lambda req,s:(200,{'code':105002 if req.get_header('X-tts-access-token')=='synthetic-old' else 36009037,'message':'fixture'}, {})
    result=api.request('GET','/synthetic','synthetic-old')
    record_property('synthetic_events',json.dumps(events(chain)));record_property('auth_writes',len(chain.auth_saves));record_property('sleeps',chain.sleeps)
    assert result['code']==36009037
    assert events(chain)==[('api','synthetic-old'),('auth',None),('api','synthetic-fresh')]
    assert len(chain.auth_saves)==1 and chain.sleeps==[]


def test_explicit_http401_expiry_then_general_rate_limit_keeps_fresh_token(chain):
    chain.retries=2
    def response(req,state):
        if req.get_header('X-tts-access-token')=='synthetic-old':return 401,{'code':105002},{}
        fresh=sum(r['token']=='synthetic-fresh' for r in state.http)
        return (200,{'code':36009002},{'Retry-After':'2'}) if fresh==1 else (200,{'code':0,'data':{'items':['verified fixture']}},{})
    chain.response=response
    result=api.request('GET','/synthetic','synthetic-old')
    assert result['code']==0 and events(chain)==[('api','synthetic-old'),('auth',None),('api','synthetic-fresh'),('api','synthetic-fresh')]
    assert len(chain.auth_saves)==1 and len(chain.sleeps)==1 and chain.sleeps[0]>=2


@pytest.mark.parametrize('body',[b'<html>unknown 401</html>',{'code':105005},{'code':36009043},{'code':36009044}])
def test_non_expiry_http401_never_refreshes_or_replays(chain,body):
    chain.response=lambda *args:(401,body,{})
    with pytest.raises(RuntimeError):api.request('GET','/synthetic','synthetic-old')
    assert len(chain.http)==1 and chain.auth_saves==[] and chain.sleeps==[]


def test_mutation_429_is_not_assumed_safe_to_replay(chain):
    chain.retries=3;chain.response=lambda *args:(429,{'code':36009002},{'Retry-After':'1'})
    with pytest.raises(RuntimeError):api.post('/product/write','synthetic-old',body={'idempotency_key':'fixture-only'})
    assert len(chain.http)==1 and chain.auth_saves==[] and chain.sleeps==[]


def test_debug_and_nested_provider_errors_do_not_echo_secrets(chain,capsys):
    secret='synthetic-secret-never-print'
    chain.response=lambda *args:(200,{'code':105005,'message':secret,'data':{'headers':{'Authorization':secret},'nested':{'token':secret}}},{})
    result=api.request('GET','/synthetic','synthetic-old',query={'access_token':secret},debug=True)
    assert secret not in capsys.readouterr().out+json.dumps(result)
    assert result['code']==105005


def test_known_rejected_write_can_recover_auth_but_never_repeat_an_unknown_write(chain):
    chain.retries=0
    def response(req,state):
        if req.get_header('X-tts-access-token')=='synthetic-old':return 401,{'code':105002},{}
        raise TimeoutError('synthetic unknown after write')
    chain.response=response
    with pytest.raises(api.APIRequestError) as caught:api.post('/product/write','synthetic-old',body={'value':1})
    assert caught.value.kind=='transport_outcome_unknown'
    assert events(chain)==[('api','synthetic-old'),('auth',None),('api','synthetic-fresh')]
    assert len(chain.auth_saves)==1 and chain.sleeps==[]


@pytest.mark.parametrize('status',[200,401])
def test_second_expiry_is_terminal_after_one_auth_recovery(chain,status):
    chain.retries=5;chain.response=lambda *args:(status,{'code':105002},{})
    if status==401:
        with pytest.raises(api.APIRequestError):api.get('/synthetic','synthetic-old')
    else:assert api.get('/synthetic','synthetic-old')['code']==105002
    assert len(chain.http)==3 and len(chain.auth_saves)==1 and chain.sleeps==[]


def test_unknown_refresh_never_saves_or_resends_and_hides_its_error(chain,capsys):
    chain.response=lambda *args:(200,{'code':105002},{})
    prior=urllib.request.HTTPSHandler.https_open
    def lowest(handler,req):
        if req.host=='auth.tiktok-shops.com':
            chain.http.append({'kind':'auth','token':None})
            raise TimeoutError('synthetic-refresh-unknown-secret')
        return prior(handler,req)
    # Restore automatically using the fixture monkeypatch context, including auth alias.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(urllib.request.HTTPSHandler,'https_open',lowest)
        with pytest.raises(api.APIRequestError) as caught:api.get('/synthetic','synthetic-old',debug=True)
    rendered=''.join(traceback.format_exception(caught.value))
    assert caught.value.kind=='auth_recovery_failed' and 'synthetic-refresh-unknown-secret' not in rendered+capsys.readouterr().out
    assert len(chain.http)==2 and chain.auth_saves==[] and chain.sleeps==[]


@pytest.mark.parametrize('status',[200,429])
def test_sandbox_hourly_limit_never_uses_short_backoff(chain,status):
    chain.retries=5;chain.response=lambda *args:(status,{'code':36009037},{'Retry-After':'3600'})
    if status==429:
        with pytest.raises(api.APIRequestError) as caught:api.get('/synthetic','synthetic-old')
        assert caught.value.kind=='sandbox_hourly_limit' and caught.value.retry_after_seconds==3600
    else:
        result=api.get('/synthetic','synthetic-old')
        assert result['error_kind']=='sandbox_hourly_limit' and result['retry_after_seconds']==3600
    assert len(chain.http)==1 and chain.auth_saves==[] and chain.sleeps==[]


@pytest.mark.parametrize('header',['2',formatdate(1003,usegmt=True),'NaN','bad header','3600'])
def test_retry_after_is_honored_or_deferred_without_early_retry(chain,header):
    chain.response=lambda req,s:(200,{'code':36009002},{'Retry-After':header}) if len(s.http)==1 else (200,{'code':0,'data':{}},{})
    result=api.get('/synthetic','synthetic-old')
    if header=='3600':
        assert result['code']==36009002 and result['retry_after_seconds']==3600 and len(chain.http)==1 and not chain.sleeps
    else:
        assert result['code']==0 and len(chain.http)==2 and len(chain.sleeps)==1
        if header=='2':assert chain.sleeps[0]>=2
        if header.startswith('Thu'):assert chain.sleeps[0]>=3


def test_transient_read_failure_is_counted_in_same_caller_budget(chain):
    chain.retries=2
    def response(*args):raise urllib.error.URLError(TimeoutError('synthetic'))
    chain.response=response
    with pytest.raises(api.APIRequestError) as caught:api.get('/synthetic','synthetic-old')
    assert caught.value.kind=='transport_outcome_unknown' and len(chain.http)==3 and len(chain.sleeps)==2 and not chain.auth_saves


@pytest.mark.parametrize('method,body',[('POST',{}),('PUT',{}),('DELETE',None),('GET',{})])
def test_unknown_mutation_or_bodyful_get_never_replays(chain,method,body):
    def response(*args):raise TimeoutError('synthetic-secret-in-error')
    chain.response=response
    with pytest.raises(api.APIRequestError) as caught:api.request(method,'/write','synthetic-old',body=body)
    assert len(chain.http)==1 and not chain.sleeps and not chain.auth_saves
    assert 'synthetic-secret-in-error' not in ''.join(traceback.format_exception(caught.value))


@pytest.mark.parametrize('path',sorted(api.READ_ONLY_POST_PATHS))
def test_exact_reviewed_post_search_retains_read_retry_and_identical_body(chain,path):
    chain.response=lambda req,s:(200,{'code':36009002},{}) if len(s.http)==1 else (200,{'code':0,'data':{'list':['fixture']}},{})
    body={'title':'中文','n':1}
    assert api.post(path,'synthetic-old',body=body)['code']==0
    assert len(chain.http)==2 and all(r['data']==b'{"title":"\\u4e2d\\u6587","n":1}' for r in chain.http)


@pytest.mark.parametrize('method,path',[('POST','/product/write'),('PUT','/product/202309/products/search'),('POST','/order/202309/orders/search?extra=1'),('POST','/order/202309/orders/search/extra')])
def test_arbitrary_retry_read_flag_cannot_enable_write_replay(chain,method,path):
    with pytest.raises(ValueError,match='exact reviewed'):api.request(method,path,'synthetic-old',body={},retry_read=True)
    assert chain.http==[]


def test_actual_order_pagination_uses_reviewed_post_without_database(chain):
    from modules.products.sales import fetch_orders
    def response(req,state):
        query=urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)
        if 'page_token' not in query:
            if len(state.http)==1:return 429,{'code':36009002},{'Retry-After':'0'}
            return 200,{'code':0,'data':{'orders':[{'id':'one'}],'next_page_token':'cursor-two'}},{}
        assert query['page_token']==['cursor-two']
        return 200,{'code':0,'data':{'orders':[{'id':'two'}]}},{}
    chain.response=response
    assert fetch_orders('synthetic-old','fixture-shop',days=1)==[{'id':'one'},{'id':'two'}]
    assert len(chain.http)==3 and all(r['data']==chain.http[0]['data'] for r in chain.http)


def test_actual_promotion_direct_single_attempt_never_refreshes(chain,monkeypatch):
    from modules.tiktok.oneclick_promotion import _default_transport
    monkeypatch.setattr(auth,'access_token',lambda:'synthetic-old')
    chain.response=lambda *args:(200,{'code':105002},{})
    assert _default_transport().list_shops()['code']==105002
    assert len(chain.http)==1 and not chain.auth_saves and not chain.sleeps


@pytest.mark.parametrize('raw',[b'<html>synthetic-secret</html>',b'[]',b'{"code":"synthetic-secret"}',b'{"code":true}'])
def test_invalid_response_never_echoes_body_or_becomes_auth_recovery(chain,raw):
    chain.response=lambda *args:(200,raw,{})
    with pytest.raises(api.APIRequestError) as caught:api.get('/synthetic','synthetic-old')
    assert caught.value.kind=='invalid_response' and 'synthetic-secret' not in ''.join(traceback.format_exception(caught.value))
    assert len(chain.http)==1 and not chain.auth_saves


def test_sensitive_http_error_and_debug_keep_only_bounded_diagnostics(chain,capsys):
    secret='synthetic-secret-in-headers-query-body'
    chain.response=lambda *args:(401,{'code':105005,'message':secret,'data':{'nested':{'Authorization':secret}}},{'Echo':secret})
    with pytest.raises(api.APIRequestError) as caught:api.get('/secret-'+secret,'synthetic-old',query={'secret':secret},debug=True)
    assert secret not in capsys.readouterr().out+''.join(traceback.format_exception(caught.value))
    assert caught.value.http_status==401 and caught.value.provider_code==105005 and caught.value.kind=='scope_denied'


def test_http_error_body_read_failure_is_sanitized_and_never_refreshed(chain,monkeypatch):
    class FailedBody(io.BytesIO):
        def read(self,*args):raise OSError('synthetic-body-read-secret')
    def lowest(*args):
        chain.http.append({'kind':'api','token':'synthetic-old'})
        raise urllib.error.HTTPError('https://provider.invalid/?secret=synthetic-body-read-secret',401,'synthetic-body-read-secret',None,FailedBody())
    monkeypatch.setattr(urllib.request.HTTPSHandler,'https_open',lowest)
    with pytest.raises(api.APIRequestError) as caught:api.get('/synthetic','synthetic-old')
    assert len(chain.http)==1 and not chain.auth_saves
    assert 'synthetic-body-read-secret' not in ''.join(traceback.format_exception(caught.value))


@pytest.mark.parametrize('case',json.loads((Path(__file__).parent/'fixtures/tiktok_api_signature_v1.json').read_text(encoding='utf-8'))['cases'])
def test_wire_body_query_and_signature_match_exact_retained_source_golden(chain,case):
    chain.response=lambda *args:(200,{'code':0,'data':{'unchanged':True}}, {})
    result=api.request('POST',case['path'],'synthetic-old',query=case['query'],body=case['body'])
    sent=chain.http[0];query=urllib.parse.parse_qs(urllib.parse.urlsplit(sent['url']).query)
    assert result=={'code':0,'data':{'unchanged':True}} and len(chain.http)==1
    assert sent['data']==(case['body_bytes_utf8'].encode() if case['body'] is not None else None)
    assert query['sign']==[case['signature']]
    assert all(query[key]==[value] for key,value in case['query'].items())


def test_paginate_get_preserves_query_cursor_and_output_order(chain):
    original={'shop_cipher':'fixture-shop','page_size':'2'}
    def response(req,state):
        query=urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)
        assert query['shop_cipher']==['fixture-shop'] and query['page_size']==['2']
        if len(state.http)==1:return 200,{'code':0,'data':{'items':['a','b'],'next_page_token':'next+cursor'}},{}
        assert query['page_token']==['next+cursor']
        return 200,{'code':0,'data':{'items':['c']}},{}
    chain.response=response
    assert api.paginate_get('/synthetic','synthetic-old',original,'items')==['a','b','c']
    assert original=={'shop_cipher':'fixture-shop','page_size':'2'} and len(chain.http)==2
