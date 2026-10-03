from copy import deepcopy

import pytest

from domains.product_operations import approved_publication_snapshot as snapshots
from shared_platform import publication_autopilot as autopilot
from test_approved_publication_snapshot import _approved_plan
from test_b4b_release_compiler import policy, INCIDENTS


def test_marketplace_preview_preserves_v2_discount_from_frozen_r1(tmp_path, monkeypatch):
    from shared_platform.postpublish_promotions import approved_promotion_action_policy
    _, _, _, _, io, market = reviewed_marketplace(tmp_path, monkeypatch)
    payload = market['plan']['payload']
    policy = approved_promotion_action_policy(payload, 'promotion:tiktok:LH_PH')
    assert policy['policy_version'] == 'oneclick-postpublish-promotion/v2'
    assert policy['discount_percent'] == 10
    assert payload['pricing']['selected_targets']['tiktok:LH_PH']['store_prices'][0]['sale_after_discount'] == '18'
    assert io.mutations == 1


def governed_files(tmp_path, monkeypatch):
    import json
    policy_path = tmp_path / 'policy.json'
    policy_path.write_text(json.dumps(policy(4)), encoding='utf-8')
    incidents_path = tmp_path / 'incidents.json'
    incidents_path.write_text(json.dumps(INCIDENTS), encoding='utf-8')
    monkeypatch.setattr(autopilot, 'POLICY_PATH', policy_path)
    monkeypatch.setattr(autopilot, 'INCIDENT_REGISTRY_PATH', incidents_path)
    from modules.products import server
    from shared_platform.publication_runtime_config import capture_startup_config
    monkeypatch.setattr(server, 'R3_STARTUP_CONFIG', capture_startup_config(root=tmp_path, environ={
        'ORBIT_R3_POLICY_PATH': 'policy.json', 'ORBIT_R3_INCIDENT_REGISTRY_PATH': 'incidents.json'}))


def test_named_preview_has_no_approval_and_is_rejected_by_existing_consumers():
    payload = _approved_plan()['payload']
    before = deepcopy(payload)
    preview = snapshots.build_publication_preview(payload)
    assert preview['schema_version'] == 'publication-snapshot-preview/v1'
    assert preview['status'] == 'NOT_APPROVED'
    assert not {'approved_at', 'approved_by', 'snapshot_digest'} & set(preview)
    assert payload == before
    assert snapshots.validate_publication_preview(preview) == preview
    with pytest.raises(ValueError):
        snapshots.validate_approved_publication_snapshot(preview)
    with pytest.raises(ValueError):
        autopilot.compile_release_candidate(preview, policy=policy(), incident_registry=INCIDENTS)
    candidate = autopilot.compile_release_preview(preview, policy=policy(), incident_registry=INCIDENTS)
    assert candidate['schema_version'] == 'publication-candidate-preview/v1'
    assert candidate['approval_status'] == 'NOT_APPROVED'
    assert candidate['status'] in {'PREVIEW_READY', 'PREVIEW_BLOCKED'}
    with pytest.raises(ValueError):
        autopilot.build_final_approval_receipt(candidate, approved_by='Kyle')
    with pytest.raises(ValueError):
        autopilot.validate_release_candidate_for_execution(candidate, snapshot=preview,
            platform_scope=('TIKTOK',), target_labels=('tiktok:LH_PH',))


@pytest.mark.parametrize('field', ['title', 'cost', 'assignment'])
def test_preview_validates_real_v4_facts(field):
    payload = _approved_plan()['payload']
    if field == 'title':
        payload['product_facts']['title'] = ''
    elif field == 'cost':
        key = payload['product_facts']['selected_sku_keys'][0]
        payload['product_facts']['sku_commercial_facts'][key]['cost'] = {'amount': '-1', 'currency': 'CNY'}
    else:
        payload['sku_lineage']['assignment']['model_skus'][0]['variant_key'] = 'wrong'
    with pytest.raises(ValueError):
        snapshots.build_publication_preview(payload)


def test_marketplace_preview_is_actual_common_consumer(tmp_path, monkeypatch):
    from modules.products import server
    from test_b4b_common_stage import context, CommonTransport, approve
    _, _, store, request = context(tmp_path, monkeypatch)
    governed_files(tmp_path, monkeypatch)
    io = CommonTransport(monkeypatch)
    status, blocked = server._preview_r3_marketplace_stage({'offer_id': request['offer_id']})
    assert status == 409 and io.mutations == 0
    approved = approve(request)
    assert server._prepare_miaoshou_release(dict(approved, confirm_miaoshou_write=True))[0] == 200
    status, result = server._preview_r3_marketplace_stage({'offer_id': request['offer_id']})
    assert status == 409 and result['error'] == 'OZON_EXACT_STOCK_WAREHOUSE_DECISION_REQUIRED', result
    assert result['marketplace']['status'] == 'BLOCKED'
    assert io.mutations == 1
    assert store.active_plan_for_product(request['offer_id'])['plan_id'] == approved['plan_id']


def test_legacy_two_unit_stock_snapshot_cannot_acquire_ozon_review(tmp_path, monkeypatch):
    from modules.products import server
    from test_b4b_common_stage import approve
    _, _, store, request, io = multivariant_context(tmp_path, monkeypatch, modern_stock=False)
    approved = approve(request)
    assert server._prepare_miaoshou_release(dict(approved, confirm_miaoshou_write=True))[0] == 200
    status, result = server._preview_r3_marketplace_stage({'offer_id': request['offer_id']})
    assert status == 409 and result['error'] == 'OZON_EXACT_STOCK_WAREHOUSE_DECISION_REQUIRED'
    assert io.mutations == 1
    assert store.active_plan_for_product(request['offer_id'])['plan_id'] == approved['plan_id']


def test_changed_ozon_warehouse_receipt_cannot_enter_final_review(tmp_path, monkeypatch):
    import json
    from modules.products import server
    from shared_platform import publication_r3_image_bridge as bridge
    from test_b4b_common_stage import approve
    _, _, store, request, io = multivariant_context(tmp_path, monkeypatch)
    approved = approve(request)
    assert server._prepare_miaoshou_release(dict(approved, confirm_miaoshou_write=True))[0] == 200
    receipt_path = bridge.REPORTS_ROOT / request['offer_id'] / 'ozon-warehouse-readback.json'
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    receipt['decision']['warehouse_id'] = 72
    receipt_path.write_text(json.dumps(receipt), encoding='utf-8')
    status, result = server._preview_r3_marketplace_stage({'offer_id': request['offer_id']})
    assert status == 409 and result['error'] == 'OZON_WAREHOUSE_RECEIPT_IDENTITY_DRIFTED', result
    assert io.mutations == 1
    assert store.active_plan_for_product(request['offer_id'])['plan_id'] == approved['plan_id']


def multivariant_context(tmp_path, monkeypatch, *, modern_stock=True, synthetic_technical_authority=True):
    from pathlib import Path
    import json
    from test_b4b_common_stage import context, CommonTransport
    fixture = Path(__file__).parent / 'fixtures/b4b_common_multivariant_actual'
    documents, dashboard, store, request = context(tmp_path, monkeypatch, fixture=fixture)
    if modern_stock:
        from shared_platform import publication_r3_image_bridge as bridge
        from shared_platform.publication_rounds import canonical_digest
        from shared_platform.publication_stock_policy import default_publication_stock_policy
        r1 = documents['round1_snapshot']
        original_digest = r1['snapshot_digest']
        stock = default_publication_stock_policy()
        r1['publication_stock_policy'] = stock
        r1['fact_snapshot']['publication_stock_policy'] = stock
        r1['snapshot_digest'] = canonical_digest({key: value for key, value in r1.items() if key != 'snapshot_digest'})
        replacement = r1['snapshot_digest']
        for key, name in bridge.R2_DOCUMENTS.items():
            value = json.loads(json.dumps(documents[key]).replace(original_digest, replacement))
            if key == 'image_qa':
                value['qa_digest'] = canonical_digest({part: row for part, row in value.items() if part != 'qa_digest'})
            documents[key] = value
            (bridge.REPORTS_ROOT / request['offer_id'] / name).write_text(json.dumps(value), encoding='utf-8')
        revised_dashboard = json.loads(json.dumps(dashboard).replace(original_digest, replacement))
        dashboard.clear()
        dashboard.update(revised_dashboard)
    governed_files(tmp_path, monkeypatch)
    if modern_stock:
        from shared_platform import publication_r3_image_bridge as bridge
        from shared_platform import ozon_runtime_credentials as credentials
        from shared_platform.ozon_runtime_credentials import PinnedOzonCredentials
        from shared_platform.ozon_stock_warehouse_decision import resolve_warehouse_decision
        pinned = PinnedOzonCredentials('12345', 'synthetic-secret', 'a' * 64)
        monkeypatch.setattr(credentials, 'required_pinned_ozon_credentials', lambda: pinned)
        receipt = resolve_warehouse_decision(offer_id=request['offer_id'],
            round1=documents['round1_snapshot'], pinned=pinned,
            post_bound=lambda path, body, **kwargs: {'warehouses': [
                {'warehouse_id': 71, 'status': 'active', 'is_kgt': False}]})
        (bridge.REPORTS_ROOT / request['offer_id'] / 'ozon-warehouse-readback.json').write_text(
            json.dumps(receipt), encoding='utf-8')
    from shared_platform.product_publication_runs import ProductPublicationRunStore
    from test_product_publication_runner import _report_store
    from modules.products import server
    runs = ProductPublicationRunStore(tmp_path / 'async-runs.db')
    reports = _report_store(tmp_path)
    monkeypatch.setattr(server, '_product_publication_run_store', lambda: runs)
    monkeypatch.setattr(server, '_product_publication_report_store', lambda: reports)
    io = CommonTransport(monkeypatch, synthetic_technical_authority=synthetic_technical_authority)
    io.detail['skuMap'] = {'red': {'itemNum': 'old-red', 'specLabel': 'Red'},
                           'blue': {'itemNum': 'old-blue', 'specLabel': 'Blue'}}
    return documents, dashboard, store, request, io


def test_actual_multivariant_producer_common_to_final_preview(tmp_path, monkeypatch):
    from modules.products import server
    from test_b4b_common_stage import approve
    _, _, store, request, io = multivariant_context(tmp_path, monkeypatch)
    approved = approve(request)
    status, result = server._prepare_miaoshou_release(dict(approved, confirm_miaoshou_write=True))
    assert status == 200, result
    assert {key: value['itemNum'] for key, value in io.detail['skuMap'].items()} == {'blue': '095202', 'red': '095201'}
    status, result = server._preview_r3_marketplace_stage({'offer_id': request['offer_id']})
    assert status == 200, result
    candidate = result['marketplace']['preview']
    assert candidate['status'] == 'READY_FOR_FINAL_REVIEW', candidate['blockers']
    assert io.mutations == 1


def reviewed_marketplace(tmp_path, monkeypatch):
    # Existing report/projection fixtures describe old normalized private
    # history. They must not dispatch a new EDIT through the retired writer.
    from owned_common_review_fixtures import historical_reviewed_marketplace
    return historical_reviewed_marketplace(tmp_path, monkeypatch)


def seed_preexisting_marketplace_approval(store, data, market):
    """Restore a historical approved fixture; never exercise the new approval route."""
    from shared_platform import publication_r3_image_bridge as bridge
    from modules.products import server
    status, unredacted = server._preview_r3_marketplace_stage({'offer_id': data['offer_id']})
    assert status == 200
    market = unredacted['marketplace']
    autopilot.persist_release_candidate(market['preview'], reports_root=bridge.REPORTS_ROOT)
    created = store.create_plan(market['plan']['payload'])
    return store.approve_plan(data['plan_id'], user_approved=True,
                              approved_by='Kyle',
                              confirmation_token=created['confirmation_token'])


def test_one_final_decision_binds_existing_store_and_final_authority(tmp_path, monkeypatch):
    from modules.products import server
    from shared_platform import publication_r3_image_bridge as bridge
    _, _, store, data, io, market = reviewed_marketplace(tmp_path, monkeypatch)
    status, result = server._approve_r3_marketplace_stage(data)
    assert status == 409 and result['error'] == 'POST_COMMON_FINAL_REVIEW_BLOCKED'
    assert store.get_plan(data['plan_id']) is None
    seed_preexisting_marketplace_approval(store, data, market)
    status, result = server._approve_r3_marketplace_stage(data)
    assert status == 200, result
    plan = store.get_plan(data['plan_id'])
    snapshot = store.approved_publication_snapshot(offer_id=data['offer_id'], plan_id=data['plan_id'])
    candidate, receipt = autopilot.resolve_persisted_execution_authority(snapshot=snapshot,
        platform_scope=('TIKTOK', 'OZON'), target_labels=('tiktok:LH_PH', 'ozon:RU'), reports_root=bridge.REPORTS_ROOT)
    assert plan['status'] == 'APPROVED'
    assert candidate == market['preview']
    assert receipt['candidate_digest'] == market['preview']['candidate_digest']
    assert receipt['snapshot_digest'] == market['preview']['snapshot_digest']
    assert receipt['approved_at'] == plan['approval']['approved_at']
    assert server._approve_r3_marketplace_stage(data)[0] == 200
    assert io.mutations == 1


@pytest.mark.parametrize('drift', ['preview', 'facts', 'common_receipt'])
def test_final_decision_rechecks_review_and_common_before_store_approval(tmp_path, monkeypatch, drift):
    from modules.products import server
    _, dashboard, store, data, io, _ = reviewed_marketplace(tmp_path, monkeypatch)
    if drift == 'preview':
        data['preview_digest'] = 'a' * 64
    elif drift == 'facts':
        dashboard['product']['cost_cny'] = '9'
    else:
        import sqlite3
        with sqlite3.connect(store.path) as db:
            db.execute('DELETE FROM release_target_readbacks')
    assert server._approve_r3_marketplace_stage(data)[0] == 409
    assert store.get_plan(data['plan_id']) is None
    assert io.mutations == 1


@pytest.mark.parametrize('failure_point', ['persist_release_candidate', 'persist_final_approval_receipt'])
def test_approved_binding_recovers_from_original_payload_after_current_drift(tmp_path, monkeypatch, failure_point):
    from modules.products import server
    from shared_platform import publication_r3_image_bridge as bridge
    _, dashboard, store, data, io, market = reviewed_marketplace(tmp_path, monkeypatch)
    real_persist = getattr(autopilot, failure_point)
    def lose_response(*args, **kwargs):
        raise OSError('synthetic interrupted derived receipt')
    if failure_point == 'persist_final_approval_receipt':
        seed_preexisting_marketplace_approval(store, data, market)
    monkeypatch.setattr(autopilot, failure_point, lose_response)
    status, result = server._approve_r3_marketplace_stage(data)
    assert status == 409, result
    persisted_plan = store.get_plan(data['plan_id'])
    if failure_point == 'persist_release_candidate':
        assert persisted_plan is None
        assert result['error'] == 'POST_COMMON_FINAL_REVIEW_BLOCKED'
        assert io.mutations == 1
        return
    assert persisted_plan['status'] == 'APPROVED'
    snapshot = store.approved_publication_snapshot(offer_id=data['offer_id'], plan_id=data['plan_id'])
    with pytest.raises(ValueError):
        autopilot.resolve_persisted_execution_authority(snapshot=snapshot, platform_scope=('TIKTOK',),
            target_labels=('tiktok:LH_PH',), reports_root=bridge.REPORTS_ROOT)
    dashboard['product']['cost_cny'] = '9'
    monkeypatch.setattr(autopilot, failure_point, real_persist)
    status, result = server._resume_r3_marketplace_stage({'offer_id': data['offer_id'], 'plan_id': data['plan_id']})
    assert status == 200, result
    assert io.mutations == 1
