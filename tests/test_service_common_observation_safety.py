"""Additional test-first boundaries, kept separate from frozen original RED6."""
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from modules.miaoshou import client
from modules.products import server, release_adapters
from shared_platform import publication_runtime_config
from shared_platform.r3_common_source_facts import NativeCommonSourceReader
from shared_platform.r3_frozen_review_producer import DomainFrozenReviewProducer, DomainReviewBlocked
from test_r3_common_source_facts import stored_common, connect, rows
from test_release_store import _plan
from test_service_common_observation import CONFIG, ClosedCommonTransport, ClosedResponse


def test_missing_pinned_common_config_cannot_fall_back_to_other_ambient_root(tmp_path, monkeypatch):
    pinned = tmp_path / 'pinned'; pinned.mkdir()
    ambient = tmp_path / 'ambient'; (ambient / 'config').mkdir(parents=True)
    other = ambient / 'config/miaoshou.local.json'
    other.write_text(json.dumps({'app_id':'other-closed-scope','app_secret':'other-closed-secret'}),encoding='utf-8')
    startup = publication_runtime_config.capture_startup_config(root=pinned, environ={})
    monkeypatch.setenv('ORBIT_R3_CONFIG_ROOT',str(ambient))
    monkeypatch.setenv('TIKTOK_ECOMM_HOME',str(ambient))
    monkeypatch.setattr(client,'CONFIG_CANDIDATES',[other])
    monkeypatch.setattr(client,'MAIN_REPO',ambient)
    monkeypatch.setattr(server,'_COMMON_DETAIL_OBSERVER_FACTORY',None,raising=False)
    monkeypatch.setattr(client.urllib.request,'urlopen',lambda *a,**kw: pytest.fail('missing config must not dispatch'))
    server._install_service_common_detail_observer(startup)
    with pytest.raises((FileNotFoundError,client.MiaoshouLocalConfigMissing)):
        server._COMMON_DETAIL_OBSERVER_FACTORY()
    assert other.read_text(encoding='utf-8')==json.dumps({'app_id':'other-closed-scope','app_secret':'other-closed-secret'})


def test_actual_installed_factory_uses_pinned_scope_without_account_authority(tmp_path, monkeypatch):
    pinned=tmp_path/'pinned'; (pinned/'config').mkdir(parents=True)
    (pinned/'config/miaoshou.local.json').write_text(json.dumps(CONFIG),encoding='utf-8')
    ambient=tmp_path/'ambient';(ambient/'config').mkdir(parents=True)
    other=ambient/'config/miaoshou.local.json'
    other.write_text(json.dumps({'app_id':'other-closed-scope','app_secret':'other-closed-secret'}),encoding='utf-8')
    startup=publication_runtime_config.capture_startup_config(root=pinned,environ={})
    monkeypatch.setenv('ORBIT_R3_CONFIG_ROOT',str(ambient))
    monkeypatch.setattr(client,'CONFIG_CANDIDATES',[other])
    monkeypatch.setattr(client,'_wait_for_open_slot',lambda:None)
    monkeypatch.setattr(server,'_COMMON_DETAIL_OBSERVER_FACTORY',None,raising=False)
    calls=[]
    def closed(request,timeout):
        assert request.full_url==client.OPEN_BASE_URL+client.COMMON_DETAIL_OBSERVATION_PATH
        assert request.get_method()=='POST' and timeout==30
        assert request.get_header('X-app-key')==CONFIG['app_id']
        assert json.loads(request.data)=={'commonCollectBoxDetailId':123}
        calls.append('exact-pinned-transport')
        return ClosedResponse(request.full_url,b'{"result":"success","data":{}}')
    monkeypatch.setattr(client.urllib.request,'urlopen',lambda *a,**kw:pytest.fail('installed native must use refusing opener'))
    monkeypatch.setattr(client.urllib.request,'build_opener',lambda *handlers:SimpleNamespace(open=closed))
    server._install_service_common_detail_observer(startup)
    assert calls==[]
    observed=server._COMMON_DETAIL_OBSERVER_FACTORY().observe('123')
    packet=observed.receipt()
    assert calls==['exact-pinned-transport']
    assert packet['credential_scope_digest']==hashlib.sha256(('miaoshou-app-scope:'+CONFIG['app_id']).encode()).hexdigest()
    assert packet['execution_authority'] is False
    assert packet['evidence_kind']=='NATIVE_TRANSPORT_OBSERVATION_NOT_APPROVAL'
    assert not {'budget','budget_profile','approval_id','authenticated_account','execution_capability'} & set(packet)


def test_retained_native_wire_packet_still_cannot_authorize_real_reader_or_producer(stored_common,tmp_path,monkeypatch):
    store,market_id=stored_common
    market=store.get_plan(market_id)
    common=store.get_plan(market['payload']['r3_marketplace_binding']['common_plan_id'])
    io=ClosedCommonTransport(monkeypatch,common['payload'])
    packet=release_adapters.readback_miaoshou_common(common['payload'],
        observation_reader=client.NativeCommonDetailObserver(CONFIG))
    assert packet['verified'] is True and io.edits==[]
    extra=store.create_plan(_plan(product_id=common['product_id'],targets=['miaoshou:COMMON']))
    store.approve_plan(extra['plan_id'],approved_by='Kyle',user_approved=True,
        confirmation_token=extra['confirmation_token'])
    run=store.start_run(extra['plan_id']);store.begin_target(run['run_id'],'miaoshou:COMMON')
    store.record_target_success(run['run_id'],'miaoshou:COMMON',external_id=common['product_id'],readback_evidence=packet)
    with connect(store) as db:
        before=rows(db)
        reader=NativeCommonSourceReader(store)
        facts=reader.read_source_facts(db,market_id)
        native=next(record for record in facts.records if record.table=='release_target_readbacks'
                    and record.identity[0]==run['run_id'])
        assert json.loads(native.evidence_bytes)['native_common_observation']['business_sha256']==packet['native_common_observation']['business_sha256']
        assert facts.official_provenance=='UNKNOWN' and facts.budget_status=='UNKNOWN'
        assert facts.source_coverage=='LOCAL_RETAINED_ONLY' and facts.execution_authority is False
        assert native.official_response_bytes is None
        with pytest.raises(DomainReviewBlocked,match='COMMON_TRUSTED_BUDGET_AND_OFFICIAL_READBACK_REQUIRED'):
            reader.read_verified(db,'not-admitted',market_id)
        with pytest.raises(DomainReviewBlocked,match='COMMON_TRUSTED_BUDGET_AND_OFFICIAL_READBACK_REQUIRED'):
            DomainFrozenReviewProducer(reader).build(db,'not-admitted',market_id)
        assert rows(db)==before
