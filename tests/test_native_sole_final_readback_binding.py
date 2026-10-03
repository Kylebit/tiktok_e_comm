"""Exact native submission scope; owned responses do not invent shop mappings."""
from copy import deepcopy
from dataclasses import replace

import pytest

from modules.products import release_adapters as adapters
from shared_platform import native_sole_final_service as channel
from shared_platform import native_sole_final_readback as readonly
from test_round1_workspace_freeze import live
from test_native_sole_final_service import _installed, _http, _post
from test_native_sole_final_continuation import _join
from domains.channel_operations.release_executor import AdapterExecutionResult


@pytest.mark.parametrize('regional_shop', ['8','different-shop'], ids=['equal-different-domains','other-shop-same-sku'])
def test_region_or_equal_numeric_ids_never_substitute_for_frozen_official_shop_mapping(monkeypatch,regional_shop):
    # Even a perfect product in the regional first shop is unrelated without
    # an actual immutable cross-domain shop mapping. Do not read/refresh it.
    monkeypatch.setattr(adapters,'_tiktok_shop',lambda *_:pytest.fail('regional shop must not be chosen'))
    monkeypatch.setattr(adapters.auth,'access_token',lambda:pytest.fail('no credential refresh'))
    monkeypatch.setattr(adapters,'_tiktok_readback',lambda **_:pytest.fail('same SKU is not a shop binding'))
    payload={'product_facts':{'categories_by_target':{'tiktok:LH_PH':{
        'target_label':'tiktok:LH_PH','platform':'tiktok','site':'LH_PH','category':{'id':'600999'}}}}}
    target={'target_label':'tiktok:LH_PH','external_id':'7:8', 'submission':{'evidence':{
        'accepted':True,'detail_id':7,'shop_id':8,'verified':True,
        'untrusted_official_shop_id':regional_shop}}}
    result=readonly._official_observation(payload,target)
    assert result['verified'] is False
    assert result['capability']=='FROZEN_OFFICIAL_SHOP_MAPPING_UNAVAILABLE'
    assert result['expected_category_id']=='600999' and result['external_writes_performed']==[]


def test_frozen_target_category_drift_is_not_replaced_by_hardcoded_category(monkeypatch):
    monkeypatch.setattr(adapters,'_tiktok_readback',lambda **_:pytest.fail('drift must not query'))
    target={'target_label':'tiktok:LH_PH','external_id':'7:8','submission':{
        'evidence':{'accepted':True,'detail_id':7,'shop_id':8}}}
    for category in ({'target_label':'tiktok:LH_MY','platform':'tiktok','site':'LH_MY','category':{'id':'600338'}},
                     {'target_label':'tiktok:LH_PH','platform':'shopee','site':'LH_PH','category':{'id':'600338'}}):
        result=readonly._official_observation({'product_facts':{'categories_by_target':{'tiktok:LH_PH':category}}},target)
        assert result['capability']=='FROZEN_TARGET_CATEGORY_UNAVAILABLE'
        assert result['verified'] is False and result['external_writes_performed']==[]


def test_retained_nested_miaoshou_submission_preserves_identity_domain(monkeypatch):
    monkeypatch.setattr(adapters,'_tiktok_readback',lambda **_:pytest.fail('mapping is missing'))
    payload={'product_facts':{'categories_by_target':{'tiktok:LH_PH':{
        'target_label':'tiktok:LH_PH','platform':'tiktok','site':'LH_PH','category':{'id':'600999'}}}}}
    result=readonly._official_observation(payload,{'target_label':'tiktok:LH_PH','external_id':'7:8',
        'submission':{'evidence':{'shop_id':'official-different-domain',
            'accepted_submission':{'accepted':True,'detail_id':7,'shop_id':8}}}})
    assert result['capability']=='FROZEN_OFFICIAL_SHOP_MAPPING_UNAVAILABLE'
    assert result['expected_category_id']=='600999' and not result['verified']


def test_real_native_adapter_and_explicit_readback_resume_do_not_read_wrong_shop_refresh_or_write_twice(live,monkeypatch,tmp_path):
    installed,market,*_=_installed(live,monkeypatch,tmp_path)
    original=adapters.production_adapter_registry()
    calls=[]
    def accepted(payload,*,site):
        calls.append(site)
        assert site in adapters.SEA_SITES
        return '7:8',{'source':'miaoshou_open_api','accepted':True,'detail_id':7,'shop_id':8,
            'write_outcome':'submission_accepted','external_writes_performed':['owned:submission']}
    monkeypatch.setattr(adapters,'_miaoshou_publish_target',accepted)
    monkeypatch.setattr(adapters,'_tiktok_readback',lambda **_:pytest.fail('native wrong-shop READ must not run'))
    monkeypatch.setattr(adapters.auth,'access_token',lambda:pytest.fail('native credential mutation must not run'))
    monkeypatch.setattr(adapters,'_repair_tiktok_title',lambda **_:pytest.fail('native READ cannot repair'))
    monkeypatch.setattr(adapters,'_cache_verified_tiktok_listing',lambda **_:pytest.fail('native READ cannot cache'))
    def dispatch(request):
        if request.target_label in {'tiktok:LH_PH','tiktok:LH_MY'}:
            return adapters.execute_tiktok_target(request)
        return AdapterExecutionResult(True,True,'owned unrelated target mechanics','owned:'+request.target_label,
            {'source':'OWNED_UNIT_READBACK','verified':True,'target_label':request.target_label})
    monkeypatch.setattr(adapters,'production_adapter_registry',lambda:{key:replace(value,execute=dispatch,
        blocker=None,automatic_first_attempt_mode='ENABLED') for key,value in original.items()})
    http,thread=_http(installed)
    try:
        prepared=installed.prepare(market['plan_id'])
        code,result=installed.approve_and_submit(nonce=prepared['nonce'],review_digest=prepared['review_digest'])
        assert code==202,result
        _join(installed)
        decision=result['decision']['decision_id']
        first=deepcopy(installed.store.get_run('release-run:'+market['payload_digest'][:24]))
        native_targets=[row for row in first['targets'] if row['target_label'] in {'tiktok:LH_PH','tiktok:LH_MY'}]
        assert len(native_targets)==2 and sorted(calls)==['LH_MY','LH_PH']
        assert all(row['status']=='SUBMITTED_UNVERIFIED' and not row['readback'] and row['attempts']==1 for row in native_targets)
        assert all(row['submission']['evidence']['official_readback_capability']=='FROZEN_OFFICIAL_SHOP_MAPPING_UNAVAILABLE' for row in native_targets)
        code,response=server_view(installed,market)
        assert code==200 and response['marketplace']['native_readback_resume_available'] is True,response
        assert response['marketplace']['native_readback_capabilities']
        # This actual Handler route schedules READ only, including after a
        # service-memory result is absent. It cannot fall back to publish.
        installed._execution_results.pop(decision,None)
        code,result=_post(http,channel.PREFIX+'readback',{'decision_id':decision})
        assert code==202 and result['readonly_recheck'] is True,result
        _join(installed)
        assert sorted(calls)==['LH_MY','LH_PH']
        after=installed.store.get_run(first['run_id'])
        assert [row['attempts'] for row in after['targets']]==[row['attempts'] for row in first['targets']]
        assert [row['submission'] for row in after['targets']]==[row['submission'] for row in first['targets']]
        assert installed._execution_results[decision][1]['completed'] is False
    finally:
        http.shutdown();http.server_close();thread.join(timeout=5);installed.close()


def server_view(installed,market):
    from modules.products import server
    return server._publication_stages_for_request({'offer_id':market['product_id'],'plan_id':market['plan_id']})


def test_explicit_readback_requires_original_accepted_submission_not_just_saved_approval(live,monkeypatch,tmp_path):
    installed,market,*_=_installed(live,monkeypatch,tmp_path)
    monkeypatch.setattr(readonly,'observe_submissions',lambda *_:pytest.fail('no accepted run'))
    http,thread=_http(installed)
    try:
        value=installed.prepare(market['plan_id'])
        saved=installed.decide(nonce=value['nonce'],review_digest=value['review_digest'])
        before=installed.store.path.read_bytes()
        code,result=_post(http,channel.PREFIX+'readback',{'decision_id':saved['decision_id']})
        assert code==409 and result['error']=='NATIVE_ACCEPTED_SUBMISSION_REQUIRED_FOR_READ_ONLY_RESUME'
        assert installed.store.path.read_bytes()==before
        assert installed._execution_thread is None and not installed._readback_schedule
    finally:
        http.shutdown();http.server_close();thread.join(timeout=5);installed.close()
