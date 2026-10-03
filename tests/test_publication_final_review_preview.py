"""Read-only candidate contract for the future single final review."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from hashlib import sha256

import pytest

from shared_platform.publication_final_review_preview import (
    build_final_review_preview,
    require_current_final_review_preview,
)
from test_b4b_common_stage import context
from modules.products import server


def candidate(tmp_path, monkeypatch):
    documents, dashboard, store, request = context(tmp_path, monkeypatch)
    status, view = server._preview_r3_common_stage(request)
    assert status == 200, view
    assert view['common']['status'] == 'APPROVAL_REQUIRED'
    return documents, dashboard, store, view


def build(documents, dashboard, view):
    return build_final_review_preview(documents=documents, dashboard=dashboard, common_view=view)


def test_frozen_complete_preview_is_readonly_and_has_no_approval(tmp_path, monkeypatch):
    documents, dashboard, store, view = candidate(tmp_path, monkeypatch)
    preview = build(documents, dashboard, view)
    body = preview.as_dict()
    assert body['status'] == 'UNAPPROVED_PREVIEW'
    assert body['execution_authority'] is False
    assert body['external_writes_performed'] == []
    assert body['offer_id'] == documents['round1_snapshot']['offer_id']
    assert body['product_revision'] == dashboard['product']['revision']
    assert body['round1_snapshot_digest'] == documents['round1_snapshot']['snapshot_digest']
    assert body['marketplace_targets'] == documents['round1_snapshot']['canonical_targets']
    assert body['common']['plan_id'] == view['common']['plan']['plan_id']
    assert body['common']['payload'] == view['common']['plan']['payload']
    assert body['common']['confirmation_token_digest'].startswith('sha256:')
    assert view['common']['plan']['confirmation_token'] not in str(body)
    assert body['expected_write_scope'] == [
        {'stage': 'R3_COMMON', 'target': 'miaoshou:COMMON', 'operation': 'common_draft_sync'},
        *[{'stage': 'R3_MARKETPLACE', 'target': target, 'operation': 'target_publication'}
          for target in body['marketplace_targets']],
    ]
    expected = sum(len(documents[key]['assets']) for key in ('generation_result', 'translation_result'))
    assert len(body['r2_images']) == expected
    assert all(row['artifact_digest'].startswith('sha256:') for row in body['r2_images'])
    assert set(body['r2_target_image_routes']) == set(body['marketplace_targets'])
    assert all(body['r2_target_image_routes'][target] for target in body['marketplace_targets'])
    assert not store.path.exists()
    assert require_current_final_review_preview(preview, documents=documents, dashboard=dashboard, common_view=view)
    body['common']['payload']['product_id'] = 'other'
    assert preview.as_dict()['common']['payload'] == view['common']['plan']['payload']
    with pytest.raises(FrozenInstanceError):
        preview.digest = 'changed'


@pytest.mark.parametrize('drift', [
    'offer', 'revision', 'r1', 'r2_image', 'targets', 'common_plan',
    'common_token', 'common_payload', 'common_payload_digest', 'missing_field',
    'dashboard_facts', 'dashboard_source', 'common_status', 'plan_target',
])
def test_identity_or_scope_drift_fails_closed(tmp_path, monkeypatch, drift):
    documents, dashboard, _, view = candidate(tmp_path, monkeypatch)
    preview = build(documents, dashboard, view)
    documents, dashboard, view = deepcopy(documents), deepcopy(dashboard), deepcopy(view)
    plan = view['common']['plan']
    payload = plan['payload']
    if drift == 'offer':
        dashboard['product']['offer_id'] = '123'
    elif drift == 'revision':
        dashboard['product']['revision'] += 1
    elif drift == 'r1':
        documents['round1_snapshot']['offer_id'] = '123'
    elif drift == 'r2_image':
        documents['generation_result']['assets'][0]['artifact_digest'] = 'sha256:' + '0' * 64
    elif drift == 'targets':
        payload['r3_stage_binding']['marketplace_targets'] = ['tiktok:OTHER']
    elif drift == 'common_plan':
        plan['plan_id'] += '-other'
    elif drift == 'common_token':
        plan['confirmation_token'] += '-other'
    elif drift == 'common_payload':
        payload['product_facts']['title'] = 'changed'
    elif drift == 'common_payload_digest':
        plan['payload_digest'] = 'sha256:' + '0' * 64
    elif drift == 'dashboard_facts':
        dashboard['product']['cost_cny'] = '999'
    elif drift == 'dashboard_source':
        dashboard['_source_identity_inputs']['collect_box']['source_item_id'] = '123'
    elif drift == 'common_status':
        view['common']['status'] = 'READY_TO_SYNC'
    elif drift == 'plan_target':
        plan['targets'] = ['miaoshou:OTHER']
    else:
        del payload['r3_stage_binding']['r2_identity']
    with pytest.raises(ValueError):
        require_current_final_review_preview(preview, documents=documents, dashboard=dashboard, common_view=view)


def test_missing_image_or_duplicate_target_cannot_build(tmp_path, monkeypatch):
    documents, dashboard, _, view = candidate(tmp_path, monkeypatch)
    documents = deepcopy(documents)
    documents['translation_result']['assets'].pop()
    with pytest.raises(ValueError):
        build(documents, dashboard, view)
    documents, dashboard, _, view = candidate(tmp_path / 'second', monkeypatch)
    view = deepcopy(view)
    labels = view['common']['plan']['payload']['r3_stage_binding']['marketplace_targets']
    labels.append(labels[0])
    with pytest.raises(ValueError):
        build(documents, dashboard, view)


def test_existing_unapproved_common_plan_is_still_only_a_preview(tmp_path, monkeypatch):
    documents, dashboard, store, view = candidate(tmp_path, monkeypatch)
    store.create_plan(view['common']['plan']['payload'])
    status, persisted_view = server._preview_r3_common_stage({
        'offer_id': documents['round1_snapshot']['offer_id'],
        'publication_targets': ['miaoshou:COMMON'], 'release_stage': 'R3_COMMON',
    })
    assert status == 200
    assert persisted_view['common']['plan']['status'] == 'PENDING_APPROVAL'
    # The Store row does not expose an exact `approved: false`; fail closed.
    with pytest.raises(ValueError):
        build(documents, dashboard, persisted_view)


@pytest.mark.parametrize('value', [1, True, 0, None, 'false', [], {}])
def test_approved_flag_requires_exact_false(tmp_path, monkeypatch, value):
    documents, dashboard, _, view = candidate(tmp_path, monkeypatch)
    view = deepcopy(view)
    view['common']['plan']['approved'] = value
    with pytest.raises(ValueError):
        build(documents, dashboard, view)


@pytest.mark.parametrize('value', [1, True, None, 'false'])
def test_persistence_flag_requires_exact_false(tmp_path, monkeypatch, value):
    documents, dashboard, _, view = candidate(tmp_path, monkeypatch)
    view = deepcopy(view)
    view['common']['plan']['persisted'] = value
    with pytest.raises(ValueError):
        build(documents, dashboard, view)


def test_current_rejects_foreign_equality_and_corrupt_owned_fields(tmp_path, monkeypatch):
    documents, dashboard, _, view = candidate(tmp_path, monkeypatch)
    preview = build(documents, dashboard, view)

    class AlwaysEqual:
        def __ne__(self, other):
            return False

    with pytest.raises((TypeError, ValueError)):
        require_current_final_review_preview(AlwaysEqual(), documents=documents,
                                             dashboard=dashboard, common_view=view)
    object.__setattr__(preview, 'digest', 'sha256:' + '0' * 64)
    with pytest.raises((TypeError, ValueError)):
        require_current_final_review_preview(preview, documents=documents,
                                             dashboard=dashboard, common_view=view)
    object.__setattr__(preview, 'digest', 'sha256:' + sha256(preview._canonical_bytes).hexdigest())
    object.__setattr__(preview, '_canonical_bytes', bytearray(preview._canonical_bytes))
    with pytest.raises((TypeError, ValueError)):
        require_current_final_review_preview(preview, documents=documents,
                                             dashboard=dashboard, common_view=view)


def test_unrelated_dashboard_state_does_not_stale_preview(tmp_path, monkeypatch):
    documents, dashboard, _, view = candidate(tmp_path, monkeypatch)
    preview = build(documents, dashboard, view)
    changed = deepcopy(dashboard)
    changed['weekly_profit'] = {'generated_at': 'later'}
    changed['global_sku_reservation_count'] = 999
    changed['release_rehearsal'] = {'blockers': ['unrelated']}
    changed['listing_copy']['candidates'][0]['created_at'] = 'later'
    assert require_current_final_review_preview(preview, documents=documents,
                                                dashboard=changed, common_view=view)


@pytest.mark.parametrize('path', [
    ('product', 'cost_cny'), ('product', 'seller_sku_candidate'),
    ('publication_scope', 'selected_labels'),
    ('pricing_review', 'target_pricing'),
])
def test_selected_dashboard_business_facts_still_stale_preview(tmp_path, monkeypatch, path):
    documents, dashboard, _, view = candidate(tmp_path, monkeypatch)
    preview = build(documents, dashboard, view)
    changed = deepcopy(dashboard)
    changed[path[0]][path[1]] = 'changed'
    with pytest.raises(ValueError):
        require_current_final_review_preview(preview, documents=documents,
                                             dashboard=changed, common_view=view)


def test_selected_variant_price_stales_preview(tmp_path, monkeypatch):
    documents, dashboard, _, view = candidate(tmp_path, monkeypatch)
    preview = build(documents, dashboard, view)
    changed = deepcopy(dashboard)
    changed['product']['source_skus'][0]['price_cny'] = '999'
    with pytest.raises(ValueError):
        require_current_final_review_preview(preview, documents=documents,
                                             dashboard=changed, common_view=view)


def test_source_title_drift_tracks_real_common_plan(tmp_path, monkeypatch):
    documents, dashboard, _, request = context(tmp_path, monkeypatch)
    dashboard['product']['source_title_zh'] = '中文原题甲'
    status, view = server._preview_r3_common_stage(request)
    assert status == 200, view
    preview = build(documents, dashboard, view)
    dashboard['product']['source_title_zh'] = '中文原题乙'
    status, refreshed = server._preview_r3_common_stage(request)
    assert status == 200, refreshed
    assert refreshed['common']['plan']['plan_id'] != view['common']['plan']['plan_id']
    with pytest.raises(ValueError, match='FINAL_REVIEW_PREVIEW_STALE'):
        require_current_final_review_preview(preview, documents=documents,
                                             dashboard=dashboard, common_view=view)


def test_unselected_sku_price_does_not_stale_real_common_plan(tmp_path, monkeypatch):
    documents, dashboard, _, request = context(tmp_path, monkeypatch)
    dashboard['product']['source_skus'].append({
        'key': 'not-selected', 'label': 'Other', 'model_sku': '9999',
        'price_cny': '10', 'commercial_facts': {'cost_cny': '3'},
    })
    status, view = server._preview_r3_common_stage(request)
    assert status == 200, view
    preview = build(documents, dashboard, view)
    dashboard['product']['source_skus'][-1]['price_cny'] = '999'
    status, refreshed = server._preview_r3_common_stage(request)
    assert status == 200, refreshed
    assert refreshed['common']['plan']['payload'] == view['common']['plan']['payload']
    assert require_current_final_review_preview(preview, documents=documents,
                                                dashboard=dashboard, common_view=view)


@pytest.mark.parametrize('damage', ['duplicate_selection', 'duplicate_selected_row', 'missing_selected_row'])
def test_selected_sku_identity_must_be_complete(tmp_path, monkeypatch, damage):
    documents, dashboard, _, view = candidate(tmp_path, monkeypatch)
    changed = deepcopy(dashboard)
    if damage == 'duplicate_selection':
        changed['product']['selected_sku_keys'].append('default')
    elif damage == 'duplicate_selected_row':
        changed['product']['source_skus'].append(deepcopy(changed['product']['source_skus'][0]))
    else:
        changed['product']['source_skus'].clear()
    with pytest.raises(ValueError):
        build(documents, changed, view)
