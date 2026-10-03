"""Consumer regressions: frozen regional copy and exact Ozon category ancestry."""
from copy import deepcopy
from dataclasses import replace
import json
import socket
import hashlib

import pytest

from modules.products import release_adapters
from modules.shopee import global_copy, skill_regions
from modules.ozon.approved_publication_v4 import OzonDispatchFact, execute_ozon_v4_publication
from shared_platform.release_store import ReleaseStore
from test_shopee_skill_regions import FakeRuntime, _snapshot as regional_snapshot
from test_ozon_approved_publication_v4 import _snapshot as ozon_snapshot
from test_product_release_adapters import _context, _request


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('B4B synthetic consumer tests forbid real network')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


def _paid_probe(monkeypatch):
    calls = []
    def paid(*args, **kwargs):
        calls.append('paid-translation')
        return json.dumps({'title': 'ชื่อสินค้าไทยสำหรับการทดสอบที่ได้รับการอนุมัติ',
                           'description': 'รายละเอียดสินค้าไทยสำหรับการทดสอบ ' * 30}, ensure_ascii=False)
    monkeypatch.setattr(global_copy, '_ai_chat', paid)
    return calls


def _region_writes(runtime):
    return sum(len(getattr(runtime, name)) for name in (
        'create_calls', 'list_calls', 'logistic_enable_calls', 'copy_update_calls',
        'image_upload_calls', 'image_update_calls'))


@pytest.mark.parametrize('region', ['MY', 'TH', 'VN'])
def test_v4_missing_frozen_region_copy_blocks_before_create(monkeypatch, region):
    paid = _paid_probe(monkeypatch)
    runtime = FakeRuntime()
    result = skill_regions.dispatch_selected_regions(
        _missing_copy_snapshot('shopee:' + region), global_item_id='60000001', runtime=runtime)
    assert (len(paid), _region_writes(runtime)) == (0, 0)
    assert result['targets'][0]['outcome'] == 'NOT_ATTEMPTED'
    assert 'prepare-product-publication' in result['targets'][0]['message']


def test_v4_existing_item_missing_copy_cannot_pay_or_repair(monkeypatch):
    paid = _paid_probe(monkeypatch)
    runtime = FakeRuntime()
    runtime.localize_regional_copy = lambda context, **kw: global_copy.localize_shopee_copy(region=context.region, **kw)
    snapshot = _missing_copy_snapshot('shopee:TH')
    dispatch = {'targets': [{'target_label': 'shopee:TH', 'accepted': True,
                             'existing_item_id': '8103', 'selected_logistics_ids': [2001, 2002]}]}
    skill_regions.readback_dispatched_regions(snapshot, dispatch, global_item_id='60000001', runtime=runtime)
    assert (len(paid), _region_writes(runtime)) == (0, 0)


def test_legacy_persisted_plan_missing_copy_cannot_pay_or_repair(tmp_path, monkeypatch):
    from core import config
    monkeypatch.setattr(config, '_cache', {})
    payload = deepcopy(_context()['payload'])
    payload.update(plan_id='legacy-copy-guard', targets=['shopee:TH'], omnichannel_scope_digest='scope')
    payload['listing_copy'] = {'candidates': [{'channel': 'shopee', 'site': 'CNSC',
        'title': 'Approved English Shopee master', 'policy_check': 'passed'}],
        'shopee_description_en': 'Approved product description. ' * 30}
    payload['pricing']['selected_targets'] = {'shopee:TH': {'target_site': 'TH', 'derived_preview': {
        'global_original_price_cny': 42.59, 'local_original_price': 192,
        'source_currency': 'THB', 'exchange_rate_cny_per_local': 0.2218}}}
    store = ReleaseStore(tmp_path / 'release.sqlite')
    plan = store.create_plan(payload)
    store.approve_plan(plan['plan_id'], approved_by='Kyle', user_approved=True,
                       confirmation_token=plan['confirmation_token'])
    run = store.start_run(plan['plan_id'])
    request = replace(_request(), plan_id=plan['plan_id'], confirmation_token=plan['confirmation_token'],
                      channel='shopee', site='TH', target_label='shopee:TH',
                      idempotency_key=run['targets'][0]['idempotency_key'])
    monkeypatch.setattr(release_adapters, 'default_release_store', lambda: store)
    monkeypatch.setattr(release_adapters, '_shopee_item_id_for_match_key', lambda *a, **k: '8103')
    monkeypatch.setattr(release_adapters, '_shopee_readback', lambda **k: (False, {
        'checks': {'localized_title': False, 'rich_localized_description': False}}))
    monkeypatch.setattr('modules.shopee.auth.ensure_shop_token', lambda _: 'fixture')
    monkeypatch.setattr('modules.shopee.publish.sync_shop_ids', lambda: {'TH': 103})
    writes = []
    monkeypatch.setattr('modules.shopee.publish.shop_post', lambda *a, **k: writes.append(a) or {'error': '', 'response': {}})
    monkeypatch.setattr('modules.shopee.publish.enable_all_applicable_logistics', lambda *a: {})
    monkeypatch.setattr('modules.shopee.publish._item_base_info', lambda *a: {
        'item_name': 'ชื่อสินค้าไทยสำหรับการทดสอบที่ได้รับการอนุมัติ',
        'description': 'รายละเอียดสินค้าไทยสำหรับการทดสอบ ' * 30})
    paid = _paid_probe(monkeypatch)
    try:
        result = release_adapters.execute_shopee_target(request)
    except (ValueError, RuntimeError):
        # Even a later provider/readback failure must not hide an earlier paid/write call.
        assert (len(paid), len(writes)) == (0, 0)
        raise
    assert (len(paid), len(writes)) == (0, 0)
    assert result.succeeded is False
    assert 'prepare-product-publication' in result.detail


def _profile():
    return {'schema_version': 'ozon-official-profile-resolution/v1', 'resolution': 'EXACT',
        'description_category_id': 17028913, 'category_name': 'Souvenirs and Gifts',
        'category_path': [{'id': '14500', 'name': 'Home'},
                          {'id': '17028913', 'name': 'Souvenirs and Gifts'}],
        'type_id': 93785, 'type_name': 'Fridge Magnet',
        'required_attributes': {'brand': {'attribute_id': 85, 'dictionary_value_id': 126745801, 'value': 'No Brand'},
        'model_name': {'attribute_id': 9048},
        'product_type': {'attribute_id': 8229, 'dictionary_value_id': 93785, 'value': 'Fridge Magnet'}}}


@pytest.mark.parametrize('ancestor', ['wrong-parent', '14500'])
def test_ozon_type_display_cannot_override_conflicting_ancestor(ancestor):
    snapshot = ozon_snapshot()
    category = snapshot['categories_by_target']['ozon:RU']['category']
    category['name'] = 'Souvenirs and Gifts > Fridge Magnet'
    category['path'] = [{'id': ancestor, 'name': 'Home'},
                        {'id': category['id'], 'name': category['name']}]
    writes = []
    def dispatch(payload):
        writes.append(payload)
        return OzonDispatchFact(outcome='ACCEPTED', task_id='fixture')
    def execute():
        return execute_ozon_v4_publication(snapshot, target_labels=('ozon:RU',), dispatch_variant=dispatch,
            readback_variants=lambda _: [], official_profile_resolver=lambda _: _profile(),
            localized_copy_resolver=lambda value: {'schema_version': 'ozon-localized-copy/v1',
                'source_snapshot_digest': value['snapshot_digest'], 'language': 'ru',
                'title': 'Магнит на холодильник', 'description': 'Описание магнита на холодильник'})
    if ancestor == 'wrong-parent':
        result = execute()
        assert result['targets'][0]['status'] == 'FAILED'
    else:
        execute()
    assert len(writes) == (3 if ancestor == '14500' else 0)


def _missing_copy_snapshot(target):
    snapshot = regional_snapshot(target)
    snapshot['product'].pop('content_by_target', None)
    return snapshot


@pytest.mark.parametrize('upload_count', [0, 2])
def test_global_receipt_counts_actual_missing_uploads(upload_count):
    from test_shopee_global_v4_executor import _Runtime, _request
    from modules.shopee.global_v4_executor import ShopeeGlobalV4Resolver
    runtime = _Runtime()
    upload = runtime.checkpointed_upload_global_images
    def checkpointed(*args):
        bindings, _ = upload(*args)
        return bindings, upload_count
    runtime.checkpointed_upload_global_images = checkpointed
    request = _request()
    resolver = ShopeeGlobalV4Resolver(runtime=runtime)
    assert resolver(request) == '9001'
    assert resolver.write_count(request) == upload_count + 2


def test_local_budget_denial_is_zero_write_not_unknown(tmp_path):
    from types import SimpleNamespace
    from test_shopee_global_v4_executor import _Runtime, _request
    from modules.shopee.global_v4_executor import ShopeeGlobalV4Resolver
    from shared_platform.publication_write_budget import PublicationWriteBudgetLedger, PublicationWriteBudgetExceeded
    base = _request()
    ledger = PublicationWriteBudgetLedger(platform='SHOPEE', target_labels=base.target_labels,
        budget={'shared_maximum': 0, 'per_target_maximum': 0})
    request = SimpleNamespace(run_id=base.run_id, report_id=base.report_id, snapshot=base.snapshot, platform=base.platform,
        target_labels=base.target_labels, write_budget_ledger=ledger)
    runtime = _Runtime()
    live, uploads = _live_upload_fixture(request, tmp_path)
    runtime.checkpointed_upload_global_images = live.checkpointed_upload_global_images
    resolver = ShopeeGlobalV4Resolver(runtime=runtime)
    with pytest.raises(PublicationWriteBudgetExceeded):
        resolver(request)
    assert runtime.calls == ['lookup', 'prepare']
    assert resolver.write_count(request) == 0
    assert uploads == []


def test_region_create_local_budget_denial_never_reports_a_transport_attempt():
    from shared_platform.publication_write_budget import PublicationWriteBudgetExceeded
    runtime = FakeRuntime()
    def reject(*_):
        raise PublicationWriteBudgetExceeded('fixture cap')
    result = skill_regions.dispatch_selected_regions(regional_snapshot('shopee:PH'),
        global_item_id='60000001', runtime=runtime, before_mutation=reject)
    row = result['targets'][0]
    assert runtime.create_calls == []
    assert row['outcome'] == 'NOT_ATTEMPTED'
    assert row['attempted'] is False
    assert row['external_write_count'] == 0


@pytest.mark.parametrize('cap', [0, 1, 2])
def test_region_budget_denial_retains_prior_actual_http_writes(cap):
    from shared_platform.publication_write_budget import PublicationWriteBudgetLedger
    runtime = FakeRuntime()
    runtime.existing_items['TH'] = '8103'
    snapshot = regional_snapshot('shopee:TH')
    dispatch = skill_regions.dispatch_selected_regions(snapshot, global_item_id='60000001', runtime=runtime)
    ledger = PublicationWriteBudgetLedger(platform='SHOPEE', target_labels=('shopee:TH',),
        budget={'shared_maximum': 0, 'per_target_maximum': cap})
    result = skill_regions.readback_dispatched_regions(snapshot, dispatch, global_item_id='60000001',
        runtime=runtime, before_mutation=ledger.reserve_target)
    row = result['targets'][0]
    actual = len(runtime.copy_update_calls) + len(runtime.description_media_calls) + len(runtime.list_calls)
    assert actual == cap
    assert row['external_write_count'] == cap
    assert row['outcome'] == ('NOT_ATTEMPTED' if cap == 0 else 'FAILED')
    assert row['verified'] is False


def _live_upload_fixture(request, root):
    from modules.shopee.global_v4_live_runtime import OfficialShopeeGlobalV4Runtime
    from modules.shopee.global_v4_executor import project_shopee_global_v4_command
    uploads = []
    def upload(url, position):
        # The reservation must precede the actual lowest upload boundary.
        assert request.write_budget_ledger.shared_attempt_count == len(uploads) + 1
        uploads.append(url)
        return 'uploaded-' + str(position)
    runtime = OfficialShopeeGlobalV4Runtime(context_resolver=lambda _: {},
        official_fact_reader=lambda *_: {}, mapping_lookup=lambda _: None,
        image_upload_transport=upload, checkpoint_root=root)
    runtime.lookup_global_item_ids(project_shopee_global_v4_command(request.snapshot))
    return runtime, uploads


@pytest.mark.parametrize('cap', [0, 1, 2])
def test_official_upload_budget_counts_each_missing_http_and_reuses_cache(tmp_path, cap):
    from types import SimpleNamespace
    from test_shopee_global_v4_executor import _request
    from shared_platform.publication_write_budget import PublicationWriteBudgetLedger, PublicationWriteBudgetExceeded
    base = _request()
    ledger = PublicationWriteBudgetLedger(platform='SHOPEE', target_labels=base.target_labels,
        budget={'shared_maximum': cap, 'per_target_maximum': 0})
    request = SimpleNamespace(run_id=base.run_id, report_id=base.report_id, snapshot=base.snapshot,
        write_budget_ledger=ledger, release_candidate={'fixture': True})
    runtime, uploads = _live_upload_fixture(request, tmp_path)
    images = tuple(base.snapshot['product']['images'])
    assert len(images) == 2
    if cap < 2:
        with pytest.raises(PublicationWriteBudgetExceeded) as caught:
            runtime.checkpointed_upload_global_images(request, images)
        assert caught.value.completed_image_upload_count == cap
    else:
        bindings, count = runtime.checkpointed_upload_global_images(request, images)
        assert count == 2 and len(bindings) == 2
        request.write_budget_ledger = PublicationWriteBudgetLedger(platform='SHOPEE', target_labels=base.target_labels,
            budget={'shared_maximum': 0, 'per_target_maximum': 0})
        cached, count = runtime.checkpointed_upload_global_images(request, images)
        assert cached == bindings and count == 0
        assert request.write_budget_ledger.shared_attempt_count == 0
    assert len(uploads) == cap
    assert ledger.shared_attempt_count == cap


def test_missing_localized_copy_blocks_global_master_before_any_provider_call():
    from types import SimpleNamespace
    from test_shopee_global_v4_executor import _request, _Runtime
    from modules.shopee.global_v4_executor import ShopeeGlobalV4Resolver
    base = _request()
    # Relabel a complete synthetic approved PH contract to a complete MY fixture.
    snapshot = json.loads(json.dumps(base.snapshot).replace('shopee:PH', 'shopee:MY')
        .replace('"PH"', '"MY"').replace('"PHP"', '"MYR"'))
    source = snapshot['shopee_global_master']['price_source']
    source['source_binding_digest'] = 'sha256:' + hashlib.sha256(json.dumps({
        'schema_version': 'shopee-global-master-price-source/v1',
        'target_label': source['target_label'], 'region': source['region'],
        'target_key': source['target_key']}, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()
    snapshot.pop('snapshot_digest')
    snapshot['snapshot_digest'] = 'sha256:' + hashlib.sha256(json.dumps(snapshot,
        ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    request = SimpleNamespace(run_id=base.run_id, report_id=base.report_id, snapshot=snapshot,
        platform='SHOPEE', target_labels=('shopee:MY',))
    runtime = _Runtime()
    resolver = ShopeeGlobalV4Resolver(runtime=runtime)
    with pytest.raises(skill_regions.ShopeeRegionContractError, match='prepare-product-publication'):
        resolver(request)
    assert runtime.calls == []
    assert resolver.write_count(request) == 0


@pytest.mark.parametrize('cap', [0, 1, 2])
def test_regional_exact_upload_reserves_every_http_after_download(monkeypatch, cap):
    from modules.shopee import publish
    from shared_platform.publication_write_budget import PublicationWriteBudgetLedger, PublicationWriteBudgetExceeded
    ledger = PublicationWriteBudgetLedger(platform='SHOPEE', target_labels=('shopee:MY',),
        budget={'shared_maximum': 0, 'per_target_maximum': cap})
    uploads = []
    downloads = []
    def download(url, path):
        downloads.append(url)
        path.write_bytes(b'fixture-image')
    def upload(path, *, scene):
        assert ledger.target_attempt_counts['shopee:MY'] == len(uploads) + 1
        uploads.append(scene)
        return {'image_info': {'image_id': 'fixture-' + str(len(uploads))}}
    monkeypatch.setattr(publish, '_download_image', download)
    monkeypatch.setattr(publish, 'upload_image', upload)
    runtime = skill_regions.OfficialShopeeRegionRuntime()
    callback = lambda: ledger.reserve_target('shopee:MY', 'upload_regional_image')
    if cap < 2:
        with pytest.raises(PublicationWriteBudgetExceeded) as caught:
            runtime.upload_regional_images(FakeRuntime().context('MY'),
                ('https://fixture.invalid/one', 'https://fixture.invalid/two'), before_upload=callback)
        assert caught.value.completed_image_upload_count == cap
    else:
        result = runtime.upload_regional_images(FakeRuntime().context('MY'),
            ('https://fixture.invalid/one', 'https://fixture.invalid/two'), before_upload=callback)
        assert result['external_write_count'] == 2
    assert len(uploads) == cap
    assert uploads == ['normal', 'desc'][:cap]
    assert len(downloads) == min(cap + 1, 2)


@pytest.mark.parametrize('failure_stage,index,completed,attempts,unknown', [
    ('download', 1, 0, 0, False), ('download', 2, 1, 1, False),
    ('response', 1, 0, 1, True), ('response', 2, 1, 2, True),
])
def test_exact_upload_failure_preserves_prior_evidence(monkeypatch, failure_stage, index, completed, attempts, unknown):
    from modules.shopee import publish
    from shared_platform.publication_write_budget import PublicationWriteBudgetLedger
    ledger = PublicationWriteBudgetLedger(platform='SHOPEE', target_labels=('shopee:MY',),
        budget={'shared_maximum': 0, 'per_target_maximum': 2})
    downloads, uploads = [], []
    def download(url, path):
        downloads.append(url)
        if failure_stage == 'download' and len(downloads) == index:
            raise OSError('fixture download failed before mutation')
        path.write_bytes(b'fixture')
    def upload(path, *, scene):
        uploads.append(scene)
        if failure_stage == 'response' and len(uploads) == index:
            return {'response_without_image_identity': True}
        return {'image_info': {'image_id': 'fixture-' + str(len(uploads))}}
    monkeypatch.setattr(publish, '_download_image', download)
    monkeypatch.setattr(publish, 'upload_image', upload)
    with pytest.raises((OSError, RuntimeError)) as caught:
        skill_regions.OfficialShopeeRegionRuntime().upload_regional_images(FakeRuntime().context('MY'),
            ('https://fixture.invalid/one', 'https://fixture.invalid/two'),
            before_upload=lambda: ledger.reserve_target('shopee:MY', 'upload_regional_image'))
    assert caught.value.completed_image_upload_count == completed
    assert caught.value.image_upload_request_count == attempts
    assert caught.value.image_upload_outcome_unknown is unknown
    assert len(uploads) == attempts == ledger.total_attempt_count
