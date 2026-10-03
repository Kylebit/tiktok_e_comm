"""Same-run retention and real service READ reuse; no native EDIT grant fixture.

Private facts are used only for the existing transaction mechanics to establish
the owned historical baseline. Public fresh EDIT must reject unavailable native
account/coverage. Successful public continuation performs only closed READ.
"""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest

from modules.products import server, release_adapters
from modules.miaoshou import client
from shared_platform import native_common_technical_execution as technical, release_store
from shared_platform import native_common_edit_boundary as boundary
from shared_platform.native_common_budget_facts import census_same_snapshot
from shared_platform.r3_common_source_facts import NativeCommonSourceReader
from test_round1_workspace_freeze import live
from test_native_common_technical_execution import _setup, _reserve, _consume, _successor
from test_native_common_retained_review_graph import _completed
from test_native_common_baseline_source import _service_config
from test_native_common_write_census import _normal_historical_evidence, _closed_comparison, CONFIG


def _service(monkeypatch, tmp_path, store):
    config = _service_config(tmp_path, monkeypatch)
    monkeypatch.setattr(server, '_COMMON_STANDING_POLICY_READER', None)
    server._install_service_common_standing_policy(config)
    monkeypatch.setattr(server, '_COMMON_DETAIL_OBSERVER_FACTORY',
                        lambda: client.NativeCommonDetailObserver(CONFIG))
    monkeypatch.setattr(release_store, 'default_release_store', lambda: store)
    monkeypatch.setattr(server, '_release_dashboard_for_request',
                        lambda *_: pytest.fail('native COMMON must not require the market approval route'))
    monkeypatch.setattr(client, 'post_open', lambda *_a, **_k: pytest.fail('No EDIT or unsigned transport'))


def _retained(store, run_id):
    with store._connect_readonly() as db:
        return db.execute('SELECT * FROM release_target_readbacks WHERE run_id=?', (run_id,)).fetchone()


def test_original_packet_and_attempt_close_same_run_without_second_human_run(live, monkeypatch, tmp_path):
    store, plan, payload, facts = _setup(live, monkeypatch, tmp_path)
    claim = _reserve(store, facts)
    assert _consume(store, facts, claim['run_id'])['consumed']
    evidence = _normal_historical_evidence(payload, monkeypatch)
    raw_packet = deepcopy(evidence['native_common_observation'])
    result = technical.retain_readback(store, plan['plan_id'], claim['run_id'], 1, evidence)
    assert result['state'] == 'CONFIRMED_WRITE'
    stored = json.loads(_retained(store, claim['run_id'])['evidence_json'])
    assert stored['native_common_observation'] == raw_packet
    assert stored['stored_common_lineage']['run_id'] == claim['run_id']
    assert stored['stored_common_lineage']['attempt'] == 1
    with store._connect_readonly() as db:
        assert db.execute('SELECT COUNT(*) FROM release_runs').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0] == 0
    assert store.get_plan(plan['plan_id'])['status'] == release_store.PLAN_PENDING_APPROVAL


@pytest.mark.parametrize('change', ['attempt', 'run', 'packet', 'comparison'])
def test_wrong_attempt_or_changed_packet_rolls_back_readback_and_keeps_unknown(live, monkeypatch, tmp_path, change):
    store, plan, payload, facts = _setup(live, monkeypatch, tmp_path)
    claim = _reserve(store, facts);_consume(store, facts, claim['run_id'])
    evidence = _normal_historical_evidence(payload, monkeypatch)
    run, attempt = claim['run_id'], 1
    if change == 'attempt':attempt = 2
    elif change == 'run':run = 'another-native-run'
    elif change == 'packet':evidence['native_common_observation']['comparison_sha256'] = '0'*64
    else:evidence['checks']['same_offer_id'] = False
    with pytest.raises((ValueError, release_store.ReleaseStoreError)):
        technical.retain_readback(store, plan['plan_id'], run, attempt, evidence)
    assert _retained(store, claim['run_id']) is None
    assert technical.inspect_existing(store, claim['run_id'])['state'] == 'UNKNOWN'
    assert not _consume(store, facts, claim['run_id'])['consumed']


def test_two_actual_retention_threads_and_restart_never_make_another_attempt(live, monkeypatch, tmp_path):
    store, plan, payload, facts = _setup(live, monkeypatch, tmp_path)
    claim = _reserve(store, facts);_consume(store, facts, claim['run_id'])
    evidence = _normal_historical_evidence(payload, monkeypatch)
    def complete(_):return technical.retain_readback(store, plan['plan_id'], claim['run_id'], 1, evidence)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(r['state']=='CONFIRMED_WRITE' for r in pool.map(complete, [0, 1]))
    restart = release_store.ReleaseStore(store.path)
    before = store.path.read_bytes()
    assert technical.retain_readback(restart, plan['plan_id'], claim['run_id'], 1, evidence)['state'] == 'CONFIRMED_WRITE'
    target = restart.get_run(claim['run_id'])['targets'][0]
    assert target['attempts'] == 1 and target['status'] == 'SUCCEEDED'
    assert store.path.read_bytes() == before


def test_completed_state_cannot_hide_a_rebound_false_comparison(live, monkeypatch, tmp_path):
    store, plan, payload, facts = _setup(live, monkeypatch, tmp_path)
    claim = _reserve(store, facts);_consume(store, facts, claim['run_id'])
    evidence = _normal_historical_evidence(payload, monkeypatch)
    technical.retain_readback(store, plan['plan_id'], claim['run_id'], 1, evidence)
    changed = deepcopy(evidence)
    changed['checks']['same_offer_id'] = False
    changed = release_adapters.bind_native_common_readback(evidence, changed)
    with technical._existing_transaction(store) as db:
        target = dict(db.execute('SELECT * FROM release_target_runs WHERE run_id=?',
                                 (claim['run_id'],)).fetchone())
        raw = technical._bytes(store._validated_common_observation(db, target, changed))
        from hashlib import sha256
        db.execute('UPDATE release_target_readbacks SET evidence_json=?,evidence_digest=? WHERE run_id=?',
                   (raw.decode(), sha256(raw).hexdigest(), claim['run_id']))
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match='COMMON_TECHNICAL_RETAINED_COMPARISON_CHANGED'):
        technical.retain_readback(store, plan['plan_id'], claim['run_id'], 1, changed)
    assert store.path.read_bytes() == before


def test_managed_transport_retains_its_own_claim_and_rejects_claimless_packet(live, monkeypatch, tmp_path):
    store, plan, payload, facts = _setup(live, monkeypatch, tmp_path)
    config = _service_config(tmp_path, monkeypatch)
    monkeypatch.setattr(boundary, '_ACTIVE', None)
    _, active = boundary.install_service_boundary(config)
    transport = active.bind_post(store, plan['plan_id'], lambda *_: pytest.fail('No transport in retention test'))
    evidence = _normal_historical_evidence(payload, monkeypatch)
    with pytest.raises(boundary.CommonEditBlocked, match='SAME_LEDGER_CLAIM_REQUIRED'):
        transport.retain_readback(evidence)
    claim = _reserve(store, facts);_consume(store, facts, claim['run_id'])
    # Test only the existing owned transaction claim consumer, not a public EDIT
    # grant. Real _dispatch stores exactly this committed run/attempt itself.
    transport._native_claim = (claim['run_id'], 1)
    assert transport.retain_readback(evidence)['state']=='CONFIRMED_WRITE'
    with pytest.raises(boundary.CommonEditBlocked, match='NOT_REDISPATCHED'):
        active._dispatch(transport, {}, lambda: pytest.fail('Consumed claim cannot dispatch'))


def test_real_pending_service_readonly_reuse_has_one_write_and_no_approval(live, monkeypatch, tmp_path):
    store, plan, payload, facts, old_run = _completed(live, monkeypatch, tmp_path)
    _service(monkeypatch, tmp_path, store)
    successor, next_payload, _ = _successor(store, payload, 'native-readonly-service')
    _closed_comparison(next_payload, monkeypatch)  # Installs only closed signed READ bytes.
    code, result = server._prepare_miaoshou_release({'offer_id':payload['product_id'],
        'plan_id':successor['plan_id'], 'reuse_miaoshou_readback':True})
    assert code==200, result
    assert result['mode']=='readback_reuse_no_write' and result['external_writes_performed']==[]
    assert result['native_run']['state']=='READONLY_REUSE'
    stored = json.loads(_retained(store, result['native_run']['run_id'])['evidence_json'])
    assert stored['predecessor']['run_id']==old_run
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        census=census_same_snapshot(store, db, next_payload,
            NativeCommonSourceReader(store).read_source_facts(db, successor['plan_id']))
        assert census['local_observed_confirmed_writes']==1
        assert census['local_observed_readonly_reuses']==1
        assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0]==0
    assert store.get_plan(successor['plan_id'])['status']==release_store.PLAN_PENDING_APPROVAL


def test_completed_same_plan_service_is_idempotent_without_new_read_or_edit(live, monkeypatch, tmp_path):
    store, plan, payload, facts, run_id = _completed(live, monkeypatch, tmp_path)
    _service(monkeypatch, tmp_path, store)
    monkeypatch.setattr(server, '_service_common_detail_observer', lambda: pytest.fail('Idempotent retained state needs no request'))
    before=store.path.read_bytes()
    code, result=server._prepare_miaoshou_release({'offer_id':payload['product_id'],
        'plan_id':plan['plan_id'], 'confirm_miaoshou_write':True})
    assert code==200 and result['idempotent'] and result['native_run']['run_id']==run_id, result
    assert result['external_writes_performed']==[] and store.path.read_bytes()==before


def test_fresh_service_unknown_account_coverage_cannot_call_or_reserve(live, monkeypatch, tmp_path):
    store, plan, payload, facts = _setup(live, monkeypatch, tmp_path)
    _service(monkeypatch, tmp_path, store)
    monkeypatch.setattr(server, '_service_common_detail_observer', lambda: pytest.fail('Admission before provider READ/EDIT'))
    before=store.path.read_bytes()
    code, result=server._prepare_miaoshou_release({'offer_id':payload['product_id'],
        'plan_id':plan['plan_id'], 'confirm_miaoshou_write':True})
    assert code==409 and result['error']=='COMMON_ACCOUNT_AND_COMPLETE_COVERAGE_AUTHORITY_UNKNOWN', result
    assert result['request_attempted'] is False and result['external_writes_performed']==[]
    assert store.path.read_bytes()==before


def test_unknown_consumed_same_run_service_never_reissues_edit(live, monkeypatch, tmp_path):
    store, plan, payload, facts = _setup(live, monkeypatch, tmp_path)
    claim=_reserve(store, facts);_consume(store, facts, claim['run_id'])
    _service(monkeypatch, tmp_path, store)
    monkeypatch.setattr(server, '_service_common_detail_observer', lambda: pytest.fail('Unknown never dispatched again'))
    code,result=server._prepare_miaoshou_release({'offer_id':payload['product_id'],
        'plan_id':plan['plan_id'], 'confirm_miaoshou_write':True})
    assert code==409 and result['error']=='COMMON_PRIOR_ATTEMPT_RECONCILIATION_REQUIRED'
    assert result['state']=='UNKNOWN' and result['native_run']['run_id']==claim['run_id']
    assert technical.inspect_existing(store, claim['run_id'])['state']=='UNKNOWN'
    assert store.get_run(claim['run_id'])['targets'][0]['attempts']==1


def test_real_service_wrong_offer_cannot_adopt_completed_baseline(live, monkeypatch, tmp_path):
    store, plan, payload, facts, old_run=_completed(live, monkeypatch, tmp_path)
    _service(monkeypatch, tmp_path, store)
    monkeypatch.setattr(server, '_service_common_detail_observer', lambda: pytest.fail('Wrong Offer cannot request READ'))
    before=store.path.read_bytes()
    code,result=server._prepare_miaoshou_release({'offer_id':'999999',
        'plan_id':plan['plan_id'], 'reuse_miaoshou_readback':True})
    assert code==409 and result['error']=='COMMON_TECHNICAL_REQUEST_OFFER_CHANGED'
    assert store.path.read_bytes()==before


def test_missing_native_store_is_not_created_or_fallen_into_legacy_prepare(monkeypatch, tmp_path):
    store=release_store.ReleaseStore(tmp_path/'missing-native.sqlite3')
    monkeypatch.setattr(release_store, 'default_release_store', lambda: store)
    monkeypatch.setattr(server, '_release_dashboard_for_request', lambda *_: pytest.fail('Missing native DB cannot fall into legacy'))
    code,result=server._prepare_miaoshou_release({'offer_id':'123', 'plan_id':'r3-common:123:missing',
        'reuse_miaoshou_readback':True})
    assert code==409 and result['error']=='COMMON_TECHNICAL_STORE_UNAVAILABLE'
    assert not store.path.exists()
