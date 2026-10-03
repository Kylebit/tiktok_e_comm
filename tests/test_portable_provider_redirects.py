from __future__ import annotations
import io
import json
from email.message import Message
from urllib.request import HTTPSHandler
from urllib.response import addinfourl

import pytest

from modules.tools import duoplus, tikhub
from tests.test_portable_provider_recovery import authorization,scope,tikhub_scope


def redirect_transport(monkeypatch,location,code=302):
    calls=[]
    def fake_https(handler,req):
        calls.append({'url':req.full_url,'method':req.get_method(),'headers':dict(req.header_items())})
        headers=Message();headers['Location']=location
        result=addinfourl(io.BytesIO(b''),headers,req.full_url,code);result.msg='Synthetic redirect'
        return result
    monkeypatch.setattr(HTTPSHandler,'https_open',fake_https)
    return calls


@pytest.mark.parametrize('provider',['duoplus','tikhub'])
@pytest.mark.parametrize('code',[301,302,303,307,308])
@pytest.mark.parametrize('location',['https://outside.invalid/receive','/same-origin-next'])
def test_real_urllib_redirect_chain_never_makes_second_hop(monkeypatch,provider,code,location):
    calls=redirect_transport(monkeypatch,location,code)
    with pytest.raises(RuntimeError) as error:
        if provider=='duoplus':duoplus.DuoPlusClient(api_key='synthetic-secret').devices()
        else:tikhub.request_json(tikhub.PRODUCT_SEARCH_PATH,{'search_word':'fixture'},'synthetic-secret')
    assert len(calls)==1
    assert calls[0]['url'].startswith('https://openapi.duoplus.cn/' if provider=='duoplus' else 'https://api.tikhub.io/')
    assert calls[0]['method']==('POST' if provider=='duoplus' else 'GET')
    assert any('synthetic-secret' in value for value in calls[0]['headers'].values())
    assert 'synthetic-secret' not in str(error.value) and 'outside.invalid' not in str(error.value)


@pytest.mark.parametrize('provider',['duoplus','tikhub'])
def test_redirect_retains_durable_unknown_and_attempt_without_resubmission(tmp_path,monkeypatch,provider):
    calls=redirect_transport(monkeypatch,'https://outside.invalid/receive')
    if provider=='duoplus':
        client=duoplus.DuoPlusClient(api_key='synthetic-secret')
        client.apps=lambda **kw:{'data':{'list':[{'id':'app-a','pkg':'com.example.app','version_list':[{'id':'version-a'}]}]}}
        client.installed_apps=lambda device:{'data':{'list':['com.example.app']}}
        with pytest.raises(duoplus.DuoPlusError,match='unknown'):
            duoplus.execute_install(client,artifact_root=tmp_path,scope=scope(),authorization=authorization(scope()))
        recovered=duoplus.execute_install(client,artifact_root=tmp_path,scope=scope(),authorization=authorization(scope()),reconcile_only=True)
        state=json.loads(next(tmp_path.rglob('installs.json')).read_text(encoding='utf-8'))
        assert recovered['status']=='UNKNOWN' and recovered['new_install_request_count']==0
        assert next(iter(state['requests'].values()))['install_request_count']==1
    else:
        plan=tikhub.preview(keyword='fixture',region='TH')
        args=dict(plan=plan,tenant_id='tenant-a',profile_digest='a'*64,artifact_root=tmp_path,
                  authorization=authorization(tikhub_scope(plan)),api_key='synthetic-secret')
        with pytest.raises(RuntimeError,match='unknown'):tikhub.collect(**args)
        with pytest.raises(RuntimeError,match='unknown'):tikhub.collect(**args)
        state=json.loads(next(tmp_path.rglob('manifest.json')).read_text(encoding='utf-8'))
        assert len(state['requests'])==1 and state['requests'][0]['status']=='UNKNOWN'
        assert state['requests'][0]['paid_request_count']==1
    assert len(calls)==1
