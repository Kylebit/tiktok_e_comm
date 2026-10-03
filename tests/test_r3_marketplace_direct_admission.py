"""Real Handler POST counterexamples for the original final-review endpoint."""

from copy import deepcopy
import hashlib
import json
import sqlite3

import pytest

from modules.products import server
from shared_platform import publication_autopilot as authority
from shared_platform import publication_post_common_admission as admission_gate
from shared_platform import publication_r3_image_bridge as bridge
from test_b4b_publication_preview import (
    reviewed_marketplace, seed_preexisting_marketplace_approval,
)
from test_r3_config_diagnostics import handler_request


@pytest.mark.parametrize('path', [
    '/api/product-workspace/r3-marketplace/approve',
    '/api/product-workspace/r3-marketplace/resume-binding',
])
def test_identity_only_http_approval_cannot_write_final_receipt(tmp_path, monkeypatch, path):
    _, _, store, data, io, market = reviewed_marketplace(tmp_path, monkeypatch)
    seed_preexisting_marketplace_approval(store, data, market)
    previous = store.get_plan(data['plan_id'])['approval']
    receipt_path = authority.final_approval_receipt_path(
        market['preview'], reports_root=bridge.REPORTS_ROOT)
    assert not receipt_path.exists()

    status, result = handler_request(path, data)

    assert status == 409, result
    assert result['error'] == 'MARKETPLACE_BUSINESS_EXECUTION_DISABLED'
    assert result['execution_authority'] is False
    assert result['external_writes_performed'] == []
    assert not receipt_path.exists()
    assert store.get_plan(data['plan_id'])['approval'] == previous
    assert io.mutations == 1


@pytest.mark.parametrize('runtime,allowed', [
    ({'state': 'READY', 'identity_only': True,
      'business_execution_verified': True}, False),
    ({'state': 'READY', 'identity_only': False,
      'business_execution_verified': False}, False),
    ({'state': 'READY', 'identity_only': False}, False),
    ({'state': 'UNKNOWN', 'identity_only': False,
      'business_execution_verified': True}, False),
    ({'state': 'READY', 'identity_only': False,
      'business_execution_verified': True}, True),
])
def test_http_approval_requires_explicit_verified_business_mode(
        tmp_path, monkeypatch, runtime, allowed):
    from shared_platform import runtime_identity

    _, _, store, data, io, market = reviewed_marketplace(tmp_path, monkeypatch)
    seed_preexisting_marketplace_approval(store, data, market)
    receipt_path = authority.final_approval_receipt_path(
        market['preview'], reports_root=bridge.REPORTS_ROOT)
    assert not receipt_path.exists()
    monkeypatch.setattr(runtime_identity, 'health_payload', lambda *a, **k: runtime)

    status, result = handler_request('/api/product-workspace/r3-marketplace/approve', data)

    assert status == (200 if allowed else 409), result
    assert receipt_path.exists() is allowed
    assert store.get_plan(data['plan_id'])['status'] == 'APPROVED'
    if not allowed:
        assert result['error'] == 'MARKETPLACE_BUSINESS_EXECUTION_DISABLED'
        assert result['external_writes_performed'] == []
    assert io.mutations == 1


def test_enabled_http_approval_still_requires_ready_post_common_admission(tmp_path, monkeypatch):
    from shared_platform import runtime_identity

    _, _, store, data, io, market = reviewed_marketplace(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_identity, 'health_payload', lambda *a, **k: {
        'state': 'READY', 'identity_only': False,
        'business_execution_verified': True,
    })
    receipt_path = authority.final_approval_receipt_path(
        market['preview'], reports_root=bridge.REPORTS_ROOT)
    assert not receipt_path.exists()
    status, result = handler_request('/api/product-workspace/r3-marketplace/approve', data)
    assert status == 409 and result['error'] == 'POST_COMMON_FINAL_REVIEW_BLOCKED'
    assert store.get_plan(data['plan_id']) is None
    assert not receipt_path.exists()
    assert io.mutations == 1


@pytest.mark.parametrize('entry', ['stages_get', 'marketplace_preview'])
def test_readonly_stage_does_not_offer_new_final_approval_without_post_common_authority(
        tmp_path, monkeypatch, entry):
    _, _, store, data, io, _ = reviewed_marketplace(tmp_path, monkeypatch)
    if entry == 'stages_get':
        status, result = handler_request(
            '/api/product-workspace/publication-stages?offer_id=' + data['offer_id'])
    else:
        status, result = handler_request(
            '/api/product-workspace/r3-marketplace/preview', {'offer_id': data['offer_id']})
    assert status == 200, result
    market = result['marketplace']
    assert market['preview']['status'] == 'READY_FOR_FINAL_REVIEW'
    assert market['status'] == 'BLOCKED'
    assert market['final_review_available'] is False
    assert market['final_review']['status'] == 'BLOCKED'
    assert 'COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN' in market['blockers']
    assert store.get_plan(data['plan_id']) is None
    assert io.mutations == 1  # Fixture's synthetic COMMON write only.


@pytest.mark.parametrize('damage,expected_blocker', [
    ('history_budget_unknown', 'COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN'),
    ('official_readback_missing', 'COMMON_OFFICIAL_FIELD_READBACK_UNPROVEN'),
    ('official_field_comparison_missing', 'COMMON_OFFICIAL_FIELD_READBACK_UNPROVEN'),
    ('duplicate_target', 'POST_COMMON_COMPLETE_TARGET_MATRIX_UNPROVEN'),
    ('manifest_target_missing', 'POST_COMMON_COMPLETE_TARGET_MATRIX_UNPROVEN'),
])
def test_original_http_approval_rejects_unproven_post_common_scope_before_store_mutation(
        tmp_path, monkeypatch, damage, expected_blocker):
    monkeypatch.setattr(server, '_r3_marketplace_business_execution_gate', lambda: None)
    _, _, store, data, io, _ = reviewed_marketplace(tmp_path, monkeypatch)
    status, frozen = server._preview_r3_marketplace_stage({'offer_id': data['offer_id']})
    assert status == 200 and frozen['marketplace']['status'] == 'BLOCKED'
    frozen = deepcopy(frozen)
    candidate_path = authority.release_candidate_path(
        frozen['marketplace']['preview'], reports_root=bridge.REPORTS_ROOT)
    assert not candidate_path.exists()
    if damage in {'official_readback_missing', 'official_field_comparison_missing'}:
        with sqlite3.connect(store.path) as db:
            if damage == 'official_readback_missing':
                db.execute('DELETE FROM release_target_readbacks')
            else:
                row = db.execute('SELECT evidence_json FROM release_target_readbacks').fetchone()
                evidence = json.loads(row[0])
                evidence['checks'].pop('sku_logistics')
                encoded = json.dumps(evidence, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'))
                db.execute('UPDATE release_target_readbacks SET evidence_json=?, evidence_digest=?',
                           (encoded, hashlib.sha256(encoded.encode()).hexdigest()))
        monkeypatch.setattr(server, '_preview_r3_marketplace_stage',
                            lambda _: (200, deepcopy(frozen)))
    elif damage in {'duplicate_target', 'manifest_target_missing'}:
        candidate = frozen['marketplace']['preview']
        if damage == 'duplicate_target':
            candidate['target_labels'].append(candidate['target_labels'][0])
        else:
            candidate['review_manifest']['targets'].pop()
        monkeypatch.setattr(server, '_preview_r3_marketplace_stage',
                            lambda _: (200, deepcopy(frozen)))
        # Isolate admission from the later artifact writer's digest check.
        monkeypatch.setattr(authority, 'persist_release_candidate', lambda *a, **k: None)
    status, result = handler_request('/api/product-workspace/r3-marketplace/approve', data)
    assert not candidate_path.exists(), 'unapproved candidate file was persisted'
    assert store.get_plan(data['plan_id']) is None, 'unapproved ReleaseStore plan was persisted'
    assert status == 409, result
    assert result['error'] == 'POST_COMMON_FINAL_REVIEW_BLOCKED'
    assert expected_blocker in result['blockers']
    assert result['final_review_available'] is False
    assert result['execution_authority'] is False
    assert result['external_writes_performed'] == []
    assert io.mutations == 1  # Only the fixture's synthetic COMMON write.


def test_original_http_route_only_resumes_an_already_persisted_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(server, '_r3_marketplace_business_execution_gate', lambda: None)
    _, _, store, data, io, market = reviewed_marketplace(tmp_path, monkeypatch)
    seed_preexisting_marketplace_approval(store, data, market)
    previous = store.get_plan(data['plan_id'])['approval']
    status, result = handler_request('/api/product-workspace/r3-marketplace/approve', data)
    assert status == 200, result
    assert store.get_plan(data['plan_id'])['approval'] == previous
    assert io.mutations == 1


def test_direct_approval_with_blocked_preview_missing_candidate_returns_identity_conflict(
        tmp_path, monkeypatch):
    monkeypatch.setattr(server, '_r3_marketplace_business_execution_gate', lambda: None)
    _, _, store, data, io, _ = reviewed_marketplace(tmp_path, monkeypatch)
    monkeypatch.setattr(server, '_preview_r3_marketplace_stage', lambda _: (200, {
        'marketplace': {'status': 'BLOCKED', 'plan': {
            'plan_id': data['plan_id'], 'confirmation_token': data['confirmation_token']}}}))
    status, result = handler_request('/api/product-workspace/r3-marketplace/approve', data)
    assert status == 409, result
    assert result['error'] == 'MARKETPLACE_REVIEW_IDENTITY_CONFLICT'
    assert store.get_plan(data['plan_id']) is None
    assert io.mutations == 1


def test_pending_marketplace_plan_with_common_readback_damage_is_readonly_blocked(
        tmp_path, monkeypatch):
    _, _, store, data, io, market = reviewed_marketplace(tmp_path, monkeypatch)
    store.create_plan(market['plan']['payload'])
    with sqlite3.connect(store.path) as db:
        db.execute('DELETE FROM release_target_readbacks')
    status, result = handler_request(
        '/api/product-workspace/publication-stages?offer_id=' + data['offer_id'])
    assert status == 200, result
    projected = result['marketplace']
    assert projected['plan']['plan_id'] == data['plan_id']
    assert projected['status'] == 'BLOCKED'
    assert projected['final_review_available'] is False
    assert projected['final_review']['status'] == 'BLOCKED'
    assert projected['blockers']
    assert store.get_plan(data['plan_id'])['status'] == 'PENDING_APPROVAL'
    assert io.mutations == 1


def test_final_decision_rechecks_admission_after_local_reservation(tmp_path, monkeypatch):
    """A newly supplied budget proof can expire after the first admission check."""
    _, _, store, data, io, _ = reviewed_marketplace(tmp_path, monkeypatch)
    actual_inspect = admission_gate.inspect_post_common_final_review
    actual_persist = authority.persist_release_candidate
    budget_revoked = False
    admission_calls = []

    def synthetic_budget_producer(**kwargs):
        # Simulate only the future budget producer. Keep all present field,
        # target, and digest checks from the real fail-closed admission.
        result = actual_inspect(**kwargs)
        blockers = [blocker for blocker in result['blockers']
                    if blocker != 'COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN']
        if budget_revoked:
            blockers.append('COMMON_HISTORICAL_BUDGET_CHANGED')
        admission_calls.append(tuple(blockers))
        return {**result, 'status': 'BLOCKED' if blockers else 'READY',
                'blockers': blockers, 'final_review_available': not blockers}

    def revoke_after_candidate_persist(*args, **kwargs):
        nonlocal budget_revoked
        result = actual_persist(*args, **kwargs)
        budget_revoked = True
        return result

    monkeypatch.setattr(admission_gate, 'inspect_post_common_final_review', synthetic_budget_producer)
    monkeypatch.setattr(authority, 'persist_release_candidate', revoke_after_candidate_persist)
    status, result = server._approve_r3_marketplace_stage(data)

    assert status == 409, result
    assert result['error'] == 'MARKETPLACE_ADMISSION_CHANGED_BEFORE_APPROVAL'
    assert result['external_writes_performed'] == []
    assert store.get_plan(data['plan_id'])['status'] == 'PENDING_APPROVAL'
    assert len(admission_calls) >= 2
    assert admission_calls[0] == ()
    assert 'COMMON_HISTORICAL_BUDGET_CHANGED' in admission_calls[-1]
    assert io.mutations == 1  # The fixture's synthetic COMMON write only.
