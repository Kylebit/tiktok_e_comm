from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import pytest

from modules.products import server
from shared_platform.product_publication_runner import ProductPublicationRunner, PublicationRunReceipt
from shared_platform.product_publication_reports import publication_report_id
from shared_platform.publication_write_budget import PublicationWriteBudgetLedger
from test_b4b_publication_preview import reviewed_marketplace, seed_preexisting_marketplace_approval
from test_r3_config_diagnostics import handler_request


def approved_context(tmp_path, monkeypatch):
    _, _, store, data, common_io, preview = reviewed_marketplace(tmp_path, monkeypatch)
    seed_preexisting_marketplace_approval(store, data, preview)
    status, result = server._resume_r3_marketplace_stage(data)
    assert status == 200, result
    market = result['marketplace']
    snapshot = store.approved_publication_snapshot(offer_id=data['offer_id'], plan_id=data['plan_id'])
    return store, data, common_io, market, snapshot


def seed_historical_report(store, data, market, snapshot, *, run_id='status-run', platform='TIKTOK', state='PROCESSING', unknown=False, count=2, selected=None, manual=False):
    """Restore immutable synthetic prior-run facts for projection/closure reads.

    This is report storage, not a new publication. In particular, an approved
    historical R3 plan cannot enter the legacy publication runner after the
    registered-R3 admission change. These tests cover reading and reconciling
    persisted evidence, not runner-to-projection execution or provider I/O.
    """
    runs = server._product_publication_run_store()
    labels = [label for label in market['targets'] if label.split(':')[0].upper() == platform]
    if selected is not None: labels = [label for label in labels if label in selected]
    identity = {'skill_digest':'1'*64,'git_commit':'2'*40,'code_digest':'3'*64}
    run = runs.create_run(run_id=run_id, offer_id=data['offer_id'], revision=snapshot['product_revision'],
        plan_id=data['plan_id'], snapshot_digest=snapshot['snapshot_digest'], platform_scope=(platform,),
        target_count=len(labels), execution_identity=identity)
    runs.mark_running(run_id=run_id)
    ledger = PublicationWriteBudgetLedger(platform=platform, target_labels=labels,
        budget=market['candidate']['write_budget'][platform])
    if count:
        ledger.reserve_shared('synthetic_prefix', count=count)
    report = {
        'schema_version':'product-publication-report/v2',
        'report_id':publication_report_id(run_id), 'run_id':run_id,
        'offer_id':data['offer_id'], 'revision':snapshot['product_revision'],
        'plan_id':data['plan_id'],
        'snapshot':{'schema_version':'approved-publication-snapshot/v4','digest':snapshot['snapshot_digest']},
        'execution_identity':identity,
        'targets':[{'target_label':label,'status':state,'evidence':{'target_label':label,'status':state,
            'stage':'PUBLISH','provider_code':'MANUAL_HANDOFF_ACCEPTED' if manual else 'OUTCOME_UNKNOWN' if unknown else 'ACCEPTED',
            'provider_reason':'Synthetic bounded result','request_attempted':True,
            'outcome_unknown':unknown,'external_write_count':None if unknown else 0}} for label in labels],
        'status':state,
        'mutation_budgets':[ledger.snapshot()],
        'release_authorization':{'candidate_digest':market['candidate']['candidate_digest'],
            'approval_digest':market['approval']['approval_digest']},
        'summary':{'schema_version':'product-publication-summary/v1','overall_status':state,
            'platforms':[{'platform':platform,'status':state,'target_count':len(labels),
                'verified_count':len(labels) if state=='PUBLISHED' else 0,
                'processing_count':len(labels) if state=='PROCESSING' else 0,
                'failed_count':len(labels) if state=='FAILED' else 0}],
            'evidence':{'snapshot_verified':True,'dispatch_attempted':True,
                'readback_completed':not unknown,'external_write_count':None if unknown else count},
            'requires_human_action':unknown or state=='FAILED'},
    }
    reports=server._product_publication_report_store()
    stored=reports.store_report(report)
    persisted=reports.get_report(report_id=stored.report_id, offer_id=data['offer_id'])
    assert persisted is not None
    receipt=PublicationRunReceipt(report=persisted, stored=stored, replayed=False)
    runs.mark_completed(run_id=run_id,final_report_id=run.report_id)
    return receipt


def state(data):
    status,result = handler_request('/api/product-workspace/publication-stages?offer_id='+data['offer_id'])
    assert status == 200, result
    return result['marketplace']


def test_historical_processing_readback_is_stable_without_runner_dispatch(tmp_path,monkeypatch):
    store,data,io,market,snapshot = approved_context(tmp_path,monkeypatch)
    monkeypatch.setattr(ProductPublicationRunner, 'run',
        lambda *args, **kwargs: pytest.fail('historical report fixture dispatched the legacy runner'))
    seed_historical_report(store,data,market,snapshot)
    projected = state(data)
    assert projected['status'] == 'RECONCILIATION_REQUIRED'
    assert [row['target_label'] for row in projected['target_results']] == market['targets']
    assert projected['target_results'][0]['lifecycle'] == 'RECONCILIATION_REQUIRED'
    assert projected['target_results'][0]['reported_status'] == 'PROCESSING'
    assert projected['target_results'][0]['readback_completed'] is None
    assert projected['target_results'][0]['reported_readback_completed'] is True
    assert projected['target_results'][1]['status'] == 'NOT_RUN'
    assert projected['execution_summary']['external_write_count'] == 2
    assert io.mutations == 0


def test_no_execution_has_full_approved_scope_and_approval_state(tmp_path,monkeypatch):
    _,data,io,market,_ = approved_context(tmp_path,monkeypatch)
    result = state(data)
    assert result['final_review']['status'] == 'APPROVED'
    assert result['final_review']['approval_recorded'] is True
    assert result['final_review']['execution_recorded'] is False
    assert [t['target_label'] for t in result['target_results']] == market['targets']
    assert all(t['status']=='NOT_RUN' and t['outcome_unknown'] is None for t in result['target_results'])
    assert io.mutations == 0


def test_newer_failure_wins_over_older_success(tmp_path,monkeypatch):
    store,data,io,market,snapshot = approved_context(tmp_path,monkeypatch)
    seed_historical_report(store,data,market,snapshot,run_id='older',state='PUBLISHED')
    seed_historical_report(store,data,market,snapshot,run_id='newer',state='FAILED')
    result = state(data);target=result['target_results'][0]
    # The older synthetic PUBLISHED claim still lacks target observation. Keep
    # the newer failure visible without silently resolving that older history.
    assert target['status']=='RECONCILIATION_REQUIRED' and target['source']['run_id']=='newer'
    assert target['reported_status']=='FAILED'
    assert 'EARLIER_RUN_UNRESOLVED' in target['blockers']
    assert [x['source']['run_id'] for x in target['history']]==['newer','older']
    assert result['execution_summary']['external_write_count']==4


def test_old_unknown_is_not_erased_by_later_success(tmp_path,monkeypatch):
    store,data,io,market,snapshot = approved_context(tmp_path,monkeypatch)
    seed_historical_report(store,data,market,snapshot,run_id='unknown',state='FAILED',unknown=True)
    seed_historical_report(store,data,market,snapshot,run_id='later',state='PUBLISHED')
    result=state(data);target=result['target_results'][0]
    assert target['status']=='RECONCILIATION_REQUIRED' and not target['official_success']
    assert 'EARLIER_RUN_UNRESOLVED' in target['blockers']
    assert result['execution_summary']['external_write_count'] is None
    assert result['execution_summary']['runs'][1]['mutation_budgets'][0]['attempts']['total']==2


@pytest.mark.parametrize('damage',['missing','malformed','summary_digest'])
def test_corrupt_report_cannot_revive_older_success(tmp_path,monkeypatch,damage):
    store,data,io,market,snapshot = approved_context(tmp_path,monkeypatch)
    seed_historical_report(store,data,market,snapshot,run_id='older',state='PUBLISHED')
    receipt=seed_historical_report(store,data,market,snapshot,run_id='newer',state='FAILED')
    reports=server._product_publication_report_store()
    path=reports.reports_root/receipt.stored.report_path
    if damage=='missing':path.unlink()
    elif damage=='malformed':path.write_text('{broken')
    else:
        with sqlite3.connect(reports.path) as db:db.execute('UPDATE product_publication_reports SET summary_digest=? WHERE run_id=?',('bad','newer'))
    result=state(data)
    assert result['target_results'][0]['status']=='RECONCILIATION_REQUIRED'
    assert not result['target_results'][0]['official_success']
    assert result['execution_summary']['external_write_count'] is None


def test_missing_terminal_report_and_same_offer_other_plan_are_separate(tmp_path,monkeypatch):
    store,data,io,market,snapshot=approved_context(tmp_path,monkeypatch)
    runs=server._product_publication_run_store()
    for run_id,plan_id in [('missing',data['plan_id']),('foreign','another-plan')]:
        runs.create_run(run_id=run_id,offer_id=data['offer_id'],revision=snapshot['product_revision'],plan_id=plan_id,
            snapshot_digest=snapshot['snapshot_digest'],platform_scope=('TIKTOK',),target_count=1)
        runs.mark_failed(run_id=run_id,failure_code='WORKER_STOPPED')
    result=state(data)
    assert result['execution_summary']['run_count']==1
    assert result['target_results'][0]['blockers']==['TERMINAL_RUN_REPORT_MISSING']
    assert result['target_results'][1]['status']=='NOT_RUN'


def test_read_projection_keeps_original_approval_after_current_evidence_changes(tmp_path,monkeypatch):
    from shared_platform import publication_r3_image_bridge as bridge
    store,data,io,market,snapshot=approved_context(tmp_path,monkeypatch)
    seed_historical_report(store,data,market,snapshot)
    (tmp_path/'policy.json').unlink()
    (bridge.REPORTS_ROOT/data['offer_id']/bridge.R2_DOCUMENTS['image_qa']).unlink()
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob('*') if p.is_file()}
    result=state(data)
    after={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob('*') if p.is_file()}
    assert before==after
    assert result['final_review']['approval_id']==store.get_plan(data['plan_id'])['approval']['approval_id']
    assert result['status']=='RECONCILIATION_REQUIRED' and io.mutations==0
    assert result['target_results'][0]['reported_status']=='PROCESSING'
    assert result['target_results'][0]['reported_readback_completed'] is True


@pytest.mark.parametrize('packet_present',[True,False])
def test_multitarget_subset_keeps_other_target_failure(tmp_path,monkeypatch,packet_present):
    from shared_platform.release_store import ReleaseStore
    from shared_platform.product_publication_runs import ProductPublicationRunStore
    from shared_platform import publication_autopilot as authority
    from shared_platform.publication_status_projection import project_execution
    from test_b4b_release_compiler import neutral_product_plan,policy,INCIDENTS
    from test_product_publication_runner import _report_store
    from test_b4b_ozon_partial_budget import real_ozon_store
    store,plan,snapshot=real_ozon_store(tmp_path,payload=neutral_product_plan()['payload'])
    candidate=authority.compile_release_candidate(snapshot,policy=policy(),incident_registry=INCIDENTS)
    assert candidate['status']=='READY_FOR_FINAL_REVIEW',candidate['blockers']
    approval=authority.build_final_approval_receipt(candidate,approved_by='Kyle')
    reports=_report_store(tmp_path);runs=ProductPublicationRunStore(tmp_path/'runs.db')
    monkeypatch.setattr(server,'_product_publication_report_store',lambda:reports)
    monkeypatch.setattr(server,'_product_publication_run_store',lambda:runs)
    data={'offer_id':plan['product_id'],'plan_id':plan['plan_id']}
    market={'targets':plan['targets'],'candidate':candidate,'approval':approval}
    seed_historical_report(store,data,market,snapshot,run_id='both-failed',state='FAILED')
    subset=authority.compile_release_candidate(snapshot,policy=policy(),incident_registry=INCIDENTS,
        platform_scope=('TIKTOK',),target_scope=('tiktok:LH_PH',))
    subset_approval=authority.build_final_approval_receipt(subset,approved_by='Kyle')
    authority.persist_release_candidate(subset,reports_root=tmp_path/'authority')
    authority.persist_final_approval_receipt(subset_approval,subset,reports_root=tmp_path/'authority')
    seed_historical_report(store,data,{**market,'candidate':subset,'approval':subset_approval},snapshot,
        run_id='one-recovered',state='PUBLISHED',selected=('tiktok:LH_PH',))
    result=project_execution(plan=plan,snapshot=snapshot,candidate=candidate,approval=approval,run_store=runs,
        report_store=reports,authority_root=tmp_path/('authority' if packet_present else 'missing-authority'))
    indexed={row['target_label']:row for row in result['target_results']}
    assert indexed['tiktok:LH_PH']['status']=='RECONCILIATION_REQUIRED'
    if packet_present:
        assert indexed['tiktok:LH_PH']['reported_status']=='PUBLISHED'
        assert 'TARGET_OFFICIAL_READBACK_UNAVAILABLE' in indexed['tiktok:LH_PH']['blockers']
        assert indexed['tiktok:LH_PH']['source']['run_id']=='one-recovered'
    assert indexed['tiktok:LH_MY']['status']==('FAILED' if packet_present else 'RECONCILIATION_REQUIRED')
    assert len(indexed)==len(plan['targets'])


@pytest.mark.parametrize('running',[False,True])
def test_unfinished_run_is_read_only_and_keeps_unexecuted_platform(tmp_path,monkeypatch,running):
    _,data,_,market,snapshot=approved_context(tmp_path,monkeypatch)
    runs=server._product_publication_run_store()
    runs.create_run(run_id='unfinished',offer_id=data['offer_id'],revision=snapshot['product_revision'],
        plan_id=data['plan_id'],snapshot_digest=snapshot['snapshot_digest'],platform_scope=('TIKTOK',),target_count=1)
    if running:runs.mark_running(run_id='unfinished')
    result=state(data)
    assert result['target_results'][0]['status']==('RUNNING' if running else 'QUEUED')
    assert result['platforms'][0]['next_action']=='READ_EXISTING_RUN'
    assert result['target_results'][1]['status']=='NOT_RUN'
    assert result['execution_summary']['external_write_count'] is None


def test_running_run_before_report_table_exists_projects_processing(tmp_path):
    from shared_platform.product_publication_reports import ProductPublicationReportStore
    from shared_platform.product_publication_runs import ProductPublicationRunStore
    from shared_platform.publication_status_projection import project_execution

    shared_index = tmp_path / 'publication-index.db'
    runs = ProductPublicationRunStore(shared_index)
    reports = ProductPublicationReportStore(
        shared_index,
        reports_root=tmp_path / 'publication-reports',
    )
    plan = {
        'product_id': '3956742887',
        'plan_id': 'omnichannel:running-before-report-index',
        'targets': ['tiktok:LH_PH', 'shopee:PH'],
        'approval': {'approved_at': '2020-01-01T00:00:00+00:00'},
    }
    snapshot = {
        'product_revision': 5,
        'snapshot_digest': 'sha256:' + '1' * 64,
    }
    runs.create_run(
        run_id='running-before-report-index',
        offer_id=plan['product_id'],
        revision=snapshot['product_revision'],
        plan_id=plan['plan_id'],
        snapshot_digest=snapshot['snapshot_digest'],
        platform_scope=('TIKTOK',),
        target_count=1,
    )
    runs.mark_running(run_id='running-before-report-index')
    with sqlite3.connect(shared_index) as db:
        assert db.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'product_publication_reports'"
        ).fetchone() is None

    result = project_execution(
        plan=plan,
        snapshot=snapshot,
        candidate={},
        approval={},
        run_store=runs,
        report_store=reports,
    )

    assert result['status'] == 'PROCESSING'
    assert result['execution_summary']['run_count'] == 1
    assert result['execution_summary']['index_valid'] is True
    assert result['execution_summary']['blockers'] == []
    assert result['target_results'][0]['status'] == 'RUNNING'
    assert result['target_results'][1]['status'] == 'NOT_RUN'
    assert result['execution_summary']['external_write_count'] is None


def test_equal_source_times_require_reconciliation(tmp_path,monkeypatch):
    from datetime import datetime,timezone,timedelta
    from shared_platform import product_publication_runs as runs_module,product_publication_reports as reports_module
    store,data,_,market,snapshot=approved_context(tmp_path,monkeypatch)
    moment=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat()
    monkeypatch.setattr(runs_module,'_utc_now',lambda:moment)
    monkeypatch.setattr(reports_module,'_utc_now',lambda:moment)
    seed_historical_report(store,data,market,snapshot,run_id='equal-a',state='PUBLISHED')
    seed_historical_report(store,data,market,snapshot,run_id='equal-b',state='FAILED')
    target=state(data)['target_results'][0]
    assert target['status']=='RECONCILIATION_REQUIRED'
    assert 'SOURCE_ORDER_CONFLICT' in target['blockers']


def test_manual_handoff_is_not_official_publication(tmp_path,monkeypatch):
    store,data,_,market,snapshot=approved_context(tmp_path,monkeypatch)
    seed_historical_report(store,data,market,snapshot,state='PUBLISHED',manual=True)
    result=state(data)
    assert result['execution_summary']['official_success_count']==0
    assert result['target_results'][0]['blockers']==['MANUAL_IS_NOT_OFFICIAL_SUCCESS']


def test_superseded_plan_keeps_history_without_execution_action(tmp_path,monkeypatch):
    store,data,_,market,snapshot=approved_context(tmp_path,monkeypatch)
    seed_historical_report(store,data,market,snapshot)
    store.supersede_plan(data['plan_id'],reason='Synthetic replacement')
    status,result=handler_request('/api/product-workspace/publication-stages?offer_id='+data['offer_id']+'&plan_id='+data['plan_id'])
    assert status==200,result
    market=result['marketplace']
    assert market['status']=='SUPERSEDED'
    assert market['target_results'][0]['lifecycle']=='RECONCILIATION_REQUIRED'
    assert market['target_results'][0]['reported_status']=='PROCESSING'
    assert all(row['next_action']=='READ_ONLY_HISTORY' for row in market['target_results']+market['platforms'])


@pytest.mark.parametrize('damage',['orphan','run_index','report_index','time'])
def test_index_and_time_conflicts_are_explicit(tmp_path,monkeypatch,damage):
    store,data,_,market,snapshot=approved_context(tmp_path,monkeypatch)
    seed_historical_report(store,data,market,snapshot,state='PUBLISHED')
    runs=server._product_publication_run_store();reports=server._product_publication_report_store()
    if damage=='orphan':
        with sqlite3.connect(runs.path) as db:db.execute('DELETE FROM product_publication_runs')
    elif damage=='run_index':runs.path.write_bytes(b'broken database')
    elif damage=='report_index':reports.path.write_bytes(b'broken database')
    else:
        with sqlite3.connect(reports.path) as db:
            db.execute('UPDATE product_publication_reports SET created_at=?, updated_at=?',
                ('2000-01-01T00:00:00+00:00','2000-01-01T00:00:00+00:00'))
    result=state(data)
    assert result['target_results'][0]['status']=='RECONCILIATION_REQUIRED'
    assert result['execution_summary']['external_write_count'] is None
    assert result['execution_summary']['official_success_count']==0
    if damage.endswith('index'):
        assert result['final_review']['execution_recorded'] is None


@pytest.mark.parametrize('second_unknown',[False,True])
def test_mixed_platforms_keep_independent_result_and_prefix_counts(tmp_path,monkeypatch,second_unknown):
    store,data,_,market,snapshot=approved_context(tmp_path,monkeypatch)
    seed_historical_report(store,data,market,snapshot,run_id='tiktok',state='PUBLISHED')
    second=market['targets'][1].split(':')[0].upper()
    seed_historical_report(store,data,market,snapshot,run_id='second',platform=second,state='FAILED',unknown=second_unknown,count=3)
    result=state(data)
    assert result['target_results'][0]['official_success'] is False
    assert result['target_results'][0]['reported_status']=='PUBLISHED'
    assert result['target_results'][0]['status']=='RECONCILIATION_REQUIRED'
    assert result['target_results'][0]['source']['run_id']=='tiktok'
    assert 'TARGET_OFFICIAL_READBACK_UNAVAILABLE' in result['target_results'][0]['blockers']
    assert result['target_results'][1]['status']==('RECONCILIATION_REQUIRED' if second_unknown else 'FAILED')
    assert result['execution_summary']['external_write_count']==(None if second_unknown else 5)
    assert result['execution_summary']['official_success_count']==0
