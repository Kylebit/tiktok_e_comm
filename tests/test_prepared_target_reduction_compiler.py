"""Inert successor projection preserves history and cannot admit dispatch."""
from copy import deepcopy
import hashlib
import json

import pytest

from shared_platform.prepared_target_reduction import build_prepared_target_reduction, canonical_digest
from shared_platform import publication_r3_image_bridge as bridge
from owned_common_review_fixtures import historical_completed_context
from test_b4b_release_compiler import policy, INCIDENTS


def completed_run(store, plan):
    with store._connect_readonly() as db:
        row = db.execute('SELECT run_id FROM release_runs WHERE plan_id=?', (plan['plan_id'],)).fetchone()
    return store.get_run(row['run_id'])


def reduction_for(documents):
    identity = bridge.validate_r2_identity(documents)
    r1 = documents['round1_snapshot']
    targets = sorted(x for x in r1['canonical_targets'] if x != 'miaoshou:COMMON')
    remaining = [x for x in targets if x != 'ozon:RU']
    predecessor = {'common_collectbox_id': r1['offer_id'], 'product_center_revision': 2,
        'snapshot_digest': r1['snapshot_digest'], 'first_review_digest': identity['first_review_digest'],
        'R2_identity': identity, 'configured_targets':
            [{'target': x, 'configured_shop_id': 'fixture-' + x} for x in targets]}
    raw = json.dumps(predecessor, sort_keys=True).encode()
    scope = {'offer_id': r1['offer_id'], 'shops': targets, 'skus': []}
    request = {'schema': 'detached-product-task-target-reduction-request/v1', 'task_id': 'fixture-task',
        'scope_authority': {'schema': 'exact-product-scope-reduction-authority/v1',
            'common_collect_box_id': r1['offer_id'], 'user_message_id': 'fixture-authority',
            'user_message_at_utc': '2026-10-09T05:43:58Z', 'verbatim_user_instruction': 'skip Ozon',
            'received_via_parent_thread': 'fixture-coordinator',
            'removed_target': {'target': 'ozon:RU', 'configured_shop_id': 'fixture-ozon:RU',
                               'status': 'USER_CANCELLED_THIS_PRODUCT_ONLY'},
            'remaining_target_labels': remaining, 'remaining_target_count': len(remaining),
            'other_Ozon_business_changed': False, 'new_publication_approval_granted': False,
            'new_paid_or_refresh_authorization_granted': False},
        'predecessor_lineage': {'predecessor_target_labels': targets,
            'predecessor_target_count': len(targets), 'predecessor_product_center_revision': 2,
            'predecessor_review_file_sha256': hashlib.sha256(raw).hexdigest(),
            'predecessor_R1_snapshot_digest': r1['snapshot_digest'],
            'predecessor_first_review_digest': identity['first_review_digest'],
            'predecessor_R2_identity': identity, 'new_R1_or_R2_approval_or_digest_created': False},
        'desired_business_scope': {**scope, 'shops': remaining},
        'execution_authority': False, 'user_final_publication_approval': False,
        'business_writes': 0, 'paid_calls': 0, 'new_refreshes': 0}
    return build_prepared_target_reduction(request, task_id='fixture-task', scope=scope,
        version={'code_version': 'fixture-original'}, checkpoint_sha256='1' * 64,
        prepared_reference='r1-prepared:' + '2' * 64, predecessor_review_bytes=raw)


def test_inert_cancellation_rebuilds_retained_matrix_without_ozon_stock(tmp_path, monkeypatch):
    documents, _, store, request, io = historical_completed_context(tmp_path, monkeypatch)
    plan = store.get_plan(request['plan_id'])
    run = completed_run(store, plan)
    before = deepcopy((documents, plan, run))
    reduction = reduction_for(documents)
    result = bridge.build_marketplace_review_material(documents, plan, run,
        policy=policy(40), incidents=INCIDENTS, target_reduction=reduction)
    payload, candidate = result['payload'], result['candidate']
    assert payload['targets'] == ['tiktok:LH_PH']
    assert candidate['target_labels'] == ['tiktok:LH_PH']
    assert [r['target_label'] for r in candidate['review_manifest']['targets']] == ['tiktok:LH_PH']
    assert payload['r3_marketplace_binding']['documents'] == documents
    assert payload['r3_marketplace_binding']['target_reduction'] == reduction
    assert payload['r3_marketplace_binding']['r2_identity'] == bridge.validate_r2_identity(documents)
    assert (documents, plan, run) == before
    assert 'ozon_stock_decision' not in payload['product_facts']
    for mapping in (payload['product_facts']['categories_by_target'],
                    payload['product_facts']['content_by_target'], payload['pricing']['selected_targets'],
                    payload['localized_image_routing']['routes']):
        assert set(mapping) == {'tiktok:LH_PH'}
    assert io.mutations == 0
    # The current domain reader deliberately has no consumer-adoption path.
    from shared_platform.r3_frozen_review_producer import rebuild_domain_review_graph, DomainReviewBlocked
    from shared_platform.release_store import preview_release_plan
    with pytest.raises(DomainReviewBlocked, match='DOMAIN_PLAN_SOURCE_SCOPE_CHANGED'):
        rebuild_domain_review_graph(plan, run, preview_release_plan(payload))


@pytest.mark.parametrize('drift', ['identity', 'digest', 'offer'])
def test_compiler_rejects_cancellation_from_another_graph(tmp_path, monkeypatch, drift):
    documents, _, store, request, io = historical_completed_context(tmp_path, monkeypatch)
    plan = store.get_plan(request['plan_id'])
    run = completed_run(store, plan)
    reduction = reduction_for(documents)
    if drift == 'identity': reduction['predecessor_lineage']['predecessor_R2_identity']['qa_digest'] = 'sha256:' + '0' * 64
    if drift == 'offer': reduction['offer_id'] = 'another-offer'
    if drift == 'digest': reduction['reduction_digest'] = '0' * 64
    else:
        reduction['reduction_digest'] = canonical_digest({k:v for k,v in reduction.items() if k != 'reduction_digest'})
    with pytest.raises(ValueError, match='target reduction compiler lineage conflicts'):
        bridge.build_marketplace_review_material(documents, plan, run,
            policy=policy(40), incidents=INCIDENTS, target_reduction=reduction)
    assert io.mutations == 0


def test_quality_reconstruction_rejects_reintroduced_cancelled_target(tmp_path, monkeypatch):
    from domains.product_operations.approved_publication_snapshot import build_publication_preview
    documents, _, store, request, io = historical_completed_context(tmp_path, monkeypatch)
    plan = store.get_plan(request['plan_id'])
    payload = bridge.build_marketplace_stage_payload(documents, plan, completed_run(store, plan),
        policy=policy(40), incidents=INCIDENTS, target_reduction=reduction_for(documents))
    snapshot = build_publication_preview(payload)
    payload['targets'].append('ozon:RU')
    with pytest.raises(ValueError, match='MARKETPLACE_CANCELLED_TARGET_SCOPE_CHANGED'):
        bridge.marketplace_quality_evidence(snapshot, payload)
    assert io.mutations == 0
