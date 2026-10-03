"""Owned historical ledger mechanics, not native COMMON mutation authority.

Existing ReleaseStore approval/run fixtures are used only to populate a real
FK-valid historical database. No service admission is replaced or opened, no
provider edit is called, and every count explicitly remains local observation.
"""
from copy import deepcopy
import json

import pytest

from modules.products import server, release_adapters
from modules.miaoshou import client
from shared_platform import native_common_budget_facts as facts
from shared_platform import publication_r3_image_bridge as bridge
from shared_platform import publication_common_write_admission as admission
from shared_platform import operations_publication_common as common
from shared_platform import release_store
from test_common_native_preparation_source import _native_common_chain
from test_round1_workspace_freeze import live
from test_common_detail_observation import CONFIG, LocalResponse, install_response
from test_service_common_observation import ClosedCommonTransport


def _origin(live, monkeypatch):
    _, task, _, _ = _native_common_chain(live, monkeypatch)
    store = server._release_store()
    payload = common._read(server, task)['common']['plan']['payload']
    return store, store.create_plan(payload), payload


def _historical_target(store, plan):
    # Reuse the original test-only ledger mechanics. This approval is never
    # read as standing policy/account authority by the new census or admission.
    store.approve_plan(plan['plan_id'], approved_by='Kyle', user_approved=True,
                       confirmation_token=plan['confirmation_token'])
    run = store.start_run(plan['plan_id'])
    store.begin_target(run['run_id'], 'miaoshou:COMMON')
    return run


def _closed_comparison(payload, monkeypatch):
    detail = ClosedCommonTransport.detail_for(payload)
    raw = json.dumps({'result': 'success', 'data': {
        'editCommonCollectBoxDetail': detail}}, ensure_ascii=False).encode()
    calls = install_response(monkeypatch, LocalResponse(raw))
    evidence = release_adapters.readback_miaoshou_common(payload,
        observation_reader=client.NativeCommonDetailObserver(CONFIG))
    assert evidence['verified'] and calls == [{'commonCollectBoxDetailId': int(payload['product_id'])}]
    assert evidence['external_writes_performed'] == []
    return evidence


def _normal_historical_evidence(payload, monkeypatch):
    source = _closed_comparison(payload, monkeypatch)
    comparison = {key: value for key, value in source.items()
                  if key not in {'native_common_observation', 'stored_common_lineage'}}
    comparison.update(mode='historical-confirmed-write',
                      external_writes_performed=['miaoshou:COMMON:immutable_plan_write'])
    # Original production binder rebinds a retained real comparison packet to
    # the historical summary; this does not claim a provider write happened now.
    return release_adapters.bind_native_common_readback(source, comparison)


def _census(store, plan):
    diagnostic = admission.inspect_common_write_admission(plan, store=store)
    assert diagnostic['status'] == 'BLOCKED' and diagnostic['execution_authority'] is False
    assert 'COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN' in diagnostic['blockers']
    return diagnostic['source_facts']['native_local_census']


def test_same_store_unstarted_native_plan_reads_root_without_future_graph(live, monkeypatch):
    store, plan, payload = _origin(live, monkeypatch)
    before = store.path.read_bytes()
    census = _census(store, plan)
    assert census['status'] == 'LOCAL_PERSISTED_CENSUS'
    assert census['local_observed_confirmed_writes'] == 0
    assert census['confirmed_write_count'] == census['budget_authority'] == 'UNKNOWN'
    assert census['preparation_root'] == payload['r3_stage_binding']['native_preparation_source']['preparation_root']
    assert census['unresolved_attempts'] == [] and store.path.read_bytes() == before


def test_actual_persisted_readback_counts_confirmed_write_not_attempt_or_new_plan(live, monkeypatch):
    store, plan, payload = _origin(live, monkeypatch)
    run = _historical_target(store, plan)
    evidence = _normal_historical_evidence(payload, monkeypatch)
    store.record_target_success(run['run_id'], 'miaoshou:COMMON',
                               external_id=payload['product_id'], readback_evidence=evidence)
    successor = deepcopy(payload)
    successor['content_package_id'] += ':technical-rebind'
    successor['plan_id'] = bridge.common_stage_plan_id(successor, offer_id=payload['product_id'])
    rebound = store.create_plan(successor)
    census = _census(store, rebound)
    assert rebound['plan_id'] != plan['plan_id']
    assert census['local_observed_confirmed_writes'] == 1
    assert census['local_observed_readonly_reuses'] == 0 and census['unresolved_attempts'] == []
    assert census['confirmed_write_count'] == 'UNKNOWN' and census['execution_authority'] is False
    assert rebound['payload']['r3_stage_binding']['native_preparation_source']['preparation_root'] == payload['r3_stage_binding']['native_preparation_source']['preparation_root']
    live['restart']()
    assert _census(store, store.get_plan(rebound['plan_id']))['local_observed_confirmed_writes'] == 1


def test_running_attempt_remains_unknown_before_any_readback_and_after_restart(live, monkeypatch):
    store, plan, payload = _origin(live, monkeypatch)
    run = _historical_target(store, plan)
    before = _census(store, plan)
    assert before['local_observed_confirmed_writes'] == 0
    assert before['unresolved_attempts'] == [{'run_id': run['run_id'], 'attempt': 1,
                                             'same_preparation_root': True, 'status': 'RUNNING'}]
    live['restart']()
    assert _census(store, store.get_plan(plan['plan_id']))['unresolved_attempts'] == before['unresolved_attempts']


def test_real_readonly_reuse_keeps_one_write_and_retained_predecessor(live, monkeypatch):
    store, plan, payload = _origin(live, monkeypatch)
    old_run = _historical_target(store, plan)
    store.record_target_success(old_run['run_id'], 'miaoshou:COMMON',
        external_id=payload['product_id'], readback_evidence=_normal_historical_evidence(payload, monkeypatch))
    old_target = store.get_run(old_run['run_id'])['targets'][0]
    successor = deepcopy(payload)
    successor['content_package_id'] += ':readonly-reuse'
    successor['plan_id'] = bridge.common_stage_plan_id(successor, offer_id=payload['product_id'])
    rebound = store.create_plan(successor, supersedes_plan_id=plan['plan_id'])
    run = _historical_target(store, rebound)
    source = _closed_comparison(successor, monkeypatch)
    comparison = {key: value for key, value in source.items()
                  if key not in {'native_common_observation', 'stored_common_lineage'}}
    comparison['predecessor'] = {
        'plan_id': plan['plan_id'], 'run_id': old_run['run_id'], 'payload_digest': plan['payload_digest'],
        'common_status': old_target['status'], 'common_external_id': old_target['external_id'],
        'common_readback_evidence_digest': old_target['readback']['evidence_digest'],
        'common_readback_verified_at': old_target['readback']['verified_at']}
    evidence = release_adapters.bind_native_common_readback(source, comparison)
    store.record_target_success(run['run_id'], 'miaoshou:COMMON',
                               external_id=payload['product_id'], readback_evidence=evidence)
    census = _census(store, rebound)
    assert evidence['mode'] == 'readback_reuse_no_write' and evidence['external_writes_performed'] == []
    assert census['local_observed_confirmed_writes'] == census['local_observed_readonly_reuses'] == 1
    assert census['unresolved_attempts'] == [] and census['budget_authority'] == 'UNKNOWN'


def test_legacy_active_unknown_cannot_be_erased_by_native_root_or_new_request(live, monkeypatch):
    store, plan, payload = _origin(live, monkeypatch)
    legacy = deepcopy(payload)
    legacy['r3_stage_binding'].pop('native_preparation_source')
    legacy['content_package_id'] += ':legacy-unbound'
    legacy['plan_id'] = bridge.common_stage_plan_id(legacy, offer_id=legacy['product_id'])
    legacy_plan = store.create_plan(legacy)
    run = _historical_target(store, legacy_plan)
    census = _census(store, plan)
    assert census['unresolved_attempts'] == [{'run_id': run['run_id'], 'attempt': 1,
        'same_preparation_root': None, 'status': 'RUNNING'}]
    successor = deepcopy(payload)
    successor['content_package_id'] += ':cannot-clear-unknown'
    successor['plan_id'] = bridge.common_stage_plan_id(successor, offer_id=payload['product_id'])
    with pytest.raises(release_store.ReleaseAuthorizationError, match='COMMON_RECONCILIATION_REQUIRED'):
        store.create_plan(successor)
    assert census['local_observed_confirmed_writes'] == 0 and census['confirmed_write_count'] == 'UNKNOWN'


def test_old_normalized_success_remains_unclassified_not_confirmed_or_authority(live, monkeypatch):
    store, plan, payload = _origin(live, monkeypatch)
    run = _historical_target(store, plan)
    evidence = _normal_historical_evidence(payload, monkeypatch)
    evidence.pop('native_common_observation')
    store.record_target_success(run['run_id'], 'miaoshou:COMMON',
                               external_id=payload['product_id'], readback_evidence=evidence)
    census = _census(store, plan)
    assert census['local_observed_confirmed_writes'] == 0
    assert census['confirmed_write_count'] == 'UNKNOWN'
    assert len(census['unclassified_history']) == 1 and census['unresolved_attempts'] == []


def test_forged_root_is_rejected_by_same_store_preparation_reread(live, monkeypatch):
    store, plan, payload = _origin(live, monkeypatch)
    bad = deepcopy(payload)
    bad['r3_stage_binding']['native_preparation_source']['preparation_root']['root_id'] = 'r1-cycle:' + '0' * 32
    bad['plan_id'] = bridge.common_stage_plan_id(bad, offer_id=bad['product_id'])
    persisted = store.create_plan(bad)
    before = store.path.read_bytes()
    diagnostic = admission.inspect_common_write_admission(persisted, store=store)
    assert diagnostic['status'] == 'BLOCKED' and diagnostic['execution_authority'] is False
    assert 'COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN' in diagnostic['blockers']
    source = diagnostic['source_facts']
    assert source['status'] == 'INVALID' and source['reason'] == 'COMMON_SOURCE_ORIGIN_IDENTITY_INVALID'
    assert source['execution_authority'] is False and 'native_local_census' not in source
    assert diagnostic['external_writes_performed'] == []
    assert store.path.read_bytes() == before


def test_declared_snapshot_cannot_substitute_a_different_prepared_review(live, monkeypatch):
    store, plan, payload = _origin(live, monkeypatch)
    bad = deepcopy(payload)
    snapshot = bad['r3_stage_binding']['native_preparation_source']['round1_snapshot']
    snapshot['first_review_digest'] = 'sha256:' + '0' * 64
    from shared_platform import publication_rounds as rounds
    snapshot['snapshot_digest'] = rounds.canonical_digest(
        {key: value for key, value in snapshot.items() if key != 'snapshot_digest'})
    source = bad['r3_stage_binding']['native_preparation_source']
    source['round1_snapshot_digest'] = snapshot['snapshot_digest']
    bad['r3_stage_binding']['round1_snapshot_digest'] = snapshot['snapshot_digest']
    bad['r3_stage_binding']['r2_identity']['round1_snapshot_digest'] = snapshot['snapshot_digest']
    identity = bad['r3_stage_binding']['r2_identity']
    identity['identity_digest'] = bridge._canonical_digest(
        {key: value for key, value in identity.items() if key != 'identity_digest'})
    bad['plan_id'] = bridge.common_stage_plan_id(bad, offer_id=bad['product_id'])
    persisted = store.create_plan(bad)
    before = store.path.read_bytes()
    diagnostic = admission.inspect_common_write_admission(persisted, store=store)
    assert diagnostic['status'] == 'BLOCKED' and diagnostic['execution_authority'] is False
    assert 'COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN' in diagnostic['blockers']
    source = diagnostic['source_facts']
    assert source['status'] == 'INVALID' and source['reason'] == 'COMMON_SOURCE_ORIGIN_IDENTITY_INVALID'
    assert source['execution_authority'] is False and 'native_local_census' not in source
    assert diagnostic['external_writes_performed'] == []
    assert store.path.read_bytes() == before


@pytest.mark.parametrize('existing', [False, True])
def test_source_reader_does_not_create_wrong_or_missing_store(live, monkeypatch, tmp_path, existing):
    _, _, _ = _origin(live, monkeypatch)
    from modules.sourcing import new_product_workbench as workbench
    docs = bridge.load_r2_documents(live['offer'])
    wrong = release_store.ReleaseStore(tmp_path / 'not-created.db')
    if existing:
        import sqlite3
        with sqlite3.connect(wrong.path) as db:
            db.execute('CREATE TABLE unrelated(value TEXT)')
    before = wrong.path.read_bytes() if existing else None
    with pytest.raises(ValueError, match='COMMON_NATIVE_PREPARATION_SOURCE_UNAVAILABLE'):
        facts.read_preparation_source(wrong, docs, workbench.load_state(live['offer']))
    assert wrong.path.read_bytes() == before if existing else not wrong.path.exists()
