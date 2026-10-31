"""New three-model native path has no historical adoption/source pins."""
import json
import pytest
from test_round1_workspace_freeze import live
from test_round1_auto_freeze import public_settings
from owned_new_publication_task import complete_new_task
from shared_platform import native_sole_final_execution as execution
from shared_platform.native_new_task_v4_targets import registry_for_new_task,scope
from shared_platform.native_final_mutation_budget import build_native_budget_ledger
from shared_platform.final_review_server_admission import ApprovalBlocked


def test_new_three_models_real_graph_one_decision_generic_registry_and_original_budget(live,monkeypatch,tmp_path):
    runtime,market,task,calls=complete_new_task(live,monkeypatch,tmp_path)
    try:
        from shared_platform.publication_execution_package import preflight_native_review
        before=runtime.store.path.read_bytes()
        ready=preflight_native_review(runtime,market['plan_id'])
        assert ready['status']=='READY' and ready['execution_authority'] is False
        assert runtime.store.path.read_bytes()==before
        prepared=runtime.prepare(market['plan_id'])
        assert len(prepared['manifest']['variants'])==3
        decision=runtime.decide(nonce=prepared['nonce'],review_digest=prepared['review_digest'])
        with runtime.operation() as service:
            active=execution._NativeExecution(service,decision['decision_id'],runtime)
            active.plan_id=market['plan_id'];token=execution._ACTIVE.set(active)
            try:
                operations,current,frozen,images=scope(active,market)
                assert current['task_id']==task['task_id'] and frozen['offer_id'] not in {'4039931504','8412287877065586543'}
                registry=registry_for_new_task(active,market,{})
                from domains.channel_operations.omnichannel_orchestrator import ADAPTER_NAMES
                assert set(registry)=={ADAPTER_NAMES[p] for p in ('tiktok','shopee','ozon')}
                run=runtime.store.start_run(market['plan_id'])
                # Same complete native decision builds a dynamic frozen ledger,
                # not an old fifteen-target budget or product-specific grant.
                from shared_platform.r3_frozen_review_producer import read_stored_domain_graph
                from shared_platform.r3_common_source_facts import NativeCommonSourceReader
                with runtime.store._connect_readonly() as db:
                    db.execute('BEGIN')
                    graph=read_stored_domain_graph(db,market['plan_id'],native_reader=NativeCommonSourceReader(runtime.store))
                    row=db.execute('SELECT snapshot_json FROM approved_publication_snapshots WHERE plan_id=?',(market['plan_id'],)).fetchone()
                    snapshot=json.loads(row[0]);candidate=json.loads(graph.display_bytes)
                label=next(t for t in market['targets'] if t.startswith('ozon:'))
                runtime.store.begin_target(run['run_id'],label)
                ledger=build_native_budget_ledger(active,platform='OZON',target_label=label,snapshot=snapshot,candidate=candidate)
                original_count=ledger.total_attempt_count
                ledger.reserve_target(label,'import_variant')
                with pytest.raises(Exception,match='UNKNOWN_OPERATION_NO_REDISPATCH'):
                    ledger.reserve_target(label,'import_variant')
                assert ledger.total_attempt_count==original_count+1
                assert len(ledger.target_labels)==1 and len(candidate['target_labels'])!=15
            finally:execution._ACTIVE.reset(token)
        with runtime.store._connect_readonly() as db:
            assert db.execute('SELECT count(*) FROM native_sole_final_decisions').fetchone()[0]==1
    finally:runtime.close()
