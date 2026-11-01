"""No provider calls: original warehouse resolver/matcher and route preflight."""
from copy import deepcopy

import pytest

from modules.miaoshou.tiktok_publisher import (
    EXPECTED_SHOP_ID_BY_TARGET, MiaoshouTikTokTransport, WAREHOUSE_GET_PATH,
)
from modules.miaoshou.tiktok_warehouses import mainland_pickup_warehouse_semantic
from shared_platform.native_tiktok_exact_readback import NativeTikTokExactStorefrontReadback
from shared_platform.publication_execution_preflight import inventory_readback_checks
from shared_platform.publication_stock_policy import default_publication_stock_policy


def snapshot(labels=None):
    return {
        'product': {'stock_policy': default_publication_stock_policy()},
        'publication_targets': [
            {'platform': 'TIKTOK', 'target_label': label}
            for label in labels or EXPECTED_SHOP_ID_BY_TARGET
        ],
    }


def checks(value):
    blocked = []
    result = inventory_readback_checks(value, lambda code, target: blocked.append((code, target)))
    return result, blocked


def test_default_policy_uses_real_runtime_resolution_and_never_asserts_provider_inventory():
    value = snapshot()
    before = deepcopy(value)
    result, blocked = checks(value)
    assert not blocked
    assert value == before and 'warehouse_inventory_by_target' not in value['product']
    assert len(result) == 10
    for row in result:
        label = row['target_label']
        assert row['status'] == 'SUPPORTED_RUNTIME_EXACT_RESOLUTION'
        assert row['provider_access'] == 'NOT_OBSERVED'
        assert row['runtime_resolution_required'] is True
        assert row['shop_id'] == EXPECTED_SHOP_ID_BY_TARGET[label]
        assert row['warehouse_semantic'] == mainland_pickup_warehouse_semantic(label)
        assert row['quantity_per_sku'] == 200
        assert row['prewrite_authority'] == 'MIAOSHOU_COMPLETE_ACTIVE_WAREHOUSES_AND_EXACT_SAVED_DRAFT'
        assert row['official_inventory_verified'] is False
        if label.startswith('tiktok:LH_'):
            assert row['completion_route'] == 'OFFICIAL_EXACT_STOREFRONT_READBACK'
            assert row['missing_zero_rows'] == 'UNPROVEN_NOT_INFERRED'
        else:
            assert row['completion_route'] == 'MIAOSHOU_ACCEPTED_PROCESSING_ONLY'
    assert NativeTikTokExactStorefrontReadback.local_observation_contract()['omitted_zero_quantity_proven'] is False


@pytest.mark.parametrize('policy', [None, {}, dict(default_publication_stock_policy(), quantity_per_sku=201),
    dict(default_publication_stock_policy(), quantity_per_sku=True)])
def test_missing_or_changed_default_stock_policy_blocks(policy):
    value = snapshot(['tiktok:LH_PH'])
    value['product']['stock_policy'] = policy
    result, blocked = checks(value)
    assert blocked == [('TIKTOK_FROZEN_STOCK_POLICY_REQUIRED', 'tiktok:LH_PH')]
    assert result[0]['status'] == 'BLOCKED'


def test_unknown_or_misregistered_semantic_blocks(monkeypatch):
    _, blocked = checks(snapshot(['tiktok:UNKNOWN']))
    assert ('TIKTOK_EXACT_SHOP_WAREHOUSE_SEMANTIC', 'tiktok:UNKNOWN') in blocked
    from modules.miaoshou import tiktok_warehouses
    monkeypatch.setitem(tiktok_warehouses.MAINLAND_PICKUP_WAREHOUSE_SEMANTIC_BY_TARGET, 'tiktok:LH_PH', '')
    _, blocked = checks(snapshot(['tiktok:LH_PH']))
    assert ('TIKTOK_EXACT_SHOP_WAREHOUSE_SEMANTIC', 'tiktok:LH_PH') in blocked


def allocation(label, *, shop=None, total=200):
    return {'schema_version': 'miaoshou-tiktok-warehouse-allocation/v1',
        'shop_id': shop or EXPECTED_SHOP_ID_BY_TARGET[label],
        'warehouses': [{'warehouse_id': '910001', 'warehouse_name': 'Owned pickup', 'stock': total},
            {'warehouse_id': '910002', 'warehouse_name': 'Owned local', 'stock': 0}],
        'total_stock': total, 'source': 'CONVERSATION_APPROVAL',
        'approved_by': 'OWNED_OFFLINE_ONLY', 'approved_at': '2026-11-01T00:00:00Z'}


def test_explicit_zero_row_is_neither_missing_nor_provider_verified():
    label = 'tiktok:LH_PH'; value = snapshot([label])
    value['product']['warehouse_inventory_by_target'] = {label: allocation(label)}
    result, blocked = checks(value)
    assert not blocked
    assert result[0]['status'] == 'SUPPORTED_EXPLICIT_COMPLETE_ROWS'
    assert result[0]['official_inventory_verified'] is False
    assert result[0]['missing_zero_rows'] == 'UNPROVEN_NOT_INFERRED'
    assert result[0]['runtime_resolution_required'] is True


@pytest.mark.parametrize('change', ['cross_shop', 'total_drift', 'coverage', 'malformed_rows'])
def test_explicit_allocation_cannot_change_shop_total_or_complete_scope(change):
    label = 'tiktok:LH_PH'; value = snapshot([label]); row = allocation(label)
    value['product']['warehouse_inventory_by_target'] = {label: row}
    if change == 'cross_shop': row['shop_id'] = EXPECTED_SHOP_ID_BY_TARGET['tiktok:LH_MY']
    if change == 'total_drift': row.update(total_stock=201); row['warehouses'][0]['stock'] = 201
    if change == 'coverage': value['product']['warehouse_inventory_by_target'] = {}
    if change == 'malformed_rows': row['warehouses'][1]['stock'] = False
    result, blocked = checks(value)
    code = {'cross_shop': 'TIKTOK_WAREHOUSE_SHOP_IDENTITY_CHANGED',
        'total_drift': 'TIKTOK_STOCK_POLICY_ALLOCATION_CONFLICT',
        'coverage': 'TIKTOK_EXPLICIT_WAREHOUSE_ALLOCATION_INVALID',
        'malformed_rows': 'TIKTOK_EXPLICIT_WAREHOUSE_ALLOCATION_INVALID'}[change]
    assert (code, label) in blocked and result[0]['status'] == 'BLOCKED'


@pytest.mark.parametrize('label', ['tiktok:LH_PH', 'tiktok:HB_TH', 'tiktok:MX', 'tiktok:GB'])
def test_actual_existing_resolver_and_matcher_require_complete_current_shop_rows(label):
    shop = EXPECTED_SHOP_ID_BY_TARGET[label]
    semantic = mainland_pickup_warehouse_semantic(label)
    calls = []
    def post(path, body):
        calls.append((path, body))
        assert path == WAREHOUSE_GET_PATH and body == {'shopIds': [shop]}
        return {'data': {'shopWarehouseList': [{'shopId': shop, 'warehouseList': [
            {'warehouseId': '910001', 'warehouseName': semantic, 'warehouseEffectStatus': '1'},
            {'warehouseId': '910002', 'warehouseName': 'Owned local', 'warehouseEffectStatus': '1'},
        ]}]}}
    transport = MiaoshouTikTokTransport(post=post)
    info = {'skuMap': {'owned-key': {'stock': 200}}}
    explicit, pickup, zero_ids, total = transport._warehouse_allocation(info, shop_id=shop)
    assert explicit is None and total is None and pickup == '910001' and zero_ids == ('910002',)
    row = {'stock': 200, 'shopIdToWarehouseIdAndStockMap': {shop: {pickup: '200', '910002': '0'}}}
    kwargs = dict(shop_id=shop, mainland_warehouse_id=pickup, zero_stock_warehouse_ids=zero_ids)
    assert transport._warehouse_inventory_matches({'owned-key': row}, **kwargs)
    row['shopIdToWarehouseIdAndStockMap'][shop].pop('910002')
    assert not transport._warehouse_inventory_matches({'owned-key': row}, **kwargs)
    assert calls == [(WAREHOUSE_GET_PATH, {'shopIds': [shop]})]
