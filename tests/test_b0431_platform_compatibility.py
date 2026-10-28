"""Bounded pagination, frozen Malay, and same-attempt native zero recovery.

The optional actual-row case opens the live ledger query-only, copies only this
approved plan into a temporary database, and exercises real approval/CAS guards.
Its source recheck is an explicit test boundary, not a source-event validation.
"""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import sqlite3
from types import SimpleNamespace

import pytest

from shared_platform.product_publication_live_dependencies import (
    _seed_list_rows, LivePublicationDependencyError,
)
from modules.shopee.global_copy import contains_malay_language_features
from modules.shopee.skill_regions import _localized_copy_matches, _require_frozen_regional_copy


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


@pytest.mark.parametrize('last_size', [0, 3])
def test_manual_seed_missing_metadata_continues_to_proven_terminal_page(last_size):
    calls = []
    def post(path, body):
        calls.append((path, deepcopy(body)))
        count = 100 if body['pageNo'] == 1 else last_size
        return {'code': 'success', 'data': {'detailList': [{'id': str(body['pageNo'] * 100 + n)} for n in range(count)]}}
    rows = _seed_list_rows(post=post, path='/original-list', source_offer_id=None,
                           common_list=False, page_size=100, max_pages=20)
    assert len(rows) == 100 + last_size
    assert calls == [('/original-list', {'pageNo': n, 'pageSize': 100, 'filter': {}}) for n in (1, 2)]


def test_manual_seed_twenty_full_pages_never_claim_complete_coverage():
    calls = []
    def post(path, body):
        calls.append(deepcopy(body))
        return {'code': 'success', 'data': {'detailList': [{'id': str(body['pageNo'] * 100 + n)} for n in range(100)]}}
    with pytest.raises(LivePublicationDependencyError, match='pagination is incomplete'):
        _seed_list_rows(post=post, path='/original-list', source_offer_id=None,
                        common_list=False, page_size=100, max_pages=20)
    assert [row['pageNo'] for row in calls] == list(range(1, 21))


def test_malformed_metadata_is_still_rejected():
    with pytest.raises(LivePublicationDependencyError, match='pagination is malformed'):
        _seed_list_rows(post=lambda *_: {'data': {'detailList': [], 'hasNextPage': 'false'}},
                        path='/original-list', source_offer_id=None, common_list=False, page_size=100, max_pages=20)


MALAY_LINES = (
    'Menutupi bahagian atas peti sejuk sambil menambah hiasan bercorak.',
    'Poket sisi menyediakan ruang untuk barangan dapur kecil.',
    'Pilih corak Daisi, Bulu atau Langit Berbintang.',
    'Setiap corak ialah varian yang dipilih secara berasingan; ini bukan set tiga helai.',
)


def test_four_retained_malay_lines_and_english_negative():
    title = 'Penutup peti sejuk dengan poket sisi'
    assert all(contains_malay_language_features(line) for line in MALAY_LINES)
    assert _localized_copy_matches(title, '\n'.join(MALAY_LINES), region='MY')
    assert not _localized_copy_matches('Refrigerator Cover with Side Pockets', 'Choose Daisy, Feather or Starry Sky.', region='MY')
    assert not _localized_copy_matches(title, '\n'.join((*MALAY_LINES, 'Choose your preferred design.')), region='MY')
    assert not contains_malay_language_features('peti peti peti')  # still two distinct words
    assert contains_malay_language_features('Pelekat dinding untuk hiasan rumah')


def _zero_target():
    label = 'shopee:MY'
    proof = dict(schema_version='native-scoped-v4-target-result/v1', target_label=label,
        status='FAILED', known_pre_submit_zero=True, request_attempted=False, outcome_unknown=False,
        reconciliation_required=False, verified=False, external_writes_performed=[], catalog_projection_failures=[],
        canonical_platform_result=dict(platform='SHOPEE', dispatch_attempted=False, readback_completed=False,
            external_write_count=0, targets=[dict(target_label=label, status='FAILED', evidence=dict(
                stage='PREPARATION', provider_code='shopee_preparation_failed', target_label=label,
                provider_reason='Shopee preparation failed before mutation [phase=GLOBAL_RESOLVER; exception=ValueError]',
                request_attempted=False, outcome_unknown=False, external_write_count=0))]),
        mutation_budget=dict(platform='SHOPEE', reservations=[], attempts=dict(shared=0, total=0,
            per_target={f'shopee:{site}': 0 for site in ('MY', 'PH', 'TH', 'VN')})))
    return dict(target_label=label, status='FAILED', attempts=1,
        failure_events=[dict(attempt=1, evidence=proof, evidence_digest=sha256(_bytes(proof)).hexdigest())])


def test_shopee_zero_predicate_rejects_unknown_dispatch_budget_and_second_attempt():
    from modules.products import server
    original = _zero_target()
    assert server._native_shopee_global_zero_target(original)
    assert server._generic_tiktok_safe_retry_target(original)
    for field, value in [('outcome_unknown', True), ('request_attempted', True)]:
        row = deepcopy(original); row['failure_events'][0]['evidence'][field] = value
        row['failure_events'][0]['evidence_digest'] = sha256(_bytes(row['failure_events'][0]['evidence'])).hexdigest()
        assert not server._native_shopee_global_zero_target(row)
    row = deepcopy(original); row['failure_events'][0]['evidence']['mutation_budget']['reservations'] = [{}]
    row['failure_events'][0]['evidence_digest'] = sha256(_bytes(row['failure_events'][0]['evidence'])).hexdigest()
    assert not server._native_shopee_global_zero_target(row)
    for changes in ({'attempts': 2}, {'target_label': 'ozon:RU'}, {'submission': {'accepted': True}}):
        assert not server._native_shopee_global_zero_target({**original, **changes})
    row = deepcopy(original); row['failure_events'][0]['evidence_digest'] = '0' * 64
    assert not server._native_shopee_global_zero_target(row)
    assert server._activate_native_shopee_global_zero(SimpleNamespace()) == ()


@pytest.mark.skipif(not os.environ.get('ORBIT_B0431_ACTUAL_DB'), reason='explicit query-only actual fixture not requested')
def test_actual_approved_rows_native_shopee_cas_retains_failures_and_ozon(tmp_path, monkeypatch):
    from modules.products import server
    from shared_platform.release_store import ReleaseStore
    from shared_platform.native_sole_final_decision import NativeSoleFinalDecisionService
    from shared_platform.native_sole_final_execution import _NativeExecution, _ACTIVE
    from shared_platform import agent_prepared_v4_targets

    plan_id = 'r3-marketplace:8412287877065586543:07707b4b7901ca9b97b08acd'
    run_id = 'release-run:25e66f7250d31cbb39ff8d0e'
    actual = Path(os.environ['ORBIT_B0431_ACTUAL_DB']).resolve()
    writes = []
    def deny(*args, **kwargs):
        writes.append('network'); raise AssertionError('test forbids network')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    db = sqlite3.connect(actual.as_uri() + '?mode=ro', uri=True)
    db.execute('PRAGMA query_only=ON'); db.row_factory = sqlite3.Row
    db.execute('BEGIN')
    tables = ['release_plans', 'release_approvals', 'approved_publication_snapshots',
        'publication_business_snapshots', 'native_sole_final_decisions', 'release_runs',
        'release_target_runs', 'release_target_failure_events', 'release_target_submissions',
        'release_target_readbacks', 'release_target_repairs', 'native_final_budget_scopes',
        'native_final_budget_reservations', 'native_final_budget_dispatches', 'native_final_budget_outcomes']
    captured = {}
    for table in tables:
        if table.startswith('native_final_budget_') and table != 'native_final_budget_scopes':
            rows = db.execute('SELECT * FROM ' + table + ' WHERE scope_key IN (SELECT scope_key FROM native_final_budget_scopes WHERE plan_id=?)', (plan_id,)).fetchall()
        else:
            column = 'run_id' if table.startswith('release_target_') else 'plan_id'
            rows = db.execute('SELECT * FROM ' + table + ' WHERE ' + column + '=?', (run_id if column == 'run_id' else plan_id,)).fetchall()
        captured[table] = [dict(row) for row in rows]
    schema = {table: db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0] for table in tables}
    original_hashes = {table: sha256(_bytes(rows)).hexdigest() for table, rows in captured.items()}
    decision = captured['native_sole_final_decisions'][0]
    assert len(captured['release_approvals']) == len(captured['native_sole_final_decisions']) == 1
    snapshot = json.loads(captured['approved_publication_snapshots'][0]['snapshot_json'])
    assert sha256(captured['approved_publication_snapshots'][0]['snapshot_json'].encode()).hexdigest() == '3e41486796ec38f8f59fa73655abf1bc3deb1df27d62c5222e38baa5d4d02fb1'
    _require_frozen_regional_copy(snapshot, 'shopee:MY')  # actual immutable Malay, not a rewritten fixture
    store = ReleaseStore(tmp_path / 'isolated.db')
    with sqlite3.connect(store.path) as target:
        for table in tables:
            if not target.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                target.execute(schema[table])
            for row in captured[table]:
                target.execute('INSERT INTO ' + table + '(' + ','.join(row) + ') VALUES (' + ','.join('?' for _ in row) + ')', tuple(row.values()))
    with store._transaction():
        pass
    service = NativeSoleFinalDecisionService(store, lambda: {})
    active = object.__new__(_NativeExecution)
    active.service, active.store = service, store
    active.plan_id, active.decision_id, active.owner_sid = plan_id, decision['decision_id'], decision['owner_sid']
    active.runtime = SimpleNamespace(_operations=SimpleNamespace(agent_prepared_common=object()))
    checked = []
    monkeypatch.setattr(agent_prepared_v4_targets, '_scope', lambda a, p: checked.append((a is active, p['plan_id'])))
    run = store.get_run(run_id)
    originals = {row['target_label']: deepcopy(row) for row in run['targets']}
    shopee = tuple(row['target_label'] for row in run['targets'] if row['target_label'].startswith('shopee:'))
    assert len(shopee) == 4 and all(server._native_shopee_global_zero_target(originals[label]) for label in shopee)
    token = _ACTIVE.set(active)
    try:
        assert server._activate_native_shopee_global_zero(active) == shopee
        assert server._activate_native_shopee_global_zero(active) == shopee  # interrupted local continuation is idempotent
        after = {row['target_label']: row for row in store.get_run(run_id)['targets']}
        for label, row in after.items():
            if label in shopee:
                assert row['status'] == 'PENDING' and row['attempts'] == 1
                assert row['failure_events'] == originals[label]['failure_events']
                assert row['error'] == originals[label]['error']
            else:
                assert row == originals[label]  # includes original Ozon accepted imports
        with store._transaction() as target:
            scope = originals[shopee[0]]['failure_events'][0]['evidence']['mutation_budget']['durable_scope']['scope_key']
            target.execute('INSERT OR IGNORE INTO native_final_budget_scopes VALUES (?,?,?,?,?,?)',
                (scope, plan_id, decision['snapshot_digest'], 'SHOPEE', '{}', sha256(b'{}').hexdigest()))
            target.execute('INSERT INTO native_final_budget_reservations(scope_key,sequence,target_label,budget_scope,operation,attempt_count,initial_outcome) VALUES (?,?,?,?,?,?,?)',
                (scope, 1, 'shopee:MY', 'shared', 'diagnostic-test-only', 1, 'UNKNOWN'))
        assert server._activate_native_shopee_global_zero(active) == ()
    finally:
        _ACTIVE.reset(token)
    assert checked and all(row == (True, plan_id) for row in checked)
    assert not writes
    # The actual connection is query-only and held one read snapshot throughout.
    assert db.execute('PRAGMA query_only').fetchone()[0] == 1
    db.close()
    output = os.environ.get('ORBIT_B0431_COMPAT_OUTPUT')
    if output:
        Path(output).write_text(json.dumps(dict(status='PASS', actual_rows_query_only=True,
            actual_approved_snapshot_raw_sha256='3e41486796ec38f8f59fa73655abf1bc3deb1df27d62c5222e38baa5d4d02fb1',
            plan_id=plan_id, run_id=run_id, shopee_recovered_in_temporary_database=list(shopee),
            actual_row_hashes=original_hashes, original_failures_and_attempts_preserved=True,
            ozon_and_tiktok_unchanged=True, rejects_any_shopee_budget_reservation=True,
            source_verification='explicit unit boundary; actual source-event/runtime recheck NOT verified',
            provider_calls=0, actual_database_writes=0), indent=2), encoding='utf-8')
