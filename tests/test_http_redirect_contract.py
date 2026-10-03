"""Real urllib opener/redirect chain, only lowest HTTPS responses are fake."""
from email.message import Message
import io
import urllib.error
import urllib.request
from urllib.response import addinfourl
from types import SimpleNamespace

import pytest
from core import http_retry


@pytest.mark.parametrize('method,body',[('GET',None),('POST',b'fixture write')])
@pytest.mark.parametrize('target',['https://outside.invalid/second','https://provider.invalid/second'])
def test_root_default_redirect_cannot_spend_hidden_attempt_or_forward_credentials(monkeypatch,method,body,target,record_property):
    calls=[];monkeypatch.setattr(urllib.request,'getproxies',lambda:{})
    def fake_https(handler,req):
        calls.append({'url':req.full_url,'method':req.get_method(),'synthetic_token':req.get_header('X-tts-access-token')})
        headers=Message();status=200
        if len(calls)==1:headers['Location']=target;status=302
        response=addinfourl(io.BytesIO(b'{}'),headers,req.full_url,status);response.msg='Synthetic';return response
    monkeypatch.setattr(urllib.request.HTTPSHandler,'https_open',fake_https)
    req=urllib.request.Request('https://provider.invalid/first',method=method,data=body,headers={'x-tts-access-token':'synthetic-only-token'})
    try:
        with pytest.raises(urllib.error.HTTPError) as caught:
            http_retry.urlopen(req,attempts=1,context=http_retry.DEFAULT_SSL_CTX,allow_curl_fallback=False)
        assert caught.value.code==302
        assert len(calls)==1
    finally:record_property('lowest_http_calls',calls)


def test_curl_redirect_is_not_a_successful_asset_response(monkeypatch):
    monkeypatch.setattr(http_retry.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout=b'redirect body\n__CURL_HTTP_CODE__:302',stderr=b''))
    with pytest.raises(urllib.error.HTTPError) as caught:
        http_retry._curl_urlopen(urllib.request.Request('https://provider.invalid/image'),1,proxy='')
    assert caught.value.code==302


@pytest.mark.parametrize('status',[301,303,307,308])
def test_all_automatic_redirect_handlers_stop_before_second_hop(monkeypatch,status):
    calls=[];monkeypatch.setattr(urllib.request,'getproxies',lambda:{})
    def lowest(handler,req):
        calls.append(req)
        headers=Message();headers['Location']='https://outside.invalid/?secret=fixture'
        response=addinfourl(io.BytesIO(b'fixture'),headers,req.full_url,status);response.msg='redirect';return response
    monkeypatch.setattr(urllib.request.HTTPSHandler,'https_open',lowest)
    with pytest.raises(urllib.error.HTTPError) as caught:http_retry.urlopen(urllib.request.Request('https://provider.invalid/first'),attempts=4)
    assert caught.value.code==status and len(calls)==1


def test_private_opener_keeps_actual_proxy_handler_selection_and_tls_context(monkeypatch):
    calls=[];contexts=[]
    monkeypatch.setattr(urllib.request,'getproxies',lambda:{'https':'http://proxy.fixture:3128'})
    monkeypatch.setattr(urllib.request,'proxy_bypass',lambda host:False)
    original=urllib.request.HTTPSHandler.__init__
    def handler_init(handler,*args,**kwargs):
        contexts.append(kwargs.get('context'));original(handler,*args,**kwargs)
    monkeypatch.setattr(urllib.request.HTTPSHandler,'__init__',handler_init)
    def lowest(handler,req):
        calls.append((req.host,req._tunnel_host,req.get_header('X-tts-access-token')))
        response=addinfourl(io.BytesIO(b'fixture'),Message(),req.full_url,200);response.msg='ok';return response
    monkeypatch.setattr(urllib.request.HTTPSHandler,'https_open',lowest)
    req=urllib.request.Request('https://provider.invalid/first',headers={'x-tts-access-token':'synthetic-token'})
    with http_retry.urlopen(req,context=http_retry.DEFAULT_SSL_CTX,attempts=1) as response:assert response.read()==b'fixture'
    assert calls==[('proxy.fixture:3128','provider.invalid','synthetic-token')]
    assert contexts==[http_retry.DEFAULT_SSL_CTX]
