"""Actual new-task native fork with original graph, actor, claims and ledger.

Only the external platform executor edge is replaced. Its fixture responses
are ACCEPTED/PROCESSING, never claimed as official publication success.
"""
import threading
import time
from collections import Counter

from test_round1_workspace_freeze import live
from test_round1_auto_freeze import public_settings
from owned_new_publication_task import complete_new_task
from shared_platform import native_new_task_v4_targets as native
from shared_platform import native_sole_final_execution as execution


def test_new_three_model_one_decision_parallel_fork_keeps_operation_actor_reuse(
        live, monkeypatch, tmp_path, record_property):
    runtime, market, task, _ = complete_new_task(live, monkeypatch, tmp_path)
    first = set()
    calls = []
    guard = threading.Lock()
    barrier = threading.Barrier(3)

    def dependencies(active, platform, ledger, references, observations):
        def accepted(request):
            label = request.target_labels[0]
            assert request.snapshot['offer_id'] == market['product_id']
            assert len(request.snapshot['skus']) == 3
            with guard:
                initial = platform not in first
                first.add(platform)
                calls.append((platform, label))
            if initial:
                barrier.wait(timeout=30)
            operation = {'TIKTOK': 'publish_target', 'SHOPEE': 'create_publish_task',
                         'OZON': 'import_variant'}[platform]
            ledger.reserve_target(label, operation)
            # The real durable ledger owns the attempt and dispatch identity.
            fixture = ledger.dispatch_reserved(operation, lambda payload: payload,
                                                {'fixture_target': label})
            assert fixture['fixture_target'] == label
            ledger.complete_reserved(operation, 'ACCEPTED')
            references.append('owned-provider-fixture:' + label)
            return {'schema_version': 'product-publication-platform-result/v1',
                    'platform': platform,
                    'targets': [{'target_label': label, 'status': 'PROCESSING',
                                 'evidence': {'accepted': True}}],
                    'dispatch_attempted': True, 'readback_completed': False,
                    'external_write_count': 1}
        return accepted

    monkeypatch.setattr(native, '_dependencies', dependencies)
    try:
        prepared = runtime.prepare(market['plan_id'])
        decision = runtime.decide(nonce=prepared['nonce'], review_digest=prepared['review_digest'])
        before = len(runtime.supervisor.client_history)
        started = time.monotonic()
        with runtime.operation() as service:
            code, result = execution.execute_decision(service, decision['decision_id'], runtime=runtime)
        elapsed = time.monotonic() - started
        rpc_count = len(runtime.supervisor.client_history) - before
        record_property('native_execution_elapsed_seconds', round(elapsed, 6))
        record_property('actual_owner_authentication_rpcs', rpc_count)
        record_property('platforms_started_before_first_returns', len(first))
        assert code == 200, result
        assert sorted(label for _, label in calls) == sorted(market['targets'])
        assert all(count == 1 for count in Counter(label for _, label in calls).values())
        run = runtime.store.get_run('release-run:' + market['payload_digest'][:24])
        assert all(row['status'] == 'SUBMITTED_UNVERIFIED' and row['attempts'] == 1
                   for row in run['targets'])
        with runtime.store._connect_readonly() as db:
            assert db.execute('SELECT count(*) FROM native_sole_final_decisions').fetchone()[0] == 1
            reservations = db.execute('SELECT count(*) FROM native_final_budget_reservations').fetchone()[0]
        assert reservations == len(market['targets'])
        # One original parent operation plus one genuine per-platform lease;
        # worker threads cannot inherit the parent's ContextVar proof.
        assert rpc_count == 4, (rpc_count, len(calls))
        with runtime.operation() as service:
            execution.execute_decision(service, decision['decision_id'], runtime=runtime)
        assert len(calls) == len(market['targets'])
        after = runtime.store.get_run(run['run_id'])
        assert [row['attempts'] for row in after['targets']] == [row['attempts'] for row in run['targets']]
    finally:
        runtime.close()
