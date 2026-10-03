"""Actual SAVE/publish consumers reject invalid warehouse evidence."""
from copy import deepcopy
import pytest
from domains.channel_operations.tiktok_publisher import TikTokPublisher
from domains.product_operations.sku_display_name import validate_specification_mapping
from modules.miaoshou.tiktok_publisher import MiaoshouTikTokTransport, WAREHOUSE_GET_PATH, SAVE_SITE_DRAFT_PATH, SAVE_SHOP_DRAFT_PATH, PUBLISH_PATH
from modules.miaoshou.tiktok_v4_drafts import MiaoshouOpenApiTikTokV4DraftTransport, _draft_payload
from test_tiktok_independent_publisher import _snapshot as publish_snapshot, FakeLowestTransport
from test_miaoshou_tiktok_v4_drafts import _snapshot, CategoryResolver, _editable_site_payload, SHOP_IDS


def _allocation(shop):
    return {'schema_version': 'miaoshou-tiktok-warehouse-allocation/v1', 'shop_id': shop,
        'warehouses': [{'warehouse_id': '90001', 'warehouse_name': 'Approved warehouse', 'stock': 300}],
        'total_stock': 300, 'source': 'CONVERSATION_APPROVAL', 'approved_by': 'Kyle',
        'approved_at': '2026-08-30T00:00:00+08:00'}


def _warehouse(shop, case):
    row = {'warehouseId': '90001', 'warehouseName': 'Approved warehouse', 'warehouseEffectStatus': '1'}
    group = {'shopId': shop, 'warehouseList': [row]}
    if case in ('zero', 'false', 'none', 'int-one'):
        row['warehouseEffectStatus'] = {'zero': 0, 'false': False, 'none': None, 'int-one': 1}[case]
    elif case == 'missing-status':
        row.pop('warehouseEffectStatus')
    elif case == 'missing-shop':
        group.pop('shopId')
    elif case == 'duplicate':
        group['warehouseList'].insert(0, dict(row, warehouseName='Different warehouse'))
    return {'result': 'success', 'data': {'shopWarehouseList': [group]}}


BAD = ['zero', 'false', 'none', 'missing-status', 'missing-shop', 'duplicate']


@pytest.mark.parametrize('case', BAD + ['string-one', 'int-one'])
def test_real_v4_draft_save_requires_exact_active_warehouse(case):
    snapshot = _snapshot()
    target = snapshot['publication_targets'][0]
    shop = SHOP_IDS[target['target_label']]
    draft = _draft_payload(snapshot, target=target, category=CategoryResolver().resolve(
        target=target, product=snapshot['product'], skus=snapshot['skus']))
    draft['warehouse_inventory'] = _allocation(shop)
    calls = []
    def post(path, body):
        calls.append((path, deepcopy(body)))
        if path.endswith('get_site_collect_item_info'):
            return _editable_site_payload(site=body['site'], revision='fixture')
        if path == WAREHOUSE_GET_PATH:
            return _warehouse(shop, case)
        return {'result': 'success', 'data': {}}
    transport = MiaoshouOpenApiTikTokV4DraftTransport(common_detail_id='5001', post=post,
        read_attempts=1, sleep=lambda _: None)
    identity = {'target_label': target['target_label'], 'shop_id': shop, 'detail_id': '7301'}
    def execute():
        prepared = transport.prepare_save_draft(identity=identity, draft=draft)
        return transport.save_prepared_draft(identity=identity, prepared=prepared)
    if case in BAD:
        try:
            execute()
        except ValueError:
            pass
        assert not any(path in {SAVE_SITE_DRAFT_PATH, SAVE_SHOP_DRAFT_PATH, PUBLISH_PATH} for path, _ in calls)
    else:
        execute()
        saved = [body for path, body in calls if path == SAVE_SITE_DRAFT_PATH]
        assert len(saved) == 1
        assert all(row['shopIdToWarehouseIdAndStockMap'][shop] == {'90001': '300'}
                   for row in saved[0]['siteCollectItemInfo']['skuMap'].values())


@pytest.mark.parametrize('case', BAD + ['string-one', 'int-one'])
def test_real_publisher_never_saves_or_submits_invalid_warehouse(case):
    snapshot = publish_snapshot(targets=('tiktok:LH_PH',))
    target = snapshot['targets'][0]
    target['expected_warehouse_inventory'] = _allocation(target['shop_id'])
    fake = FakeLowestTransport(snapshot)
    calls = []
    def post(path, body):
        calls.append((path, deepcopy(body)))
        if path == WAREHOUSE_GET_PATH:
            return _warehouse(target['shop_id'], case)
        return fake(path, body)
    TikTokPublisher(transport=MiaoshouTikTokTransport(post=post)).publish(snapshot)
    writes = [path for path, _ in calls if path in {SAVE_SITE_DRAFT_PATH, SAVE_SHOP_DRAFT_PATH, PUBLISH_PATH}]
    if case in BAD:
        assert writes == []
    else:
        assert SAVE_SITE_DRAFT_PATH in writes


@pytest.mark.parametrize('quantity', ['30', '3', '30 pcs', '3 pcs'])
def test_schema_accepted_quantity_survives_real_spec_consumer(quantity):
    approved = validate_specification_mapping({'size': '30 x 45 cm', 'quantity': quantity}, model_sku='0967')
    assert MiaoshouTikTokTransport._approved_variant_display(approved) == f'30 x 45 cm ({quantity})'
    snapshot = publish_snapshot(targets=('tiktok:LH_PH',))
    target = snapshot['targets'][0]
    target.update(expected_variant_model_skus={'v1': '0967'},
        expected_variant_specifications={'v1': approved},
        expected_sku_parcels={'v1': {'weight_kg': '0.1', 'package_cm': ['20', '20', '3']}},
        expected_sku_prices={'0967': target['expected_price']})
    fake = FakeLowestTransport(snapshot)
    def post(path, body):
        response = fake(path, body)
        if path.endswith('get_site_collect_item_info'):
            response['data']['siteCollectItemInfo']['skuPropertyList'] = [
                {'attrName': 'Size', 'attrValueList': [{'attrValueId': 'v1', 'attrValue': 'old size'}]}]
        return response
    transport = MiaoshouTikTokTransport(post=post)
    transport.save_approved_draft(target, transport.read_draft(target))
    saved = next(body for path, body in fake.calls if path == SAVE_SITE_DRAFT_PATH)
    info = saved['siteCollectItemInfo']
    assert info['skuPropertyList'][0]['attrValueList'][0]['attrValue'] == f'30 x 45 cm ({quantity})'
    assert info['skuMap']['v1']['specification'] == {'option': f'30 x 45 cm ({quantity})'}
