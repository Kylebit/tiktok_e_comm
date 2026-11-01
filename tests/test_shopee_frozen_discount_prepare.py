"""New immutable products use their own quote through the real SH registration.

Only the final official transport edge is synthetic. No token, database,
provider request, existing activity or real product is opened by these tests.
"""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from domains.channel_operations.oneclick_release_adapters import production_adapter_registry
from modules.shopee import oneclick_promotion as sh
from shared_platform.postpublish_promotions import build_approved_postpublish_promotion_policy


QUOTES = {
    'MY': ('MYR', '74', '51.80'),
    'PH': ('PHP', '851', '595.70'),
    'TH': ('THB', '584', '408.80'),
    'VN': ('VND', '554000', '387800.00'),
}


def _request(region):
    currency, amount, sale = QUOTES[region]
    target = 'shopee:' + region
    payload = {
        'product_id': 'owned-new-product-not-a-retained-offer',
        'seller_sku': 'owned-new-model',
        'sku_lineage': {'assignment': {'model_skus': [{'model_sku': 'owned-new-model'}]}},
        'approved_postpublish_promotion_policy': build_approved_postpublish_promotion_policy(
            approval_reference='OWNED_IMMUTABLE_FIXTURE_NOT_LIVE_AUTHORITY'),
        'pricing': {'selected_targets': {target: {'store_prices': [{
            'currency': currency, 'list_price': amount,
            'discount_reserve_pct': 30, 'sale_after_discount': sale,
        }]}}},
    }
    return SimpleNamespace(target_label='promotion:' + target,
        immutable_plan_payload=payload, idempotency_key='owned-promotion-' + region,
        prerequisite_context={'target_label': target, 'external_id': 'shopee:100:item:200',
            'readback_evidence_digest': 'a'*64, 'result_digest': 'b'*64})


def _closed_edge(monkeypatch, region, *, add_unknown=False):
    currency, amount, sale = QUOTES[region]
    calls, writes = [], []
    class Transport:
        def __call__(self, path, params):
            calls.append((path, dict(params)))
            if path == sh.MODEL:
                return {'model': [{'model_id': 300, 'model_sku': 'owned-new-model',
                    'price_info': [{'currency': currency, 'original_price': amount}]}]}
            if path == sh.LIST:
                return {'more': False, 'discount_list': [{'discount_id': 400,
                    'start_time': 1, 'end_time': 4102444800, 'discount_status': 'ongoing'}]}
            if path == sh.DETAIL:
                return {'more': False, 'item_list': []}
            pytest.fail('Unexpected original official read endpoint')
        def add(self, body):
            writes.append(deepcopy(body))
            if add_unknown:
                raise TimeoutError('Owned synthetic external edge; nothing sent')
            pytest.fail('No write expected')
    monkeypatch.setattr(sh, '_transport', lambda action, prerequisite: Transport())
    return calls, writes


@pytest.mark.parametrize('region', QUOTES)
def test_real_shopee_registration_uses_own_frozen_prices_and_exact_decimal_body(monkeypatch, region):
    request = _request(region)
    original = deepcopy(request.immutable_plan_payload)
    calls, writes = _closed_edge(monkeypatch, region)
    result = production_adapter_registry()['postpublish_promotion'].prepare(request)
    assert result['classification'] == 'EXACT_READY_AUTOMATIC', result
    command = result['command']
    currency, amount, sale = QUOTES[region]
    assert command['expected'] == {'currency': currency, 'list_price': amount,
        'sale_after_discount': sale, 'seller_skus': ['owned-new-model']}
    assert command['discount_percent'] == 30
    assert command['mode'] == 'ADD_TO_EXISTING_DISCOUNT'
    value = sh._body(command)['item_list'][0]['model_list'][0]['model_promotion_price']
    assert Decimal(str(value)) == Decimal(sale)
    assert [p for p, _ in calls] == [sh.MODEL, sh.LIST, sh.DETAIL]
    assert not writes and request.immutable_plan_payload == original


@pytest.mark.parametrize('damage', [
    'missing-approval', 'missing-row', 'multiple-rows', 'bad-sale',
    'bad-currency', 'boolean-rate', 'nonfinite', 'missing-prerequisite',
])
def test_new_frozen_contract_damage_stops_before_official_reads(monkeypatch, damage):
    request = _request('PH')
    payload = request.immutable_plan_payload
    row = payload['pricing']['selected_targets']['shopee:PH']['store_prices'][0]
    if damage == 'missing-approval':
        del payload['approved_postpublish_promotion_policy']
    elif damage == 'missing-row':
        payload['pricing']['selected_targets']['shopee:PH']['store_prices'] = []
    elif damage == 'multiple-rows':
        payload['pricing']['selected_targets']['shopee:PH']['store_prices'].append(deepcopy(row))
    elif damage == 'bad-sale':
        row['sale_after_discount'] = '596'
    elif damage == 'bad-currency':
        row['currency'] = 'JPY'
    elif damage == 'boolean-rate':
        row['discount_reserve_pct'] = True
    elif damage == 'nonfinite':
        row['sale_after_discount'] = 'NaN'
    else:
        request.prerequisite_context = {'target_label': 'shopee:MY'}
    calls, writes = _closed_edge(monkeypatch, 'PH')
    result = production_adapter_registry()['postpublish_promotion'].prepare(request)
    assert result['classification'] == 'BLOCKED_CAPABILITY', result
    assert result['command'] is None and result['proof'] is None
    assert not calls and not writes


def test_missing_new_projection_still_cannot_borrow_retained_product_prices(monkeypatch):
    request = _request('PH')
    del request.immutable_plan_payload['pricing']['selected_targets']['shopee:PH']['store_prices']
    calls, writes = _closed_edge(monkeypatch, 'PH')
    result = production_adapter_registry()['postpublish_promotion'].prepare(request)
    assert result['classification'] == 'BLOCKED_CAPABILITY'
    assert 'EXACT_ORIGINAL_PRODUCT_REQUIRED' in result['reason_detail']
    assert not calls and not writes


@pytest.mark.parametrize('percent', [30, 35])
def test_legacy_frozen_policy_cannot_borrow_a_different_projected_rate(monkeypatch, percent):
    request = _request('PH')
    fixture = Path(__file__).parent/'fixtures/b4b_legacy_promotion_v1.json'
    request.immutable_plan_payload['approved_postpublish_promotion_policy'] = json.loads(
        fixture.read_text(encoding='utf-8'))['approval']
    row = request.immutable_plan_payload['pricing']['selected_targets']['shopee:PH']['store_prices'][0]
    row['discount_reserve_pct'] = percent
    row['sale_after_discount'] = str(Decimal('851')*(1-Decimal(percent)/100))
    calls, writes = _closed_edge(monkeypatch, 'PH')
    result = production_adapter_registry()['postpublish_promotion'].prepare(request)
    if percent == 30:
        assert result['classification'] == 'EXACT_READY_AUTOMATIC', result
    else:
        assert result['classification'] == 'BLOCKED_CAPABILITY'
        assert result['reason_code'] == 'shopee_promotion_frozen_discount_changed'
        assert not calls
    assert not writes


def test_new_quote_preserves_original_intent_before_unknown_add(monkeypatch):
    from modules.tiktok.oneclick_promotion import TikTokPromotionDispatchError
    request = _request('TH')
    calls, writes = _closed_edge(monkeypatch, 'TH', add_unknown=True)
    prepared = sh.prepare_postpublish_promotion(request)
    assert not writes
    command = prepared['command']
    intents = []
    request.command = {'payload': command}
    request.proof = {'payload': prepared['proof']}
    request.prepared_command_digest = sh._digest(command)
    request.progress_recorder = lambda *a, **k: intents.append(k['write_boundary'])
    with pytest.raises(TikTokPromotionDispatchError) as error:
        sh.dispatch_postpublish_promotion(request)
    assert error.value.dispatch_outcome_unknown is True
    assert intents == ['PRE_INVOCATION_INTENT'] and len(writes) == 1
    assert Decimal(str(writes[0]['item_list'][0]['model_list'][0]['model_promotion_price'])) == Decimal('408.80')
    assert len(calls) == 6  # Prepare and original pre-submit readback, no automatic resend.
