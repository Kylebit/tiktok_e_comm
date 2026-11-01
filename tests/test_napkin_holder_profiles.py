"""Current public taxonomy replay; no credentials, provider calls or business DB."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from shared_platform.product_publication_live_dependencies import (
    CATEGORY_TREE_PATH, CATEGORY_METADATA_PATH, LivePublicationDependencyError,
    OfficialMiaoshouTikTokCategoryResolver, OfficialOzonFridgeMagnetProfileResolver,
    build_ozon_import_item_from_frozen_variant,
)


def facts():
    return json.loads(Path(__file__).with_name('fixtures').joinpath(
        'napkin_holder_official_20261101.json').read_text(encoding='utf-8'))


def tiktok_post(shop):
    retained = facts()['tiktok']; calls = []
    def post(path, body):
        calls.append((path, deepcopy(body)))
        if path == CATEGORY_TREE_PATH:
            assert body == {'site': shop['site']}
            nodes = retained['sites'][shop['site']]['nodes']
            tree = {}
            for node in reversed(nodes):
                tree = {str(node['cid']): {**deepcopy(node), 'children': tree}}
            return {'result': 'success', 'data': {'cateTree': tree}}
        assert path == CATEGORY_METADATA_PATH
        assert body == {'site': shop['site'], 'cid': 852872, 'shopIds': [int(shop['shop_id'])]}
        return {'result': 'success', 'data': {'categoryMetadata': deepcopy(shop['metadata'])}}
    return post, calls


@pytest.mark.parametrize('label', [r['target_label'] for r in facts()['tiktok']['shops']])
def test_desktop_napkin_resolves_current_exact_shop_storage_leaf(label):
    shop = next(r for r in facts()['tiktok']['shops'] if r['target_label'] == label)
    post, calls = tiktok_post(shop)
    site = label.split(':', 1)[1]
    result = OfficialMiaoshouTikTokCategoryResolver(post=post).resolve(
        target={'target_label': label, 'platform': 'tiktok', 'site': site, 'store': site},
        product={'main_category': {'name': 'Desktop napkin holder'}}, skus=[])
    assert result['category']['id'] == '852872'
    assert result['category']['path'][-1]['id'] == '852872'
    assert result['enabled'] is True and result['resolution'] == 'EXACT'
    assert len(calls) == 2


def ozon_snapshot():
    return {'schema_version': 'approved-publication-snapshot/v4',
        'product': {'main_category': {'name': 'Desktop napkin holder'}},
        'categories_by_target': {'ozon:RU': {'category': {
            'id': '17027907', 'name': 'Cooking Accessories', 'type_id': 92404}}}}


def ozon_replay(data=None):
    retained = deepcopy(data or facts()['ozon']); calls = []
    def post(path, body):
        calls.append((path, deepcopy(body)))
        if path == '/v1/description-category/tree':
            node = {'type_id': retained['type_id'], 'type_name': retained['type_name'],
                    'disabled': retained['disabled'], 'children': []}
            for ancestor in reversed(retained['category_path'][:-1]):
                node = {'description_category_id': int(ancestor['id']), 'category_name': ancestor['name'],
                        'disabled': False, 'children': [node]}
            return {'result': [node]}
        assert (body['description_category_id'], body['type_id']) == (17027907, 92404)
        if path == '/v1/description-category/attribute':
            return {'result': deepcopy(retained['attributes'])}
        assert path == '/v1/description-category/attribute/values/search'
        return deepcopy(retained['dictionaries'][str(body['attribute_id'])])
    return OfficialOzonFridgeMagnetProfileResolver(post=post), calls


def napkin_variant(profile):
    return {'offer_id': '1234', 'approved_seller_sku': '1234', 'title': 'Деревянная салфетница',
        'description': 'Настольная подставка для сложенных салфеток.',
        'images': ['https://fixture.example/napkin.jpg'],
        'parcel': {'weight_kg': '0.3', 'package_cm': ['15', '13', '10']},
        'category': {'id': '17027907', 'name': 'Cooking Accessories', 'type_id': 92404},
        'currency': 'CNY', 'price': '78', 'old_price': '90', 'official_profile': profile}


@pytest.mark.parametrize('semantic', ['Desktop napkin holder', 'Napkin Holder', '桌面餐巾纸架'])
def test_napkin_official_profile_and_actual_import_builder_preserve_frozen_facts(semantic):
    snapshot = ozon_snapshot(); snapshot['product']['main_category']['name'] = semantic
    before = deepcopy(snapshot); resolver, calls = ozon_replay()
    profile = resolver(snapshot)
    item = build_ozon_import_item_from_frozen_variant(napkin_variant(profile))
    assert (item['description_category_id'], item['type_id']) == (17027907, 92404)
    attrs = {a['id']: a['values'] for a in item['attributes']}
    assert attrs[8229] == [{'dictionary_value_id': 92404, 'value': 'Napkin Holder'}]
    assert attrs[9048][0]['value'] == '1234-napkin-holder'
    assert (item['weight'], item['depth'], item['width'], item['height']) == (300, 150, 130, 100)
    assert snapshot == before and len(calls) == 4


@pytest.mark.parametrize('mutation', ['wrong_type', 'disabled', 'extra_required', 'missing_model', 'wrong_dictionary'])
def test_napkin_wrong_official_profile_or_required_metadata_is_rejected(mutation):
    retained = facts()['ozon']
    if mutation == 'wrong_type': retained['type_id'] = 92011
    elif mutation == 'disabled': retained['disabled'] = True
    elif mutation == 'extra_required': retained['attributes'].append({'id': 999, 'is_required': True})
    elif mutation == 'missing_model': retained['attributes'] = [a for a in retained['attributes'] if a['id'] != 9048]
    else: retained['dictionaries']['8229']['result'] = [{'id': 92011, 'value': 'Toilet Paper Holder'}]
    resolver, _ = ozon_replay(retained)
    with pytest.raises(LivePublicationDependencyError): resolver(ozon_snapshot())


def test_napkin_does_not_accept_toilet_type_in_frozen_category():
    snapshot = ozon_snapshot(); snapshot['categories_by_target']['ozon:RU']['category']['type_id'] = 92011
    resolver, calls = ozon_replay()
    with pytest.raises(LivePublicationDependencyError): resolver(snapshot)
    assert calls == []


def test_native_preflight_actual_consumer_accepts_new_napkin_category_without_provider():
    from shared_platform.native_new_task_v4_targets import local_execution_capabilities
    from test_tiktok_v4_execution import _snapshot
    snapshot = _snapshot()
    # Native marketplace preflight runs after COMMON and deliberately excludes
    # that predecessor. The reusable lower-level snapshot fixture includes it.
    snapshot['publication_targets'] = [r for r in snapshot['publication_targets']
                                       if r['target_label'] != 'miaoshou:COMMON']
    snapshot['product']['main_category'] = {'name': 'Desktop napkin holder'}
    for label, row in snapshot['categories_by_target'].items():
        if label.startswith('tiktok:'):
            row['category'] = {'id': '852872', 'name': 'Storage Holders & Racks', 'path': []}
        elif label.startswith('ozon:'):
            row['category'] = ozon_snapshot()['categories_by_target']['ozon:RU']['category']
    candidate = {'target_labels': [r['target_label'] for r in snapshot['publication_targets']]}
    result = local_execution_capabilities(snapshot, candidate)
    assert all(r['local_adapter_supported'] for r in result['targets']), json.dumps(result)
    assert result['execution_authority'] is False


def test_current_gb_single_enum_producer_remains_bound_to_its_current_metadata():
    from modules.miaoshou.tiktok_v4_drafts import MiaoshouOpenApiTikTokV4DraftTransport
    shop = next(r for r in facts()['tiktok']['shops'] if r['target_label'] == 'tiktok:GB')
    post, calls = tiktok_post(shop)
    transport = MiaoshouOpenApiTikTokV4DraftTransport(common_detail_id='999', post=post)
    attributes = transport._gb_required_attributes(shop_id=shop['shop_id'], category_id='852872')
    assert attributes[0]['attributeId'] == '102255'
    assert attributes[0]['attributeValues'][0]['valueId'] == '1000256'
    assert len(calls) == 1


def test_new_napkin_mapping_keeps_legacy_toilet_semantics_separate():
    from shared_platform.product_publication_live_dependencies import _tiktok_profile
    assert _tiktok_profile({'main_category': {'name': 'Toilet paper holder'}})['primary'] == '853256'
    assert _tiktok_profile({'main_category': {'name': '纸巾架'}})['primary'] == '853256'
    assert _tiktok_profile({'main_category': {'name': 'Desktop napkin holder'}})['primary'] == '852872'
    with pytest.raises(LivePublicationDependencyError):
        OfficialOzonFridgeMagnetProfileResolver._profile_ids({**ozon_snapshot(),
            'product': {'main_category': {'name': 'Toilet paper holder'}}})


@pytest.mark.parametrize('field', ['category_type', 'type_dictionary', 'brand_dictionary'])
def test_napkin_import_still_rejects_conflicting_frozen_type_or_dictionary(field):
    resolver, _ = ozon_replay()
    variant = napkin_variant(resolver(ozon_snapshot()))
    if field == 'category_type': variant['category']['type_id'] = 92011
    else:
        key = 'product_type' if field == 'type_dictionary' else 'brand'
        variant['official_profile']['required_attributes'][key]['dictionary_value_id'] = 92011
    with pytest.raises(LivePublicationDependencyError):
        build_ozon_import_item_from_frozen_variant(variant)
