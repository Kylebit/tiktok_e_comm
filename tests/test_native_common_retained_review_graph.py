"""Owned original preparation/technical storage plus closed response bytes.

The transaction fixture replaces missing upstream grants explicitly. The native
service reader verifies retained local completion only, not official authority.
No human COMMON approval is fabricated and no provider is contacted.
"""
import json

import pytest

from shared_platform import publication_r3_image_bridge as bridge
from shared_platform import native_common_technical_execution as technical
from shared_platform.native_common_retained_completion import read_retained_completion
from shared_platform.r3_common_source_facts import NativeCommonSourceReader
from shared_platform.r3_frozen_review_producer import DomainFrozenReviewProducer, DomainReviewBlocked
from shared_platform.release_store import ReleaseAuthorizationError, PLAN_PENDING_APPROVAL
from test_round1_workspace_freeze import live
from test_native_common_technical_execution import _setup, _reserve, _consume, _retain_owned_comparison, _recover, _successor
from test_native_common_write_census import _normal_historical_evidence, _closed_comparison
from modules.products import release_adapters


def _completed(live, monkeypatch, tmp_path):
    _propose_market_inputs(live, monkeypatch)
    store, plan, payload, facts = _setup(live, monkeypatch, tmp_path)
    reserved = _reserve(store, facts)
    assert _consume(store, facts, reserved['run_id'])['consumed']
    _retain_owned_comparison(store, reserved['run_id'], _normal_historical_evidence(payload, monkeypatch))
    assert _recover(store, facts, reserved['run_id'])['state'] == 'CONFIRMED_WRITE'
    return store, plan, payload, facts, reserved['run_id']


def _propose_market_inputs(live, monkeypatch):
    """Supply proposed display inputs before real immutable preparation.

    Shopee categories still come from the real closed official capture; its
    EVIDENCE_BOUND receipt is never replaced by these proposed sidecar rows.
    """
    from copy import deepcopy
    from shared_platform import release_control
    import test_round1_sidecar_convergence as sidecar
    from modules.shopee import global_plan_candidate as channel
    fake = live['fake']
    original_get = fake.merchant_get
    fake.attribute_rows_by_category[101157] = deepcopy(fake.attribute_rows_by_category[101])
    def wall_sticker_capture(path, params):
        result = original_get(path, params)
        if path == channel.CATEGORY_RECOMMEND_PATH:
            result['response'][fake.recommendation_field] = [101157, 202]
        return result
    # Change the closed official recommendation/attribute source before any
    # capture or freeze, not the packet or its receipt/digests afterwards.
    monkeypatch.setattr(fake, 'merchant_get', wall_sticker_capture)
    original = sidecar.candidate
    def proposed(target):
        dashboard = release_control.build_release_dashboard(offer_id=live['offer'])
        labels = [label for label in dashboard['publication_scope']['selected_labels']
                  if label != 'miaoshou:COMMON']
        labels = [target] + [label for label in labels if label != target]
        result = original(target)
        template = result['target_candidates'][0]
        result['target_candidates'] = []
        for label in labels:
            row = deepcopy(template)
            row['target'] = label
            platform = label.split(':', 1)[0]
            row['category'].update(candidate=('17027906' if platform == 'ozon' else '600338') + ' · Wall Stickers',
                authority='OWNED_PROPOSED_DISPLAY_INPUT_NOT_OFFICIAL',
                evidence_digest=bridge._canonical_digest({'platform': platform, 'kind': 'owned proposed category'}))
            row['copy'].update(language={'tiktok:LH_MY':'ms', 'tiktok:MX':'es',
                'shopee:MY':'ms', 'ozon:RU':'ru'}.get(label, 'en'),
                title=dashboard['product']['title'],
                description='Decorative wall sticker for household decoration.')
            row['copy']['variants'][0]['seller_sku'] = dashboard['product']['seller_sku_candidate']
            result['target_candidates'].append(row)
        return result
    monkeypatch.setattr(sidecar, 'candidate', proposed)


def _market(store, plan, run_id):
    # Re-read the SAME actual R1/R2 documents; do not transplant another fixture.
    from test_b4b_release_compiler import policy, INCIDENTS
    documents = bridge.load_r2_documents(plan['product_id'])
    assert documents['round1_snapshot']['snapshot_digest'] == plan['payload']['r3_stage_binding']['round1_snapshot_digest']
    from shared_platform.ozon_runtime_credentials import PinnedOzonCredentials
    from shared_platform.ozon_stock_warehouse_decision import resolve_warehouse_decision
    # Ozon warehouse resolution uses the real producer with a closed fixture
    # response; this completes display facts, not a native account grant.
    warehouse = resolve_warehouse_decision(offer_id=plan['product_id'],
        round1=documents['round1_snapshot'], pinned=PinnedOzonCredentials('12345','owned-secret','a'*64),
        post_bound=lambda path, body, **kwargs: {'warehouses':[
            {'warehouse_id':71,'status':'active','is_kgt':False}]})
    material = bridge.build_marketplace_review_material(documents, store.get_plan(plan['plan_id']),
        store.get_run(run_id), policy=policy(40), incidents=INCIDENTS,
        ozon_stock_decision=warehouse['decision'], category_store=store)
    return store.create_plan(material['payload'])


def _inspect(store, market_id, run_id):
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        return DomainFrozenReviewProducer(NativeCommonSourceReader(store)).inspect(db, run_id, market_id)


def test_pending_technical_common_produces_complete_inert_final_display_without_another_approval(live, monkeypatch, tmp_path):
    store, plan, payload, facts, run_id = _completed(live, monkeypatch, tmp_path)
    market = _market(store, plan, run_id)
    before = store.path.read_bytes()
    value = _inspect(store, market['plan_id'], run_id)
    assert value['manifest']['variants'] and value['manifest']['copy_sets'] and value['manifest']['image_sets'], value
    assert value['targets'] == bridge.load_r2_documents(payload['product_id'])['round1_snapshot']['canonical_targets']
    assert [row['target_label'] for row in value['manifest']['targets']] == value['targets']
    assert value['status'] == 'BLOCKED' and value['execution_authority'] is False and value['final_review_available'] is False
    assert not {'nonce', 'approval_saved', 'review_digest'} & set(value)
    assert store.get_plan(plan['plan_id'])['status'] == store.get_plan(market['plan_id'])['status'] == PLAN_PENDING_APPROVAL
    with store._connect_readonly() as db:
        assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0] == 0
        db.execute('BEGIN')
        source = NativeCommonSourceReader(store).read_source_facts(db, market['plan_id'])
        assert source.graph.marketplace_plan_id == market['plan_id']
        assert source.graph.common_run_id == run_id
        assert source.graph.targets == tuple(value['targets'])
        with pytest.raises(DomainReviewBlocked, match='COMMON_TRUSTED_BUDGET_AND_OFFICIAL_READBACK_REQUIRED'):
            DomainFrozenReviewProducer(NativeCommonSourceReader(store)).build(db, run_id, market['plan_id'])
    assert store.path.read_bytes() == before


def test_native_market_source_reread_rejects_drift_without_recursive_graph_adoption(live, monkeypatch, tmp_path):
    store, plan, payload, facts, run_id = _completed(live, monkeypatch, tmp_path)
    market = _market(store, plan, run_id)
    with technical._existing_transaction(store) as db:
        db.execute('UPDATE release_target_readbacks SET evidence_json=? WHERE run_id=?', ('{}', run_id))
    before = store.path.read_bytes()
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        with pytest.raises(DomainReviewBlocked):
            NativeCommonSourceReader(store).read_source_facts(db, market['plan_id'])
    assert store.path.read_bytes() == before


def test_market_reference_cannot_be_followed_as_common_before_durable_origin_validation(live, monkeypatch, tmp_path):
    store, plan, payload, facts, run_id = _completed(live, monkeypatch, tmp_path)
    market = _market(store, plan, run_id)
    reader = NativeCommonSourceReader(store)
    original = reader.read_source_facts
    observed = []
    def observe_source(db, plan_id):
        observed.append(plan_id)
        return original(db, plan_id)
    monkeypatch.setattr(reader, 'read_source_facts', observe_source)
    before = store.path.read_bytes()
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        with pytest.raises(DomainReviewBlocked, match='^COMMON_TECHNICAL_COMPLETION_MISSING$'):
            read_retained_completion(reader, db, market['plan_id'], run_id)
    assert observed == [], 'Wrong COMMON origin must be rejected before following another market graph'
    assert store.path.read_bytes() == before


def test_native_display_does_not_accept_a_caller_reader_or_unfinished_technical_run(live, monkeypatch, tmp_path):
    store, plan, payload, facts = _setup(live, monkeypatch, tmp_path)
    reserved = _reserve(store, facts)
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        with pytest.raises(DomainReviewBlocked, match='COMMON_NATIVE_SOURCE_READER_REQUIRED'):
            read_retained_completion({'approved': True}, db, plan['plan_id'], reserved['run_id'])
        with pytest.raises(DomainReviewBlocked, match='COMMON_TECHNICAL_COMPLETION_UNPROVEN'):
            read_retained_completion(NativeCommonSourceReader(store), db, plan['plan_id'], reserved['run_id'])
    assert not _consume(store, facts, reserved['run_id']).get('state') == 'CONFIRMED_WRITE'


def test_retained_completion_wrong_store_and_changed_packet_are_rejected(live, monkeypatch, tmp_path):
    from shared_platform.release_store import ReleaseStore
    store, plan, payload, facts, run_id = _completed(live, monkeypatch, tmp_path)
    wrong = NativeCommonSourceReader(ReleaseStore(tmp_path/'never-created.sqlite3'))
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        with pytest.raises(DomainReviewBlocked):
            read_retained_completion(wrong, db, plan['plan_id'], run_id)
    assert not wrong.store.path.exists()
    with technical._existing_transaction(store) as db:
        db.execute('UPDATE release_target_readbacks SET evidence_json=? WHERE run_id=?', ('{}', run_id))
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        with pytest.raises((DomainReviewBlocked, ReleaseAuthorizationError)):
            read_retained_completion(NativeCommonSourceReader(store), db, plan['plan_id'], run_id)


def test_readonly_completion_rechecks_exact_durable_confirmed_predecessor(live, monkeypatch, tmp_path):
    store, plan, payload, facts, old_run_id = _completed(live, monkeypatch, tmp_path)
    new_plan, new_payload, new_facts = _successor(store, payload, 'graph-readonly-reuse')
    reused = _reserve(store, new_facts, readonly_reuse=True)
    source = _closed_comparison(new_payload, monkeypatch)
    old_target = store.get_run(old_run_id)['targets'][0]
    summary = {k:v for k,v in source.items() if k not in {'native_common_observation','stored_common_lineage'}}
    summary['predecessor'] = {'plan_id':plan['plan_id'], 'run_id':old_run_id, 'payload_digest':plan['payload_digest'],
        'common_status':old_target['status'], 'common_external_id':old_target['external_id'],
        'common_readback_evidence_digest':old_target['readback']['evidence_digest'],
        'common_readback_verified_at':old_target['readback']['verified_at']}
    _retain_owned_comparison(store, reused['run_id'], release_adapters.bind_native_common_readback(source, summary))
    assert _recover(store, new_facts, reused['run_id'])['state'] == 'READONLY_REUSE'
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        observation = read_retained_completion(NativeCommonSourceReader(store), db, new_plan['plan_id'], reused['run_id'])
        assert observation.state == 'READONLY_REUSE'
    with technical._existing_transaction(store) as db:
        db.execute('UPDATE release_target_readbacks SET evidence_json=? WHERE run_id=?', ('{}', old_run_id))
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        with pytest.raises((DomainReviewBlocked, ReleaseAuthorizationError)):
            read_retained_completion(NativeCommonSourceReader(store), db, new_plan['plan_id'], reused['run_id'])


def test_legacy_approved_full_graph_keeps_original_read_path(full_sources):
    # The existing pure original-page compiler fixture remains unmodified.
    from shared_platform.r3_frozen_review_producer import rebuild_domain_review_graph
    common, run, market = full_sources
    assert rebuild_domain_review_graph(common, run, market).marketplace_plan_id == market['plan_id']


from test_r3_frozen_review_producer import full_sources


@pytest.mark.parametrize('status', ['PENDING', 'SUPERSEDED', 'FAILED'])
def test_completed_native_graph_rejects_noncanonical_unapproved_plan_status(live, monkeypatch, tmp_path, status):
    from copy import deepcopy
    from shared_platform.r3_frozen_review_producer import rebuild_domain_review_graph
    store, plan, payload, facts, run_id = _completed(live, monkeypatch, tmp_path)
    market = _market(store, plan, run_id)
    common = store.get_plan(plan['plan_id'])
    run = store.get_run(run_id)
    assert common['status'] == PLAN_PENDING_APPROVAL
    before = store.path.read_bytes()
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        completion = read_retained_completion(NativeCommonSourceReader(store), db, plan['plan_id'], run_id)
        changed = deepcopy(common)
        changed['status'] = status
        with pytest.raises(DomainReviewBlocked, match='^COMMON_TECHNICAL_COMPLETION_UNPROVEN$'):
            rebuild_domain_review_graph(changed, run, market, native_completion=completion,
                category_store=store, category_connection=db)
    assert store.path.read_bytes() == before
    assert store.get_plan(plan['plan_id'])['status'] == PLAN_PENDING_APPROVAL
