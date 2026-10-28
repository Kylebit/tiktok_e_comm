"""Replay original native TikTok zero failures without changing their bytes."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import sqlite3

import pytest
from test_b0431_ozon_media_recovery import original as approved_clone


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


@pytest.fixture
def originals(monkeypatch):
    path = os.environ.get('ORBIT_B0431_ACTUAL_DB')
    if not path:
        pytest.skip('explicit actual query-only fixture not requested')
    from shared_platform.release_store import ReleaseStore
    def deny(*args, **kwargs):
        raise AssertionError('No network or writable ledger connection is permitted')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(ReleaseStore, '_connect', deny)
    store = ReleaseStore(path)
    with sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row; db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
        run_id = 'release-run:25e66f7250d31cbb39ff8d0e'
        rows = [row for row in store._run_in_transaction(db, run_id)['targets'] if row['target_label'].startswith('tiktok:')]
        raw = [dict(row) for row in db.execute('SELECT * FROM release_target_failure_events WHERE run_id=? AND target_label LIKE ? ORDER BY target_label,attempt', (run_id, 'tiktok:%'))]
        assert len(rows) == len(raw) == 4
        assert all(row['status'] == 'FAILED' and row['attempts'] == 1 for row in rows)
        assert all(sha256(row['evidence_json'].encode()).hexdigest() == row['evidence_digest'] for row in raw)
        assert db.execute('PRAGMA query_only').fetchone()[0] == 1
    return rows, raw


def test_actual_four_native_tiktok_failures_are_typed_eligible(originals):
    from modules.products import server
    rows, raw = originals
    before = _bytes((rows, raw))
    for row in rows:
        assert server._generic_tiktok_safe_retry_target(row), row['target_label']
        assert server._zero_write_pre_submit_evidence(row['failure_events'][0]['evidence'])
        assert server._zero_write_pre_submit_evidence(row['latest_failure_evidence'])
    assert _bytes((rows, raw)) == before
    output = os.environ.get('ORBIT_B0431_TK_ZERO_OUTPUT')
    if output:
        Path(output).write_text(json.dumps(dict(status='PASS_PURE_TYPED_ELIGIBILITY',
            run_id=rows[0]['run_id'], rows=[dict(target_label=row['target_label'], attempts=row['attempts'], status=row['status'],
                raw_failure_sha256=next(r['evidence_digest'] for r in raw if r['target_label'] == row['target_label']), eligible=True) for row in rows],
            original_failure_bytes_unchanged=True, query_only=True, database_writes=0, provider_calls=0,
            permission='Predicate only; original protected native decision and ReleaseStore retry guards remain required',
            not_verified=['deployed source/current chain', 'runtime resume', 'provider publication']), indent=2), encoding='utf-8')


def test_native_unknown_dispatch_budget_identity_and_second_attempt_are_rejected(originals):
    from modules.products import server
    original = originals[0][0]
    assert server._generic_tiktok_safe_retry_target(original)
    mutations = [
        lambda p: p.update(outcome_unknown=True),
        lambda p: p.update(request_attempted=True),
        lambda p: p.update(known_pre_submit_zero=False),
        lambda p: p['canonical_platform_result'].update(dispatch_attempted=True),
        lambda p: p['canonical_platform_result'].update(external_write_count=1),
        lambda p: p['canonical_platform_result']['targets'][0]['evidence'].update(request_attempted=True),
        lambda p: p['mutation_budget']['attempts'].update(shared=1, total=1),
        lambda p: p['mutation_budget'].update(reservations=[{'outcome': 'UNKNOWN'}]),
        lambda p: p['checkpoint_file_identity'].update(release_run_id='different-run'),
        lambda p: p.update(schema_version='unverified-schema'),
    ]
    for mutate in mutations:
        row = deepcopy(original); event = row['failure_events'][0]; mutate(event['evidence'])
        event['evidence_digest'] = sha256(_bytes(event['evidence'])).hexdigest()
        row['latest_failure_evidence'] = deepcopy(event)
        assert not server._generic_tiktok_safe_retry_target(row)
    for changes in ({'attempts': 2}, {'submission': {'accepted': True}}, {'target_label': 'tiktok:HB_MY'}):
        assert not server._generic_tiktok_safe_retry_target({**original, **changes})
    row = deepcopy(original); row['failure_events'][0]['evidence_digest'] = '0' * 64
    assert not server._generic_tiktok_safe_retry_target(row)
    row = deepcopy(original); row['failure_events'] *= 2
    assert not server._generic_tiktok_safe_retry_target(row)
    row = deepcopy(original); row['latest_failure_evidence'] = deepcopy(row['latest_failure_evidence'])
    row['latest_failure_evidence']['evidence'] = {'pre_submit_failure': True, 'submission_accepted': False, 'external_writes_performed': []}
    assert not server._generic_tiktok_safe_retry_target(row)


def test_original_legacy_zero_contract_remains_available():
    from modules.products import server
    proof = {'pre_submit_failure': True, 'submission_accepted': False, 'external_writes_performed': []}
    assert server._zero_write_pre_submit_evidence(proof)
    assert server._generic_tiktok_safe_retry_target(dict(target_label='tiktok:LH_MY', status='FAILED', failure_events=[{'evidence': proof}]))
    assert not server._zero_write_pre_submit_evidence({**proof, 'submission_accepted': True})


@pytest.mark.parametrize('first_channel', ['tiktok', 'shopee'])
def test_actual_original_publish_loop_claims_native_zero_before_wire(approved_clone, monkeypatch, first_channel):
    """Actual approved rows in temporary SQLite; source gate is a named boundary."""
    from types import SimpleNamespace
    from modules.products import server, release_adapters
    from domains.channel_operations.release_executor import AdapterRegistration
    from shared_platform import agent_prepared_v4_targets
    from shared_platform import native_ozon_three_card_media_recovery as media
    from shared_platform.native_final_mutation_budget import NativeMutationBudgetHold
    ctx, _ = approved_clone
    active = ctx['active'];store = active.store;plan = store.get_plan(media.PLAN)
    active.runtime._operations = SimpleNamespace(agent_prepared_common=object())
    source_boundary = []
    monkeypatch.setattr(agent_prepared_v4_targets, '_scope', lambda value, frozen:
        source_boundary.append((value is active, frozen['plan_id'])))
    # No Ozon authority intake/provider work belongs in this TK/SH loop test.
    def hold_media(value):raise NativeMutationBudgetHold('OZON_THREE_MEDIA_TEST_SCOPE_HOLD')
    monkeypatch.setattr(media, 'activate_failed_targets', hold_media)
    monkeypatch.setattr(media, 'recovery_actions', lambda value, run, actions: actions)
    before = {row['target_label']:deepcopy(row) for row in store.get_run(media.RUN)['targets']}
    with store._connect_readonly() as db:
        budget_before = {table:[tuple(row) for row in db.execute('SELECT * FROM '+table)] for table in
            ('native_final_budget_scopes','native_final_budget_reservations','native_final_budget_dispatches','native_final_budget_outcomes')}
    calls=[];gates=[];claims=[]
    class BeforeWire(BaseException):pass
    def stop(request):
        calls.append(request.target_label)
        raise BeforeWire('Stop before provider/dependency factory or mutation reservation')
    registry = {name:AdapterRegistration(name, stop if first_channel == 'tiktok' or channel != 'tiktok' else None,
        True,True,True,True) for channel,name in release_adapters.ADAPTER_NAMES.items()}
    def gate(data, *, store):
        gates.append(True)
        with store._connect_readonly() as db:active.service._decision(db,active.decision_id,owner_sid=active.owner_sid)
        current=store.get_plan(media.PLAN)
        assert data['confirmation_token']==current['confirmation_token']
        return {'dashboard':{'frozen_review_projection':True},'payload':current['payload'],'plan':current,
            'run':store.get_run(media.RUN),'predecessor_run':None,'registry':registry,
            'target_rows':[dict(channel=label.split(':')[0],site=label.split(':')[1],
                adapter=release_adapters.ADAPTER_NAMES[label.split(':')[0]]) for label in current['targets']]},None
    active.gate=gate
    active.common_dependency_verified=lambda value:True
    original_begin=store.begin_target
    def begin(run,label):
        row=original_begin(run,label);claims.append((label,row['status'],row['attempts']));return row
    monkeypatch.setattr(store,'begin_target',begin)
    with pytest.raises(BeforeWire):
        server._publish_selected_release(dict(offer_id=plan['product_id'],plan_id=media.PLAN,
            confirmation_token=plan['confirmation_token'],confirm_publish=True),native_gate=active)
    expected='tiktok:LH_MY' if first_channel=='tiktok' else 'shopee:MY'
    assert calls==[expected] and claims==[(expected,'RUNNING',2)]
    assert len(gates)==(3 if first_channel=='tiktok' else 7)  # Four unavailable TK adapters are gated, never begun.
    after={row['target_label']:row for row in store.get_run(media.RUN)['targets']}
    for label,row in after.items():
        assert row['failure_events']==before[label]['failure_events']
        assert row['submission']==before[label]['submission']
        if label!=expected:
            assert row['attempts']==before[label]['attempts']
            assert row['error']==before[label]['error']
        if label.startswith('tiktok:') and label!=expected:
            assert row['status']=='PENDING' and row['attempts']==1
            assert not server._release_target_recovery_actions({'targets':[row]},registry=registry)[0]['runnable']
    assert after['ozon:RU']==before['ozon:RU']
    with store._connect_readonly() as db:
        assert {table:[tuple(row) for row in db.execute('SELECT * FROM '+table)] for table in budget_before}==budget_before
    assert source_boundary and all(value==(True,media.PLAN) for value in source_boundary)
    output=os.environ.get('ORBIT_B0431_TK_LOOP_OUTPUT')
    if output:
        destination=Path(output).with_name(Path(output).stem+'-'+first_channel+'.json')
        destination.write_text(json.dumps(dict(status='PASS_ORIGINAL_LOOP_TEMPORARY_CLONE',
            first_target=expected,real_begin_attempt=2,gate_snapshots=len(gates),
            original_failure_rows_preserved=True,unclaimed_errors_and_attempts_preserved=True,
            original_ozon_submission_unchanged=True,all_budget_rows_unchanged=True,provider_calls=0,
            actual_database_writes=0,source_actor_COMMON='Explicit boundary; actual source recheck NOT run',
            api9_plan=media.PLAN,decision_id=media.DECISION),indent=2),encoding='utf-8')


def test_native_continuation_rejects_missing_active_and_nonzero_budget(approved_clone, monkeypatch):
    from types import SimpleNamespace
    from modules.products import server
    from shared_platform import agent_prepared_v4_targets
    from shared_platform.native_sole_final_execution import _ACTIVE
    from shared_platform import native_ozon_three_card_media_recovery as media
    ctx,_=approved_clone;active=ctx['active'];store=active.store
    active.runtime._operations=SimpleNamespace(agent_prepared_common=object())
    monkeypatch.setattr(agent_prepared_v4_targets,'_scope',lambda value,plan:None)
    labels=['tiktok:LH_MY','tiktok:LH_PH','tiktok:LH_TH','tiktok:LH_VN']
    token=_ACTIVE.set(None)
    try:assert server._activate_native_tiktok_zero(active,labels)==()
    finally:_ACTIVE.reset(token)
    assert server._activate_native_tiktok_zero(active,['shopee:MY'])==()
    before=deepcopy(store.get_run(media.RUN))
    target=next(row for row in before['targets'] if row['target_label']=='tiktok:LH_MY')
    scope=target['failure_events'][0]['evidence']['mutation_budget']['durable_scope']
    with store._transaction() as db:
        db.execute('INSERT INTO native_final_budget_scopes VALUES (?,?,?,?,?,?)',
            (scope['scope_key'],media.PLAN,media.SNAPSHOT,'TIKTOK','{}',sha256(b'{}').hexdigest()))
        db.execute('INSERT INTO native_final_budget_reservations(scope_key,sequence,target_label,budget_scope,operation,attempt_count,initial_outcome) VALUES (?,?,?,?,?,?,?)',
            (scope['scope_key'],1,'tiktok:LH_MY','shared','diagnostic-only',1,'UNKNOWN'))
    assert server._activate_native_tiktok_zero(active,labels)==()
    assert store.get_run(media.RUN)==before
