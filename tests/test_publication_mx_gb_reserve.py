"""Real original calculator and original R1 consumer; owned inputs only."""
from copy import deepcopy
import json

import pytest

from modules.sourcing import new_product_workbench as workbench
from shared_platform import release_control
from shared_platform.publication_discount_terms import apply_source_terms
from test_round1_workspace_freeze import live
from test_round1_auto_freeze import public_settings, complete_source


@pytest.mark.parametrize('target,key,old_percent', [('tiktok:MX','mx',30), ('tiktok:GB','uk',25)])
def test_explicit35_recalculates_original_price_profit_and_retains_baseline(target,key,old_percent):
    baseline = workbench.price_review(13, .3, [15,13,10])
    original = deepcopy(baseline)
    result = workbench.price_review(13, .3, [15,13,10],
        publication_discounts_by_target={target:35})
    row = result[key]
    assert baseline == original
    assert row['header_meta']['discount_reserve_pct'] == 35
    assert row['list_price'] > baseline[key]['list_price']
    assert row['sale_after_discount_local'] == round(row['list_price'] * .65, 2)
    assert row['estimated_profit_cny'] >= row['minimum_profit_cny'] == 5
    assert row['profit_margin_on_sale_pct'] >= row['header_meta']['target_margin_pct'] - .5
    proof = row['discount_reserve_repricing']
    assert proof['target_label'] == target
    assert proof['original_quote']['header_meta']['discount_reserve_pct'] == old_percent
    for field in ('list_price','sale_after_discount_local','estimated_profit_cny','profit_margin_on_sale_pct'):
        assert proof['original_quote'][field] == baseline[key][field]
        assert proof['recalculated_quote'][field] == row[field]
    assert 'price_override' not in row and 'pricing_authority' not in row


def test_absent_discount_terms_preserve_original_defaults_and_global_rules():
    before_mx, before_gb = deepcopy(workbench.MX_RULE), deepcopy(workbench.GB_RULE)
    original = workbench.price_review(13, .3, [15,13,10])
    assert workbench.price_review(13, .3, [15,13,10], publication_discounts_by_target=None) == original
    assert original['mx']['header_meta']['discount_reserve_pct'] == 30
    assert original['uk']['header_meta']['discount_reserve_pct'] == 25
    assert 'discount_reserve_repricing' not in original['mx']
    assert 'discount_reserve_repricing' not in original['uk']
    assert workbench.MX_RULE == before_mx and workbench.GB_RULE == before_gb


@pytest.mark.parametrize('target', ['tiktok:MX','tiktok:GB'])
def test_original_dashboard_and_R1_consumer_accept_recalculated35_not_relabelled_quote(live,target):
    complete_source(live)
    path = live['root']/'data/new_product_workbench'/f"{live['offer']}.json"
    state = json.loads(path.read_bytes())
    state['review'].update(cost_cny=13, weight_kg=.3, package_cm=[15,13,10],
        publication_discounts_by_target={target:35})
    path.write_text(json.dumps(state), encoding='utf-8')
    # This is the same real producer/guard which failed for the current task.
    dashboard = release_control.build_release_dashboard(offer_id=live['offer'])
    pricing = dashboard['pricing_review']
    quote = pricing['target_pricing'][target]['sku_prices'][0]
    assert quote['postpublish_discount']['percent_off'] == 35
    from test_publication_gallery_preparation_metadata import source
    projected, = source()._safe_target_facts(dashboard, [target])
    assert projected['price']['sku_prices'][0]['postpublish_discount']['percent_off'] == 35
    # A different, deeper intent still cannot reuse this35 pricing proof.
    with pytest.raises(ValueError, match='EXCEEDS_PRICED_RESERVE'):
        apply_source_terms(pricing, {'review':{'publication_discounts_by_target':{target:40}}}, [target])
