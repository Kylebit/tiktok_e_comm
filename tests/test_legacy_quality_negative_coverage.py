"""Three retained failure cases; synthetic inputs and no execution authority."""
from copy import deepcopy

from shared_platform.publication_preflight import build_platform_preflight
from shared_platform.publication_quality import evaluate_publication_quality
from shared_platform.publication_quality_evidence import _independent_price_formula_matches
from test_b4b_quality_evidence_identity import claim_snapshot
from test_b4b_release_compiler import shopee_snapshot


def test_valid_frozen_source_and_common_readback_do_not_replace_missing_image_qa():
    snapshot = shopee_snapshot('v2')
    evidence = {
        'publication_bridge': {'status': 'MIAOSHOU_VERIFIED'},
        'workflow_handoff': {'snapshot_digest': snapshot['snapshot_digest']},
        'image_qa': {'schema_version': 'automated-image-qa/v1', 'status': 'PASSED'},
    }
    assert build_platform_preflight(snapshot, evidence=evidence)['status'] == 'PASSED'
    evidence.pop('image_qa')
    before = deepcopy(snapshot)
    result = build_platform_preflight(snapshot, evidence=evidence)
    assert result['status'] == 'FAILED'
    assert next(c for c in result['checks'] if c['code'] == 'SNAPSHOT_AND_MIAOSHOU_READBACK')['status'] == 'PASSED'
    assert next(c for c in result['checks'] if c['code'] == 'AUTOMATED_IMAGE_QA')['status'] == 'FAILED'
    assert result['external_write_count'] == 0
    assert snapshot == before


def test_frozen_ph_image_route_rejects_thai_locale_even_with_same_images():
    snapshot = claim_snapshot(category_id='wallpaper', title='Decorative wallpaper roll')
    snapshot['product']['description'] = 'Decorative wallpaper.'
    images = snapshot['product']['images']
    snapshot['product']['image_routing'] = {'routes': {
        'tiktok:LH_PH': {'locale': 'en-PH', 'ordered_images': images},
    }}
    control = evaluate_publication_quality(snapshot)
    assert next(c for c in control['checks'] if c['code'] == 'IMAGE_LANGUAGE_ROUTE_MATCH' and c['target_label'] == 'tiktok:LH_PH')['status'] == 'PASSED'
    snapshot['product']['image_routing']['routes']['tiktok:LH_PH']['locale'] = 'th-TH'
    before = deepcopy(snapshot)
    result = evaluate_publication_quality(snapshot)
    assert any(c['code'] == 'IMAGE_LANGUAGE_ROUTE_MISMATCH' and c['target_label'] == 'tiktok:LH_PH' for c in result['errors'])
    assert snapshot == before


def test_independent_formula_rejects_changed_cost_despite_unchanged_price_result():
    calculation = {
        'contract_version': 'publication-price-calculation/v2',
        'kind': 'SEA_REVERSE_PRICING',
        'inputs': {
            'goods_cost_local': 80, 'logistics_local': 0, 'fixed_fee_local': 0,
            'commission_rate_pct': 0, 'transaction_rate_pct': 0, 'extra_rate_pct': 0,
            'affiliate_rate_pct': 0, 'ad_rate_pct': 0, 'creator_rate_pct': 0,
            'seller_tax_rate_pct': 0, 'target_margin_pct': 20, 'discount_reserve_pct': 0,
        },
        'result': {'sale_after_discount': 100, 'list_price': 100, 'currency': 'PHP'},
    }
    # Independent business oracle: 80 / (1 - 20%) = 100.
    assert _independent_price_formula_matches(calculation) is True
    calculation['inputs']['goods_cost_local'] = 79
    before = deepcopy(calculation)
    assert _independent_price_formula_matches(calculation) is False
    assert calculation == before
