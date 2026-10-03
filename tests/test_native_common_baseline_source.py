"""Actual retained baseline and service consumer; no provider or authority grants."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from modules.products import server, release_adapters
from shared_platform import publication_r3_image_bridge as bridge
from shared_platform.native_common_baseline_source import NativeCommonBaselineReader, inspect_service_baseline
from shared_platform.publication_runtime_config import StartupConfig
from shared_platform.r3_common_source_facts import NativeCommonSourceReader
from shared_platform.release_store import PLAN_PENDING_APPROVAL
from shared_platform import native_common_technical_execution as technical
from test_round1_workspace_freeze import live
from test_native_common_retained_review_graph import _completed
from test_native_common_technical_execution import _setup, _reserve, _successor, _retain_owned_comparison, _recover
from test_native_common_write_census import _closed_comparison, _historical_target
from test_native_common_signing_context import _config


def _service_config(tmp_path, monkeypatch):
    signing = _config(tmp_path)
    relative = 'config/product_publication_autopilot_policy.json'
    (signing.root/relative).write_bytes((Path(__file__).resolve().parents[1]/relative).read_bytes())
    config = StartupConfig(signing.root, relative, 'unused-incidents', True, True)
    monkeypatch.setattr(server, '_COMMON_SIGNING_CONTEXT_CONFIG', None)
    server._install_service_common_signing_context(config)
    return config


def _read(store, plan_id, run_id):
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        return NativeCommonSourceReader(store).read_completed_baseline(db, plan_id, run_id)


def test_completed_native_source_is_readable_without_new_edit_or_common_approval(live, monkeypatch, tmp_path):
    store, plan, payload, facts, run_id = _completed(live, monkeypatch, tmp_path)
    _service_config(tmp_path, monkeypatch)
    before = store.path.read_bytes()
    result = _read(store, plan['plan_id'], run_id).diagnostic()
    assert result['status'] == 'RETAINED_NATIVE_COMMON_BASELINE', result
    assert result['completion_state'] == 'CONFIRMED_WRITE'
    assert result['preparation_root_id'] == facts.root_id
    assert result['maximum_confirmed_writes'] == result['local_confirmed_writes'] == 1
    assert result['local_readonly_reuses'] == 0
    assert result['new_common_edit_needed'] is result['new_common_human_approval_needed'] is False
    assert result['observed_technical_schema'] == 'MATCHING_NATIVE_TECHNICAL_SCHEMA'
    assert result['service_write_path_coverage'] == result['schema_installation_receipt'] == 'UNKNOWN'
    assert result['edit_endpoint_permission'] == result['provider_account_authority'] == 'UNKNOWN'
    assert result['execution_authority'] is False and store.get_plan(plan['plan_id'])['status'] == PLAN_PENDING_APPROVAL
    assert store.path.read_bytes() == before


def test_exact_readonly_reuse_keeps_one_confirmed_write_and_validates_original_predecessor(live, monkeypatch, tmp_path):
    store, plan, payload, facts, old_run = _completed(live, monkeypatch, tmp_path)
    _service_config(tmp_path, monkeypatch)
    successor, next_payload, next_facts = _successor(store, payload, 'baseline-readonly')
    reused = _reserve(store, next_facts, readonly_reuse=True)
    source = _closed_comparison(next_payload, monkeypatch)
    summary = {k:v for k,v in source.items() if k not in {'native_common_observation','stored_common_lineage'}}
    target = store.get_run(old_run)['targets'][0]
    summary['predecessor'] = {'plan_id':plan['plan_id'], 'run_id':old_run,
        'payload_digest':plan['payload_digest'], 'common_status':target['status'],
        'common_external_id':target['external_id'],
        'common_readback_evidence_digest':target['readback']['evidence_digest'],
        'common_readback_verified_at':target['readback']['verified_at']}
    _retain_owned_comparison(store, reused['run_id'], release_adapters.bind_native_common_readback(source, summary))
    assert _recover(store, next_facts, reused['run_id'])['state'] == 'READONLY_REUSE'
    before = store.path.read_bytes()
    result = _read(store, successor['plan_id'], reused['run_id'])
    assert result.status == 'RETAINED_NATIVE_COMMON_BASELINE' and result.completion_state == 'READONLY_REUSE'
    assert result.local_confirmed_writes == result.local_readonly_reuses == 1
    assert store.path.read_bytes() == before
    with technical._existing_transaction(store) as db:
        db.execute('UPDATE release_target_readbacks SET evidence_json=? WHERE run_id=?', ('{}', old_run))
    assert _read(store, successor['plan_id'], reused['run_id']).status == 'UNKNOWN'


def test_legacy_active_unknown_still_blocks_a_completed_new_baseline(live, monkeypatch, tmp_path):
    store, plan, payload, facts, run_id = _completed(live, monkeypatch, tmp_path)
    _service_config(tmp_path, monkeypatch)
    legacy = deepcopy(payload)
    legacy['r3_stage_binding'].pop('native_preparation_source')
    legacy['content_package_id'] += ':legacy-active'
    legacy['plan_id'] = bridge.common_stage_plan_id(legacy, offer_id=legacy['product_id'])
    legacy_plan = store.create_plan(legacy)
    _historical_target(store, legacy_plan)
    before = store.path.read_bytes()
    result = _read(store, plan['plan_id'], run_id)
    assert result.status == 'UNKNOWN' and result.reason == 'COMMON_BASELINE_ACTIVE_RESULT_REQUIRES_RECONCILIATION'
    assert store.path.read_bytes() == before


@pytest.mark.parametrize('damage', ['missing-policy', 'changed-app', 'retained-bytes', 'unfinished-run'])
def test_missing_or_changed_native_facts_do_not_become_a_completed_baseline(live, monkeypatch, tmp_path, damage):
    store, plan, payload, facts, run_id = _completed(live, monkeypatch, tmp_path)
    config = _service_config(tmp_path, monkeypatch)
    if damage == 'missing-policy': (config.root/config.policy).unlink()
    elif damage == 'changed-app':
        path = config.root/'config/miaoshou.local.json'
        value = json.loads(path.read_bytes()); value['app_id'] += '-changed'
        path.write_text(json.dumps(value), encoding='utf-8')
    elif damage == 'retained-bytes':
        with technical._existing_transaction(store) as db:
            db.execute('UPDATE release_target_readbacks SET evidence_json=? WHERE run_id=?', ('{}', run_id))
    else:
        with technical._existing_transaction(store) as db:
            db.execute("UPDATE release_runs SET status='RUNNING' WHERE run_id=?", (run_id,))
    before = store.path.read_bytes()
    assert _read(store, plan['plan_id'], run_id).status == 'UNKNOWN'
    assert store.path.read_bytes() == before


def test_without_service_context_or_with_caller_receipt_no_source_is_created(live, monkeypatch, tmp_path):
    store, plan, payload, facts, run_id = _completed(live, monkeypatch, tmp_path)
    monkeypatch.setattr(server, '_COMMON_SIGNING_CONTEXT_CONFIG', None)
    before = store.path.read_bytes()
    result = _read(store, plan['plan_id'], run_id)
    assert result.reason == 'COMMON_BASELINE_SERVICE_READER_REQUIRED'
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        forged = inspect_service_baseline({'approved':True, 'coverage':'complete'}, db, plan['plan_id'], run_id)
        assert forged.status == 'UNKNOWN' and forged.reason == result.reason
    assert store.path.read_bytes() == before
