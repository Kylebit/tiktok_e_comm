"""Actual service GET consumes retained facts, never a grant or another review.

The existing owned transaction fixture records completion with closed response
bytes. It is mechanics evidence only; no test claims a native provider grant.
"""
from copy import deepcopy
import http.client
import json
from urllib.parse import urlencode

import pytest

from modules.products import server
from shared_platform import publication_r3_image_bridge as bridge
from shared_platform import native_common_technical_execution as technical
from shared_platform.native_common_baseline_source import NativeCommonBaselineReader
from shared_platform.native_common_signing_context import NativeCommonSigningContextReader
from shared_platform.r3_frozen_review_producer import DomainFrozenReviewProducer
from shared_platform.release_store import ReleaseStore, PLAN_PENDING_APPROVAL
from test_round1_workspace_freeze import live
from test_native_common_retained_review_graph import _completed, _market
from test_native_common_baseline_source import _service_config
from test_native_common_write_census import _historical_target


def _get(live, market):
    connection = http.client.HTTPConnection('127.0.0.1', live['port'], timeout=15)
    try:
        connection.request('GET', '/api/product-workspace/publication-stages?' +
            urlencode({'offer_id':live['offer'], 'plan_id':market['plan_id']}))
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def _no_new_write_or_current_read(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('A retained GET must not create a plan or request a new provider read')
    monkeypatch.setattr(ReleaseStore, 'create_plan', forbidden)
    monkeypatch.setattr(NativeCommonSigningContextReader, 'read_current', forbidden)


def test_actual_getter_recovers_complete_matrix_from_service_owned_baseline_in_one_snapshot(live, monkeypatch, tmp_path):
    store, plan, payload, facts, run_id = _completed(live, monkeypatch, tmp_path)
    market = _market(store, plan, run_id)
    _service_config(tmp_path, monkeypatch)
    _no_new_write_or_current_read(monkeypatch)
    calls = []
    original_read = NativeCommonBaselineReader.read
    original_inspect = DomainFrozenReviewProducer.inspect
    def observe_read(reader, db, plan_id, common_run_id):
        assert db.in_transaction and db.execute('PRAGMA query_only').fetchone()[0] == 1
        assert reader._store.path == store.path
        calls.append(('baseline', db, plan_id, common_run_id))
        return original_read(reader, db, plan_id, common_run_id)
    def observe_inspect(producer, db, common_run_id, market_id):
        assert calls and db is calls[0][1]
        assert db.in_transaction and db.execute('PRAGMA query_only').fetchone()[0] == 1
        calls.append(('matrix', db, market_id, common_run_id))
        return original_inspect(producer, db, common_run_id, market_id)
    monkeypatch.setattr(NativeCommonBaselineReader, 'read', observe_read)
    monkeypatch.setattr(DomainFrozenReviewProducer, 'inspect', observe_inspect)
    before = store.path.read_bytes()
    code, value = _get(live, market)
    assert code == 200, value
    common, candidate = value['common'], value['marketplace']
    assert common['status'] == 'RETAINED_TECHNICAL_BASELINE', value
    assert common['run']['run_id'] == run_id and common['plan']['status'] == PLAN_PENDING_APPROVAL
    baseline = common['retained_baseline_facts']
    assert baseline['preparation_root_id'] == facts.root_id
    assert baseline['local_confirmed_writes'] == baseline['maximum_confirmed_writes'] == 1
    assert baseline['local_readonly_reuses'] == 0
    assert baseline['current_signed_readback'] == baseline['edit_endpoint_permission'] == 'UNKNOWN'
    assert baseline['schema_installation_receipt'] == baseline['service_write_path_coverage'] == 'UNKNOWN'
    assert baseline['execution_authority'] is False
    assert common['new_common_human_approval_needed'] is False
    assert common['user_status']['summary'] == 'COMMON 已完成，回读结果已保存'
    assert common['user_status']['candidate_summary'] == '完整候选资料已恢复'
    assert common['user_status']['common_review_needed'] is False
    assert common['technical_details']['retained_baseline'] == baseline
    assert candidate['status'] == 'INERT_COMPLETE_CANDIDATE', value
    assert candidate['manifest']['variants'] and candidate['manifest']['copy_sets'] and candidate['manifest']['image_sets']
    assert [row['target_label'] for row in candidate['manifest']['targets']] == market['targets']
    assert candidate['candidate'] and candidate['candidate_digest']
    assert candidate['final_review_available'] is candidate['execution_authority'] is False
    assert candidate['final_review']['approval_recorded'] is candidate['final_review']['execution_recorded'] is False
    assert not {'nonce', 'approval_saved', 'review_digest'} & set(candidate)
    assert [item[0] for item in calls] == ['baseline', 'matrix']
    with store._connect_readonly() as db:
        assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0] == 0
    assert value['external_writes_performed'] == [] and store.path.read_bytes() == before


@pytest.mark.parametrize('damage', ['missing-context', 'changed-app', 'retained-bytes', 'unfinished-run', 'active-cross-root'])
def test_actual_getter_does_not_promote_changed_or_unfinished_baseline(live, monkeypatch, tmp_path, damage):
    store, plan, payload, facts, run_id = _completed(live, monkeypatch, tmp_path)
    market = _market(store, plan, run_id)
    config = _service_config(tmp_path, monkeypatch)
    if damage == 'missing-context':
        monkeypatch.setattr(server, '_COMMON_SIGNING_CONTEXT_CONFIG', None)
    elif damage == 'changed-app':
        path = config.root/'config/miaoshou.local.json'
        value = json.loads(path.read_bytes())
        value['app_id'] += '-changed'
        path.write_text(json.dumps(value), encoding='utf-8')
    elif damage == 'retained-bytes':
        with technical._existing_transaction(store) as db:
            db.execute('UPDATE release_target_readbacks SET evidence_json=? WHERE run_id=?', ('{}', run_id))
    elif damage == 'unfinished-run':
        with technical._existing_transaction(store) as db:
            db.execute("UPDATE release_runs SET status='RUNNING' WHERE run_id=?", (run_id,))
    else:
        legacy = deepcopy(payload)
        legacy['r3_stage_binding'].pop('native_preparation_source')
        legacy['content_package_id'] += ':getter-legacy-active'
        legacy['plan_id'] = bridge.common_stage_plan_id(legacy, offer_id=legacy['product_id'])
        legacy_plan = store.create_plan(legacy)
        _historical_target(store, legacy_plan)
    _no_new_write_or_current_read(monkeypatch)
    before = store.path.read_bytes()
    code, value = _get(live, market)
    assert code == 200, value
    common, candidate = value['common'], value['marketplace']
    assert common['status'] == 'TECHNICAL_CONDITIONS_UNKNOWN', value
    assert common['retained_baseline_facts']['status'] == candidate['status'] == 'UNKNOWN'
    assert common['new_common_human_approval_needed'] is common['user_status']['common_review_needed'] is False
    assert common['user_status']['pending_items']
    if damage == 'active-cross-root':
        assert common['retained_baseline_facts']['reason'] == 'COMMON_BASELINE_ACTIVE_RESULT_REQUIRES_RECONCILIATION'
        assert common['user_status']['pending_items'] == ['既有请求结果对账']
    assert not {'candidate', 'manifest', 'nonce'} & set(candidate)
    assert candidate['final_review_available'] is candidate['execution_authority'] is False
    assert candidate['final_review']['approval_recorded'] is False
    assert value['external_writes_performed'] == [] and store.path.read_bytes() == before


def test_retained_service_getter_context_does_not_create_an_absent_store_or_accept_a_caller(live, monkeypatch, tmp_path):
    store, plan, payload, facts, run_id = _completed(live, monkeypatch, tmp_path)
    _service_config(tmp_path, monkeypatch)
    missing = ReleaseStore(tmp_path/'not-created.sqlite3')
    _no_new_write_or_current_read(monkeypatch)
    for supplied in (missing, {'path':store.path, 'coverage':'complete', 'approved':True}):
        result, run, display = server._retained_native_common_stage_facts(supplied, plan)
        assert result['status'] == 'UNKNOWN' and result['reason'] == 'COMMON_BASELINE_PERSISTED_PLAN_REQUIRED'
        assert result['execution_authority'] is False and run is display is None
    assert not missing.path.exists()
