from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared_platform import postpublish_promotions as policy
from modules.tiktok import oneclick_promotion as adapter
from tests.test_oneclick_postpublish_promotions import _FixtureTransport, _prepare_request, _promotion_payload


def payload(percent=25):
    value = _promotion_payload()
    value['pricing']['selected_targets']['tiktok:LH_PH']['store_prices'][0].update(
        discount_reserve_pct=percent, sale_after_discount=200 * (100-percent)/100)
    return value


def test_frozen_percent_is_the_prepared_consumer_value():
    fixture = _FixtureTransport()
    adapter.configure_tiktok_promotion_transport_factory(fixture.transport)
    try:
        prepared = adapter.prepare_postpublish_promotion(_prepare_request(payload()))
        assert prepared['command']['discount_percent'] == 25
        assert prepared['proof']['discount_percent'] == 25
    finally:
        adapter.configure_tiktok_promotion_transport_factory(None)


@pytest.mark.parametrize('fault', ['missing_percent', 'missing_sale', 'wrong_sale', 'wrong_currency', 'fractional', 'nan', 'duplicate_row'])
def test_missing_or_inconsistent_frozen_price_cannot_dispatch(fault):
    value = payload()
    prices = value['pricing']['selected_targets']['tiktok:LH_PH']['store_prices']
    row = prices[0]
    if fault == 'missing_percent': row.pop('discount_reserve_pct')
    if fault == 'missing_sale': row.pop('sale_after_discount')
    if fault == 'wrong_sale': row['sale_after_discount'] += 1
    if fault == 'wrong_currency': row['currency'] = 'MYR'
    if fault == 'fractional': row['discount_reserve_pct'] = 25.5
    if fault == 'nan': row['discount_reserve_pct'] = 'NaN'
    if fault == 'duplicate_row': prices.append(deepcopy(row))
    fixture = _FixtureTransport()
    adapter.configure_tiktok_promotion_transport_factory(fixture.transport)
    try:
        with pytest.raises(ValueError):
            adapter.prepare_postpublish_promotion(_prepare_request(value))
        assert fixture.calls == []
    finally:
        adapter.configure_tiktok_promotion_transport_factory(None)


def test_legacy_approval_and_action_digest_remain_exact():
    captured = json.loads((Path(__file__).parent/'fixtures/b4b_legacy_promotion_v1.json').read_text())
    value = {'approved_postpublish_promotion_policy': captured['approval']}
    assert policy.approved_postpublish_promotion_policy(value) == captured['approval']
    assert policy.approved_promotion_action_policy(value, 'promotion:tiktok:LH_PH') == captured['action']
    assert policy.build_approved_postpublish_promotion_policy(approval_reference='new')['policy_version'].endswith('/v2')
    value['approved_postpublish_promotion_policy']['discounts']['tiktok'] = 25
    with pytest.raises(ValueError):
        policy.approved_postpublish_promotion_policy(value)


@pytest.mark.parametrize('membership', ['exact', 'conflict', 'duplicate'])
def test_membership_readback_prevents_rewrite(membership):
    fixture = _FixtureTransport()
    original = fixture.activity
    def activity(activity_id, query):
        result = original(activity_id, query)
        rows = [{'id': 'product-1', 'discount': '25' if membership != 'conflict' else '15'}]
        if membership == 'duplicate': rows.append(deepcopy(rows[0]))
        result['data']['products'] = rows
        return result
    fixture.activity = activity
    adapter.configure_tiktok_promotion_transport_factory(fixture.transport)
    try:
        prepared = adapter.prepare_postpublish_promotion(_prepare_request(payload()))
        progress = []
        request = SimpleNamespace(target_label='promotion:tiktok:LH_PH', command={'payload': prepared['command']},
            proof={'payload': prepared['proof']}, progress_recorder=lambda *a, **kw: progress.append(kw), prepared_command_digest='c'*64)
        if membership == 'exact':
            result = adapter.dispatch_postpublish_promotion(request)
            assert result['canonical_status'] == 'SUCCEEDED'
            assert result['readback_verified'] is True
            assert result['external_write_count'] == 0
        else:
            with pytest.raises(adapter.TikTokPromotionPreDispatchError):
                adapter.dispatch_postpublish_promotion(request)
        assert progress == []
        assert not [call for call in fixture.calls if call[0] == 'put']
    finally:
        adapter.configure_tiktok_promotion_transport_factory(None)


@pytest.mark.parametrize('mode', ['success', 'timeout', 'accepted_readback_failed', 'existing_exact'])
def test_production_promotion_registration_records_real_adapter_outcome_and_replay(tmp_path, mode):
    from domains.channel_operations.oneclick_release_adapters import production_adapter_registry
    from shared_platform.oneclick_release_controlplane import OneClickReleaseStore, OneClickReleaseWorker, DispatchTargetResult
    from shared_platform.release_store import ReleaseStore
    from tests.test_oneclick_release_controlplane import _registry
    targets = ['miaoshou:COMMON', 'tiktok:LH_PH']
    value = payload()
    release = ReleaseStore(tmp_path/'release.db')
    created = release.create_plan(value)
    release.approve_plan(created['plan_id'], approved_by='Kyle', user_approved=True, confirmation_token=created['confirmation_token'])
    plan = release.get_plan(created['plan_id'])
    run = release.start_run(created['plan_id'])
    def primary(request):
        return DispatchTargetResult(canonical_status='SUCCEEDED', reason_category='CAPABILITY', reason_scope='TARGET',
            reason_code='fixture_readback', reason_detail='Fixture primary readback', external_writes=(f'{request.target_label}:write',),
            external_id='product-1', submission_accepted=True, readback_verified=True, evidence={'checks': {'identity': True}})
    registry = _registry(targets, dispatch_override=primary)
    registry['postpublish_promotion'] = production_adapter_registry()['postpublish_promotion']
    fixture = _FixtureTransport(put_mode='timeout' if mode == 'timeout' else 'success')
    original = fixture.activity
    def activity(activity_id, query):
        result = original(activity_id, query)
        if mode == 'accepted_readback_failed' and fixture.written:
            raise TimeoutError('fixture readback failed after accepted PUT')
        if fixture.written or mode == 'existing_exact':
            result['data']['products'] = [{'id':'product-1', 'discount':'25'}]
        return result
    fixture.activity = activity
    adapter.configure_tiktok_promotion_transport_factory(fixture.transport)
    try:
        control = OneClickReleaseStore(release.path)
        job = control.ensure_job(plan=plan, run=run, product_revision=31, registry=registry)
        worker = OneClickReleaseWorker(control, lambda: registry, dispatch_enabled=lambda: True)
        for _ in range(8):
            if not worker.advance_once(job['job_id']): break
        action = control.get_job(job_id=job['job_id'])['postpublish_actions'][0]
        expected = None if mode == 'timeout' else (0 if mode == 'existing_exact' else 1)
        assert action['dispatch_ledger']['cumulative_external_write_count'] == expected, action
        assert action['result']['external_write_count'] == expected
        if mode in ('success', 'existing_exact'):
            assert action['status'] == 'SUCCEEDED'
        elif mode == 'timeout':
            assert action['status'] == 'RECONCILIATION_REQUIRED'
        else:
            assert action['status'] != 'SUCCEEDED'
        before = len([c for c in fixture.calls if c[0] == 'put'])
        assert before == (0 if mode == 'existing_exact' else 1)
        restarted = OneClickReleaseStore(release.path)
        assert OneClickReleaseWorker(restarted, lambda: registry, dispatch_enabled=lambda: True).advance_once(job['job_id']) is False
        assert len([c for c in fixture.calls if c[0] == 'put']) == before
        assert all(row['status'] == 'SUCCEEDED' for row in release.get_run(run['run_id'])['targets'])
    finally:
        adapter.configure_tiktok_promotion_transport_factory(None)


def test_price_override_evidence_survives_real_preview_without_nested_alias():
    from domains.channel_operations.pricing_preview import build_channel_pricing_preview
    from tests.test_channel_pricing_preview import _row
    row = _row('lh_ph', 'LivelyHive', 'PH', 'PHP', 200)
    row.update(discount_reserve_pct=25, sale_after_discount_local=150,
        pricing_authority=' frozen-plan ', price_override={'proof': {'digest': 'fixture-proof'}})
    preview = build_channel_pricing_preview({'sea':[row]}, selected_site_keys=['lh_ph'], shopee_exchange_rates={}, ozon_exchange_rates={})
    rows = [preview['selected_store_prices'][0], preview['all_legacy_store_prices'][0], preview['master_price_source'],
            preview['target_pricing']['miaoshou:COMMON']['store_prices'][0], preview['target_pricing']['tiktok:PH']['store_prices'][0]]
    assert all(r['pricing_authority'] == 'frozen-plan' for r in rows)
    assert all(r['price_override']['proof']['digest'] == 'fixture-proof' for r in rows)
    row['price_override']['proof']['digest'] = 'changed'
    rows[0]['price_override']['proof']['digest'] = 'projection-change'
    assert all(r['price_override']['proof']['digest'] == 'fixture-proof' for r in rows[1:])


@pytest.mark.parametrize('fault', ['missing_evidence', 'activity', 'product', 'percent', 'digest', 'missing_count', 'pending_unknown', 'accepted_prefix'])
def test_zero_write_membership_cannot_erase_unmatched_identity_or_prior_writes(tmp_path, fault):
    from dataclasses import replace
    from domains.channel_operations.oneclick_release_adapters import production_adapter_registry
    from shared_platform.oneclick_release_controlplane import OneClickReleaseStore, OneClickReleaseWorker, DispatchTargetResult, AdapterContractError
    from tests.test_oneclick_postpublish_promotions import _approved_promotion_context
    from tests.test_oneclick_release_controlplane import _registry
    targets = ['miaoshou:COMMON', 'tiktok:LH_PH']
    release, plan, run = _approved_promotion_context(tmp_path, targets)
    def primary(request):
        return DispatchTargetResult(canonical_status='SUCCEEDED', reason_category='CAPABILITY', reason_scope='TARGET',
            reason_code='fixture_readback', reason_detail='Fixture primary readback', external_writes=(f'{request.target_label}:write',),
            external_id='product-1', submission_accepted=True, readback_verified=True, evidence={'checks':{'identity':True}})
    registry = _registry(targets, dispatch_override=primary)
    registry['postpublish_promotion'] = production_adapter_registry()['postpublish_promotion']
    fixture = _FixtureTransport(); fixture.written = True
    adapter.configure_tiktok_promotion_transport_factory(fixture.transport)
    try:
        control = OneClickReleaseStore(release.path)
        job = control.ensure_job(plan=plan, run=run, product_revision=31, registry=registry)
        worker = OneClickReleaseWorker(control, lambda: registry, dispatch_enabled=lambda: True)
        for _ in range(4): assert worker.advance_once(job['job_id']) is True
        request = control.claim_next_dispatch(job['job_id'], registry)
        request = replace(request, progress_recorder=control.record_dispatch_progress)
        result = dict(adapter.dispatch_postpublish_promotion(request))
        if fault == 'missing_evidence': result['evidence'] = {}
        if fault in ('activity','product'): result['evidence'][fault+'_identity_digest'] = 'b'*64
        if fault == 'percent': result['evidence']['discount_percent'] = 25
        if fault == 'digest': result['evidence']['evidence_digest'] = 'c'*64
        if fault == 'missing_count': result['external_write_count'] = None
        if fault in ('pending_unknown','accepted_prefix'):
            control.record_dispatch_progress(request, (adapter.TIKTOK_PROMOTION_WRITE_CLASS,), 'promotion_apply-1',
                {'phase':'intent'}, None, 0, 1, 'PRE_INVOCATION_INTENT')
            if fault == 'accepted_prefix':
                control.record_dispatch_progress(request, (adapter.TIKTOK_PROMOTION_WRITE_CLASS,), 'promotion_apply-1',
                    {'phase':'accepted'}, 1, 1, 1, 'POST_RESPONSE_CONFIRMED')
        with pytest.raises(AdapterContractError):
            control.record_dispatch_result(request, DispatchTargetResult.from_value(result))
        assert control.get_job(job_id=job['job_id'])['postpublish_actions'][0]['status'] != 'SUCCEEDED'
        assert not [c for c in fixture.calls if c[0]=='put']
    finally:
        adapter.configure_tiktok_promotion_transport_factory(None)
