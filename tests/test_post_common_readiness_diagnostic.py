"""Offline counterexamples for the task's post-COMMON review transition."""

import pytest

from shared_platform.operations_publication_common import (
    _final_ready, inspect_post_common_readiness,
)


def _case(targets=None):
    targets = targets or ['tiktok:HB_MY', 'shopee:HB_MY']
    task = {'scope': {'offer_id': '9000052', 'shops': targets}}
    view = {
        'common': {'status': 'VERIFIED'},
        'marketplace': {
            'status': 'APPROVAL_REQUIRED',
            'preview': {'status': 'READY_FOR_FINAL_REVIEW',
                        'candidate_digest': 'sha256:' + 'a' * 64,
                        'review_manifest': {'targets': [{'target_label': value}
                                                        for value in targets]}},
            'plan': {'product_id': '9000052', 'targets': targets},
        },
    }
    return task, view


def test_verified_label_without_common_history_or_official_readback_is_blocked():
    task, view = _case()
    with pytest.raises(ValueError, match='COMMON'):
        _final_ready(task, view)


def test_duplicate_marketplace_target_cannot_be_hidden_by_set_equality():
    task, view = _case(['tiktok:HB_MY', 'tiktok:HB_MY'])
    with pytest.raises(ValueError, match='TARGET'):
        _final_ready(task, view)


def test_complete_local_shape_still_cannot_invent_common_history_budget():
    task, view = _case()
    targets = task['scope']['shops']
    view['marketplace']['targets'] = targets
    view['marketplace']['preview']['target_labels'] = targets
    view['marketplace']['preview']['review_manifest']['manifest_digest'] = 'sha256:' + 'b' * 64
    view['common']['run'] = {'targets': [{
        'target_label': 'miaoshou:COMMON', 'status': 'SUCCEEDED',
        'readback': {'evidence_digest': 'digest', 'verified_at': 'synthetic-time',
                     'evidence': {'source': 'miaoshou_open_api', 'verified': True,
                                  'checks': {'title': True}}},
    }]}
    diagnostic = inspect_post_common_readiness(task, view)
    assert diagnostic['blockers'] == ['COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN']
    assert diagnostic['status'] == 'BLOCKED'
    assert diagnostic['final_review_available'] is False
    assert diagnostic['execution_authority'] is False
    assert diagnostic['external_writes_performed'] == []
