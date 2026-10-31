"""Actual new-task graph, native hooks and concrete reader observability."""
from copy import deepcopy
from pathlib import Path
import sqlite3
import pytest
from test_round1_workspace_freeze import live
from test_round1_auto_freeze import public_settings
from owned_new_publication_task import complete_new_task
from shared_platform.publication_execution_package import NativePublicationPreflightBlocked
from shared_platform.publication_execution_preflight import preflight_native_review,inventory_readback_checks


@pytest.mark.parametrize('stocks',[(200,), (160,40), (200,0)])
def test_explicit_complete_inventory_contract_is_not_missing_equals_zero(stocks):
    from test_approved_publication_snapshot import _approved_plan,_warehouse_allocations,_rebind
    from domains.product_operations.approved_publication_snapshot import build_approved_publication_snapshot
    plan=_approved_plan();allocation=_warehouse_allocations()
    for row in allocation.values():
        first=deepcopy(row['warehouses'][0])
        row['warehouses']=[dict(first,warehouse_id=str(int(first['warehouse_id'])+i),warehouse_name='Owned warehouse '+str(i),stock=n)
            for i,n in enumerate(stocks)]
        row['total_stock']=sum(stocks)
    plan['payload']['product_facts']['warehouse_inventory_by_target']=allocation;_rebind(plan)
    snapshot=build_approved_publication_snapshot(plan).payload();blockers=[]
    checks=inventory_readback_checks(snapshot,lambda code,target:blockers.append((code,target)))
    if 0 in stocks:assert all(c[0]=='TIKTOK_ZERO_WAREHOUSE_READBACK_UNSUPPORTED' for c in blockers)
    else:assert not blockers and all(c['status']=='SUPPORTED_EXPLICIT_COMPLETE_ROWS' for c in checks)


def test_actual_new_task_collects_missing_scope_artifact_and_schema_before_nonce(live,monkeypatch,tmp_path):
    runtime,market,*_=complete_new_task(live,monkeypatch,tmp_path)
    try:
        asset=market['payload']['r3_marketplace_binding']['documents']['generation_result']['assets'][0]
        Path(asset['artifact_path']).write_bytes(b'changed owned image')
        with sqlite3.connect(runtime.store.path) as db:db.execute('DROP TRIGGER native_final_budget_outcomes_no_delete')
        before=runtime.store.path.read_bytes()
        with pytest.raises(NativePublicationPreflightBlocked) as caught:runtime.prepare(market['plan_id'])
        report=caught.value.report;codes={r['code'] for r in report['blockers']}
        assert {'IMAGE_BYTES_CHANGED','NATIVE_MUTATION_BUDGET_SCHEMA_NOT_INSTALLED',
            'TIKTOK_COMPLETE_WAREHOUSE_SCOPE_NOT_FROZEN'}<=codes
        assert report['current_provider_permission']=='NOT_ASSERTED'
        with runtime.store._connect_readonly() as db:
            assert db.execute('SELECT COUNT(*) FROM native_sole_final_nonces').fetchone()[0]==0
            assert db.execute('SELECT COUNT(*) FROM native_sole_final_decisions').fetchone()[0]==0
        assert runtime.store.path.read_bytes()==before
    finally:runtime.close()


def test_first_decision_rechecks_actual_preflight_and_preserves_inert_nonce(live,monkeypatch,tmp_path):
    runtime,market,*_=complete_new_task(live,monkeypatch,tmp_path)
    try:
        from shared_platform.native_sole_final_decision import NativeSoleFinalDecisionService
        # An inert real nonce from the old producer cannot bypass the new task
        # gate when resumed through the actual service. No approval is seeded.
        with runtime.operation() as current:
            old=NativeSoleFinalDecisionService(runtime.store,current.actor_reader)
            nonce=old.prepare(market['plan_id'])
        with pytest.raises(NativePublicationPreflightBlocked):
            runtime.decide(nonce=nonce['nonce'],review_digest=nonce['review_digest'])
        with runtime.store._connect_readonly() as db:
            assert db.execute('SELECT COUNT(*) FROM native_sole_final_decisions').fetchone()[0]==0
            assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0]==0
    finally:runtime.close()


def test_stage_and_http_expose_structured_gaps_and_stage_reuses_only_display_hint(live,monkeypatch,tmp_path):
    runtime,market,*_=complete_new_task(live,monkeypatch,tmp_path)
    http=thread=None
    try:
        from shared_platform import publication_execution_preflight as preflight
        original=preflight.preflight_native_review;calls=[]
        def counted(*args,**kwargs):calls.append(1);return original(*args,**kwargs)
        monkeypatch.setattr(preflight,'preflight_native_review',counted)
        stages={'offer_id':market['product_id'],'current_r2_matches_approved':True,'marketplace':{'plan':market}}
        first=runtime.project(stages)['marketplace'];second=runtime.project(stages)['marketplace']
        assert first['final_review_available'] is False and first['blockers']==second['blockers']
        assert any(r['code']=='TIKTOK_COMPLETE_WAREHOUSE_SCOPE_NOT_FROZEN' for r in first['blockers'])
        assert len(calls)==1
        from test_native_sole_final_service import _http,_post
        from shared_platform.native_sole_final_service import PREFIX
        http,thread=_http(runtime)
        code,value=_post(http,PREFIX+'prepare',{'plan_id':market['plan_id']})
        assert code==409 and value['preflight']['status']=='BLOCKED' and value['blockers']
        capabilities=value['preflight']['local_adapter_capabilities']
        assert capabilities['schema_version']=='native-local-publication-capabilities/v1'
        assert all('detail' in gap for row in capabilities['targets'] for gap in row['blockers'])
        assert all(row['local_adapter_supported'] for row in capabilities['targets']
            if row['target_label'].startswith('shopee:'))
        assert len(calls)==2  # Explicit prepare never trusts a cached GET hint.
    finally:
        if http is not None:http.shutdown();thread.join(timeout=5);http.server_close()
        runtime.close()
