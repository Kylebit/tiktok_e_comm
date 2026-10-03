"""Actual original producers and immutable scope; closed paid transports only."""
from copy import deepcopy
import json

import pytest

from shared_platform import workbench_publication_native as native
from shared_platform import publication_r3_image_bridge as bridge
from shared_platform.native_parent_images import NativeImageRuntime
from shared_platform.publication_paid_requests import PaidRequestBlocked
from test_native_parent_images import images_task, _runtime, FixtureClient
from test_native_parent_images_phase_continuation import _closed
from test_round1_auto_freeze import public_settings
from test_round1_workspace_freeze import live, prepared


def test_original_localized_recovery_refreshes_only_accounting_and_retains_complete_common_scope(images_task, monkeypatch):
    value = images_task
    directory = value['profile'].root / 'reports/product-preparation' / value['task']['scope']['offer_id']
    originals = {directory / name: (directory / name).read_bytes() for name in
                 ('first-review.json', 'round1-approved-snapshot.json')}
    client, history_directory, _, receipts = _closed(value, monkeypatch)
    client.ready = True
    original_refresh = NativeImageRuntime.current_master_after_localization
    observations = []
    def refresh(runtime, producer, qa, previous):
        current = producer._brand_generation_summary(runtime.frozen['offer_id'], runtime=runtime)
        assert current != previous
        accounting = {'paid_requests', 'completed_asset_generation_count', 'external_generation_count'}
        assert {k: v for k, v in current.items() if k not in accounting} == {
            k: v for k, v in previous.items() if k not in accounting}
        observations.append((deepcopy(previous), deepcopy(current)))
        return original_refresh(runtime, producer, qa, previous)
    monkeypatch.setattr(NativeImageRuntime, 'current_master_after_localization', refresh)
    result = value['worker'].images_adapter.recover_images(value['task'], token=value['token'])
    assert result['status'] == 'prepared', result
    assert len(observations) == 1
    assert sum(row['kind'] == 'brand' for row in FixtureClient.calls) == 14
    assert all(path.read_bytes() == raw for path, raw in receipts.items())
    documents = bridge.load_r2_documents(value['task']['scope']['offer_id'], reports_root=directory.parent,
        native_review_reader=native.NativeR2PreparedReviewReader(value['task'], value['profile']))
    first = documents['first_review']
    scope = first['target_selection']['requested']
    assert scope == documents['round1_snapshot']['canonical_targets'] and 'miaoshou:COMMON' in scope
    route = bridge.build_dual_brand_publication_bridge(offer_id=first['offer_id'], first_review=first,
        generation=documents['generation_result'], translation=documents['translation_result'],
        approved_by=documents['round1_snapshot']['approved_by'])
    markets = [target for target in scope if target != 'miaoshou:COMMON']
    assert route['targets'] == scope and set(route['target_facts']) == set(scope)
    assert list(route['image_routes']) == markets and list(route['route_locales']) == markets
    assert route['target_count'] == 8 and route['route_count'] == 7 and route['common_baseline_image_count'] == 7
    for malformed, error in (
        ({**first, 'targets': [row for row in first['targets'] if row['target'] != 'miaoshou:COMMON']},
         'first-review target fact coverage changed'),
        ({**first, 'target_selection': {**first['target_selection'], 'requested': scope + ['miaoshou:OTHER']}},
         'first-review publication target is unsupported'),
        ({**first, 'target_selection': {**first['target_selection'], 'requested': ['miaoshou:COMMON']}},
         'first-review publication market targets are missing')):
        with pytest.raises(ValueError, match=error):
            bridge.build_dual_brand_publication_bridge(offer_id=first['offer_id'], first_review=malformed,
                generation=documents['generation_result'], translation=documents['translation_result'],
                approved_by=documents['round1_snapshot']['approved_by'])
    before_calls = list(FixtureClient.calls)
    master_path = directory / 'brand-image-generation.json'
    master_bytes = master_path.read_bytes()
    with _runtime(value, history_directory.parents[2], read_only=True) as runtime:
        producer = runtime.adapter._script('prepare_product_images.py')
        qa = runtime.adapter._script('run_automated_image_qa.py')
        previous = json.loads(master_bytes)
        changed = deepcopy(previous)
        changed['assets'][0]['artifact_digest'] = 'sha256:' + '0' * 64
        master_path.write_text(json.dumps(changed), encoding='utf-8')
        try:
            with pytest.raises(PaidRequestBlocked, match='R2_CONTINUATION_MASTER_REPORT_CHANGED'):
                original_refresh(runtime, producer, qa, previous)
        finally:
            master_path.write_bytes(master_bytes)
        with value['engine'].transaction() as db:
            db.execute('UPDATE workbench_execution SET lease_until=0 WHERE task_id=?', (value['task']['task_id'],))
        with pytest.raises(ValueError, match='stale or invalid lease'):
            original_refresh(runtime, producer, qa, previous)
    assert FixtureClient.calls == before_calls
    assert all(path.read_bytes() == raw for path, raw in originals.items())


def test_native_immutable_source_requires_runtime_after_mutable_auto_flags_are_removed(images_task, monkeypatch):
    from modules.sourcing import new_product_workbench as workbench
    value = images_task
    offer = value['task']['scope']['offer_id']
    monkeypatch.setattr(bridge, 'load_r2_documents', lambda *a, **k: pytest.fail('native source cannot use raw fallback'))
    with pytest.raises(ValueError, match='NATIVE_R2_SERVICE_RUNTIME_REQUIRED'):
        native.load_service_r2_documents(offer)
    state = workbench.load_state(offer)
    path = value['profile'].root / 'data/new_product_workbench' / (offer + '.json')
    original = path.read_bytes()
    for field in ('approved_by', 'approval_authority', 'decision_digest'):
        state['product_approval'].pop(field, None)
    path.write_text(json.dumps(state), encoding='utf-8')
    try:
        with pytest.raises(ValueError, match='NATIVE_R2_SERVICE_RUNTIME_REQUIRED'):
            native.load_service_r2_documents(offer)
    finally:
        path.write_bytes(original)


def test_genuine_legacy_explicit_prepared_approval_keeps_original_loader(live, monkeypatch):
    packet, _ = prepared(live)
    code, result = live['call']('approve', {'offer_id': live['offer'],
        'prepared_reference': packet['prepared_reference'], 'user_approved': True,
        'approved_by': packet['approval_actor']})
    assert code == 200 and result['status'] == 'FROZEN', result
    assert result['snapshot']['approval_authority'] == 'EXPLICIT_CONVERSATION_APPROVAL'
    seen = []
    sentinel = object()
    def original_loader(offer):
        seen.append(offer)
        return sentinel
    monkeypatch.setattr(bridge, 'load_r2_documents', original_loader)
    assert native.load_service_r2_documents(live['offer']) is sentinel
    assert seen == [live['offer']]
