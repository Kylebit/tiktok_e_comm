"""New source contract uses one CNSC master; historical projections stay unchanged."""
from copy import deepcopy
import pytest
from domains.channel_operations.pricing_preview import build_channel_pricing_preview
from shared_platform.publication_quality_evidence import (
    _independent_price_formula_matches, _canonical_price_matches_frozen)
from shared_platform.product_publication_live_dependencies import (
    _shopee_expected_models, LivePublicationDependencyError)
from test_channel_pricing_preview import _row
from test_round1_workspace_freeze import live, prepared
from test_round1_auto_freeze import public_settings, complete_source, request


def quote(policy=None):
    return build_channel_pricing_preview({'sea':[
        _row('lh_my','LivelyHive','MY','MYR',20),
        _row('lh_ph','LivelyHive','PH','PHP',100)]},
        selected_site_keys=['lh_my','lh_ph'], shopee_exchange_rates={'PHP':.118,'MYR':1.75},
        ozon_exchange_rates={'PHP':.118}, shopee_global_source_policy=policy)


def test_canonical_master_is_existing_site_order_with_independent_regional_prices():
    current=quote('overall-master/v1');old=quote()
    assert old['target_pricing']['shopee:MY']['derived_preview']['global_original_price_cny']==35
    assert set(old['target_pricing']['shopee:MY']['derived_preview'])=={
        'global_original_price_cny','local_original_price','source_currency','exchange_rate_cny_per_local'}
    rows=current['target_pricing']
    assert current['master_price_source']['region']=='PH'
    assert rows['shopee:MY']['derived_preview']['global_original_price_cny']==11.8
    assert rows['shopee:PH']['derived_preview']['global_original_price_cny']==11.8
    assert rows['shopee:MY']['derived_preview']['local_original_price']==20
    assert rows['shopee:PH']['derived_preview']['local_original_price']==100
    def snapshot(value):
        return {'skus':[{'model_sku':'0431','specification':{'Style':'A'},
            'variant_images':['https://example.com/a.png'],
            'prices':{t:dict(row['derived_preview']) for t,row in value['target_pricing'].items() if t.startswith('shopee:')}}]}
    assert _shopee_expected_models(snapshot(current),['shopee:MY','shopee:PH'])[0]['global_price_cny']=='11.8'
    with pytest.raises(LivePublicationDependencyError,match='lineage conflicts'):
        _shopee_expected_models(snapshot(old),['shopee:MY','shopee:PH'])


@pytest.mark.parametrize('drift',['master','rate','result','missing'])
def test_canonical_source_evidence_is_bound_to_frozen_master(drift):
    from shared_platform import round1_workspace
    module=round1_workspace._module(None)
    calculation=module._safe_price_calculation(quote('overall-master/v1')['target_pricing']['shopee:MY'])
    frozen={'amount':20,'currency':'MYR','global_original_price_cny':11.8}
    snapshot={'shopee_global_master':{'price_source':{'target_label':'shopee:PH','target_key':'lh_ph','region':'PH'}},
        'skus':[{'model_sku':'0431','prices':{'tiktok:LH_PH':{'amount':100,'currency':'PHP'},'shopee:MY':frozen}}]}
    assert _independent_price_formula_matches(calculation) is True
    assert _canonical_price_matches_frozen(calculation,snapshot,'0431',frozen)
    changed=deepcopy(calculation);source=changed['derived_preview']['canonical_global_source']
    if drift=='master':source.update(region='MY',target_key='lh_my',currency='MYR',list_price=20,exchange_rate_cny_per_local=.59)
    elif drift=='rate':source['exchange_rate_cny_per_local']=.119
    elif drift=='result':changed['derived_preview']['global_original_price_cny']=12
    else:changed['derived_preview'].pop('canonical_global_source')
    assert not (_independent_price_formula_matches(changed)
        and _canonical_price_matches_frozen(changed,snapshot,'0431',frozen))


def test_new_prepare_persists_contract_and_replays_same_snapshot(live,monkeypatch):
    from shared_platform import round1_workspace
    from modules.products import server
    complete_source(live)
    packet,_=prepared(live)
    document=round1_workspace.read_preparation(live['offer'],packet['prepared_reference'])
    assert document['pricing_contract']=='canonical-global-v2'
    result=round1_workspace.auto_freeze(server,request(packet,live['offer']))
    assert result['status']=='FROZEN'
    assert round1_workspace.status(server,live['offer'],document['reference'])['snapshot']==result['snapshot']


def test_missing_canonical_rate_has_no_regional_fallback():
    result=build_channel_pricing_preview({'sea':[
        _row('lh_my','LivelyHive','MY','MYR',20), _row('lh_ph','LivelyHive','PH','PHP',100)]},
        selected_site_keys=['lh_my','lh_ph'],shopee_exchange_rates={'MYR':1.75},
        ozon_exchange_rates={},shopee_global_source_policy='overall-master/v1')
    assert result['target_pricing']['shopee:MY']['status']=='blocked'
    assert result['target_pricing']['shopee:MY']['derived_preview']['global_original_price_cny'] is None


@pytest.mark.parametrize('automatic',[False,True])
def test_historical_missing_contract_rebuild_is_explicit_legacy(monkeypatch,automatic):
    from shared_platform import round1_workspace as workspace
    from modules.sourcing import new_product_workbench as workbench
    class Done(Exception):pass
    monkeypatch.setattr(workbench,'load_state',lambda _: {})
    monkeypatch.setattr(workspace,'_same_state',lambda *_:'PREPARED')
    monkeypatch.setattr(workspace,'_auto_same_state',lambda *_:'PREPARED')
    monkeypatch.setattr(workspace,'_checked_candidate_input',lambda *_:(None,None))
    class Server:
        def _local_product_approval_actor(self):return 'owned-actor'
    def packet(*args,**kwargs):
        assert kwargs['pricing_contract']=='legacy-v1'
        raise Done
    monkeypatch.setattr(workspace,'_packet',packet)
    old={'scope':{'offer_id':'123'},'actor':'owned-actor'}
    with pytest.raises(Done):
        workspace._auto_recheck(Server(),old,{}) if automatic else workspace._recheck(Server(),old)
