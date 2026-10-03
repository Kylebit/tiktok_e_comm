"""Real legacy HTTP entry -> same ledger guard -> synthetic platform executor."""
from time import monotonic, sleep

import pytest

from shared_platform import operations_domain_guard
from shared_platform.workbench_engine import WorkbenchEngine
from test_product_publication_start_http import publication_start_server, isolated_catalog_sink, _post, _wait_for_report
from modules.products import server as product_server


def _lock(snapshot, tmp_path, monkeypatch, *, sku):
    engine = WorkbenchEngine(tmp_path / 'synthetic-task-ledger.db', {'code_version': 'guard-fixture'})
    targets = [r['target_label'] for r in snapshot['publication_targets'] if r['target_label'].startswith('tiktok:')]
    engine.register_executor('synthetic', ['delisting'], engine.release)
    task = engine.create({'template': 'delisting', 'source_key': 'reserved', 'scope': {'skus': [sku], 'shops': targets}})
    assert engine.claim(task['task_id'], 'synthetic')
    begin = engine.begin_domain_operation
    engine.guard_calls = []
    def recording_begin(operation_id, **scope):
        engine.guard_calls.append(scope)
        return begin(operation_id, **scope)
    engine.begin_domain_operation = recording_begin
    monkeypatch.setattr(operations_domain_guard, 'engine_for', lambda _root: engine)
    return engine


def test_original_publish_endpoint_rejects_same_sku_task_lock(publication_start_server, tmp_path, monkeypatch):
    base, snapshot, _, calls = publication_start_server
    sku = snapshot['skus'][0]['seller_sku']
    engine = _lock(snapshot, tmp_path, monkeypatch, sku=sku)
    status, response = _post(base, '/api/product-workspace/publish-tiktok', {
        'offer_id': snapshot['offer_id'], 'plan_id': snapshot['plan_id'],
        'skus': ['attacker-override'], 'shops': ['attacker-override'],
    })
    assert status == 202
    deadline = monotonic() + 3
    state = None
    while monotonic() < deadline:
        run = product_server._product_publication_run_store().get_run_by_id(run_id=response['run_id'])
        state = run['state']
        if state == 'FAILED':
            break
        sleep(.01)
    assert state == 'FAILED'
    assert calls == []
    assert engine.guard_calls and engine.guard_calls[0]['skus'] == [sku]
    assert engine.dashboard()['tasks'][0]['execution_state'] == 'running'


def test_original_publish_endpoint_allows_unrelated_sku(publication_start_server, tmp_path, monkeypatch):
    base, snapshot, reports, calls = publication_start_server
    assert snapshot['skus'][0]['seller_sku'] != '0000'
    _lock(snapshot, tmp_path, monkeypatch, sku='0000')
    status, response = _post(base, '/api/product-workspace/publish-tiktok', {
        'offer_id': snapshot['offer_id'], 'plan_id': snapshot['plan_id'],
    })
    assert status == 202
    persisted = _wait_for_report(reports, report_id=response['report_id'], offer_id=snapshot['offer_id'])
    assert persisted['status'] == 'PUBLISHED'
    assert len(calls) == 1


def test_exact_retry_owns_unique_guard_after_completed_original(tmp_path, monkeypatch):
    engine = WorkbenchEngine(tmp_path / 'ledger.db', {'code_version': 'guard-fixture'})
    monkeypatch.setattr(operations_domain_guard, 'engine_for', lambda _root: engine)
    plan = {'plan_id': 'plan-1', 'product_id': '3956742887', 'seller_sku': '0988',
            'targets': ['shopee:PH', 'shopee:MY']}
    original = operations_domain_guard.begin_publication(
        plan, ['shopee:PH', 'shopee:MY'], tmp_path)
    engine.complete_domain_operation(original[1], provider_readback_ref='zero-write:receipt')
    attempt = {'run_id': 'successor-run', 'retry_of_run_id': 'failed-run',
               'source_evidence_digest': 'a' * 64}
    retry = operations_domain_guard.begin_publication(
        plan, ['shopee:PH', 'shopee:MY'], tmp_path, retry_attempt=attempt)
    assert retry[2]['acquired'] is True
    assert retry[1] != original[1] and retry[1].startswith(original[1] + ':retry:')
    with pytest.raises(ValueError, match='already exists|reconciliation'):
        operations_domain_guard.begin_publication(
            plan, ['shopee:PH', 'shopee:MY'], tmp_path, retry_attempt=attempt)
