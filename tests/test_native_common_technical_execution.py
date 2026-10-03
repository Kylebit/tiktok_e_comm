"""Owned SQLite transaction mechanics, not native account/coverage authority.

The private `_TransactionFacts` values here bind genuine native preparation
rows but deliberately replace the still-missing upstream authority context.
Only the core is exercised with them. The service-facing reserve remains
blocked and cannot accept these objects or caller-provided grants.
"""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import threading

import pytest

from shared_platform import native_common_technical_execution as technical
from shared_platform import release_store, publication_r3_image_bridge as bridge
from modules.products import release_adapters
from test_native_common_write_census import _origin, _historical_target, _normal_historical_evidence, _closed_comparison
from test_round1_workspace_freeze import live


def _install_owned(store, tmp_path):
    assert store.path.resolve().is_relative_to(tmp_path.resolve())
    script = (Path(technical.__file__).parent/'migrations/native_common_technical_runs_v1.sql').read_text(encoding='utf-8')
    with sqlite3.connect(store.path, isolation_level=None) as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        quote = lambda name: '"' + name.replace('"', '""') + '"'
        before = {name: (list(db.execute('PRAGMA table_info('+quote(name)+')')),
                         list(db.execute('SELECT * FROM '+quote(name))),
                         list(db.execute('PRAGMA foreign_key_list('+quote(name)+')')))
                  for name in tables}
        objects = dict((r[0], (r[1], r[2], r[3])) for r in db.execute(
            "SELECT name,type,tbl_name,sql FROM sqlite_master WHERE type IN ('index','trigger') AND sql IS NOT NULL"))
        db.execute('PRAGMA foreign_keys=OFF')
        db.executescript(script)
        assert list(db.execute('PRAGMA foreign_key_check')) == []
        assert set(tables) == {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        for name, (columns, rows, foreign_keys) in before.items():
            assert list(db.execute('PRAGMA foreign_key_list('+quote(name)+')')) == foreign_keys
            if name == 'release_runs':
                old_names = ','.join(quote(row[1]) for row in columns)
                assert list(db.execute('SELECT '+old_names+' FROM release_runs')) == rows
                assert all(r == ('LEGACY', None, None) for r in db.execute(
                    'SELECT technical_execution_state,technical_admission_json,technical_admission_digest FROM release_runs'))
            else:
                assert list(db.execute('PRAGMA table_info('+quote(name)+')')) == columns
                assert list(db.execute('SELECT * FROM '+quote(name))) == rows
        after_objects = dict((r[0], (r[1], r[2], r[3])) for r in db.execute(
            "SELECT name,type,tbl_name,sql FROM sqlite_master WHERE type IN ('index','trigger') AND sql IS NOT NULL"))
        assert {name: after_objects[name] for name in objects} == objects
        assert set(after_objects) - set(objects) == {'release_runs_native_binding_immutable', 'release_runs_native_common_scope'}
        db.execute('PRAGMA foreign_keys=ON')


def _fixture_facts(plan, payload):
    source = payload['r3_stage_binding']['native_preparation_source']
    return technical._TransactionFacts(plan['plan_id'], plan['payload_digest'], payload['product_id'],
        source['account_identity_digest'], source['preparation_root']['root_id'],
        technical._bytes(release_adapters._immutable_miaoshou_common_draft(payload)),
        'owned-fixture-policy-not-native-grant', 1, 'owned-fixture-coverage-not-native-grant',
        'owned-core-test-context-not-native-authority')


def _setup(live, monkeypatch, tmp_path):
    store, plan, payload = _origin(live, monkeypatch)
    _install_owned(store, tmp_path)
    return store, plan, payload, _fixture_facts(plan, payload)


def _reserve(store, facts, **options):
    with technical._existing_transaction(store) as db:
        return technical._reserve_in_transaction(store, db, facts, **options)


def _consume(store, facts, run_id):
    with technical._existing_transaction(store) as db:
        return technical._consume_in_transaction(db, facts, run_id)


def _successor(store, payload, tag):
    value = deepcopy(payload);value['content_package_id'] += ':' + tag
    value['plan_id'] = bridge.common_stage_plan_id(value, offer_id=value['product_id'])
    plan = store.create_plan(value)
    return plan, value, _fixture_facts(plan, value)


def _retain_owned_comparison(store, run_id, evidence):
    # This inserts a closed fake read observation, not a provider write/grant.
    # The original Store verifies and supplies exact attempt/plan lineage.
    with technical._existing_transaction(store) as db:
        target = dict(db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?',
                                (run_id, technical.COMMON)).fetchone())
        value = store._validated_common_observation(db, target, evidence)
        raw = technical._bytes(value).decode()
        db.execute('INSERT INTO release_target_readbacks VALUES (?,?,?,?,?)',
                   (run_id, technical.COMMON, raw, technical.sha256(raw.encode()).hexdigest(), release_store._utc_now()))


def _recover(store, facts, run_id):
    with technical._existing_transaction(store) as db:
        return technical._complete_retained_in_transaction(store, db, facts, run_id)


def test_explicit_migration_preserves_nonempty_legacy_rows_all_foreign_keys_and_guards(live, monkeypatch, tmp_path):
    store, plan, payload = _origin(live, monkeypatch)
    failed_plan, _, _ = _successor(store, payload, 'legacy-failure')
    submitted_plan, _, _ = _successor(store, payload, 'legacy-submission')
    unapproved_plan, _, _ = _successor(store, payload, 'still-requires-human-for-old-run')
    success = _historical_target(store, plan)
    store.record_target_success(success['run_id'], technical.COMMON,
        external_id=payload['product_id'], readback_evidence=_normal_historical_evidence(payload, monkeypatch))
    failed = _historical_target(store, failed_plan)
    store.record_target_failure(failed['run_id'], technical.COMMON, error='owned old unknown',
        failure_evidence={'source':'owned-old-transport', 'request_attempted':True, 'write_outcome':'unknown'})
    submitted = _historical_target(store, submitted_plan)
    store.record_target_submission(submitted['run_id'], technical.COMMON,
        external_id=payload['product_id'], submission_evidence={'accepted':True,'source':'owned-old-submission'},
        detail='owned historical accepted but unverified')
    run_ids = [success['run_id'], failed['run_id'], submitted['run_id']]
    before = [store.get_run(run_id) for run_id in run_ids]
    _install_owned(store, tmp_path)
    after = [store.get_run(run_id) for run_id in run_ids]
    assert [{key: new[key] for key in old} for old, new in zip(before, after)] == before
    assert all(new['technical_execution_state'] == 'LEGACY'
               and new['technical_admission_json'] is None and new['technical_admission_digest'] is None for new in after)
    with store._connect_readonly() as db:
        assert technical._schema_installed(db)
        for table in ('release_target_runs','release_target_readbacks','release_target_failure_events','release_target_submissions'):
            assert db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] > 0
    with pytest.raises(release_store.ReleaseAuthorizationError):
        store.start_run(unapproved_plan['plan_id'])
    with sqlite3.connect(store.path) as db:
        with pytest.raises(sqlite3.IntegrityError, match='release run identity is immutable'):
            db.execute('UPDATE release_runs SET approval_id=NULL WHERE run_id=?', (success['run_id'],))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('INSERT INTO release_runs(run_id,plan_id,status,created_at,updated_at) VALUES (?,?,\'RUNNING\',?,?)',
                       ('invalid-null-credential', unapproved_plan['plan_id'], 'owned', 'owned'))


def test_missing_native_schema_or_store_never_creates_database(live, monkeypatch, tmp_path):
    store, plan, payload = _origin(live, monkeypatch)
    before = store.path.read_bytes()
    with pytest.raises(release_store.ReleaseAuthorizationError, match='COMMON_TECHNICAL_SCHEMA_NOT_INSTALLED'):
        technical.reserve(store, plan['plan_id'])
    assert store.path.read_bytes() == before
    missing = release_store.ReleaseStore(tmp_path/'never-created.sqlite3')
    with pytest.raises(release_store.ReleaseAuthorizationError, match='COMMON_TECHNICAL_STORE_UNAVAILABLE'):
        technical.reserve(missing, plan['plan_id'])
    assert not missing.path.exists()


def test_installed_schema_does_not_substitute_native_policy_account_or_coverage(live, monkeypatch, tmp_path):
    store, plan, payload, facts = _setup(live, monkeypatch, tmp_path)
    before = store.path.read_bytes()
    with pytest.raises(release_store.ReleaseAuthorizationError, match='COMMON_.*AUTHORITY_UNKNOWN'):
        technical.reserve(store, plan['plan_id'])
    assert store.path.read_bytes() == before
    with store._connect_readonly() as db:
        assert db.execute('SELECT COUNT(*) FROM release_runs').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0] == 0
    with pytest.raises(TypeError):
        technical.reserve(store, plan['plan_id'], facts=facts)


def test_two_actual_threads_share_one_durable_reservation_without_human_approval(live, monkeypatch, tmp_path):
    store, plan, payload, facts = _setup(live, monkeypatch, tmp_path)
    ready = threading.Barrier(2)
    def attempt():
        ready.wait(timeout=5)
        return _reserve(store, facts)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(attempt);b=pool.submit(attempt);results=[a.result(),b.result()]
    assert len({r['run_id'] for r in results}) == 1
    assert sorted(r['created'] for r in results) == [False, True]
    with store._connect_readonly() as db:
        row = db.execute('SELECT * FROM release_runs').fetchone()
        assert row['approval_id'] is None and row['technical_execution_state'] == 'RESERVED'
        assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM release_target_runs').fetchone()[0] == 1
    with pytest.raises(release_store.ReleaseAuthorizationError):
        store.begin_target(results[0]['run_id'], technical.COMMON)


def test_unknown_is_committed_before_transport_and_restart_does_not_resend(live, monkeypatch, tmp_path):
    store, plan, payload, facts = _setup(live, monkeypatch, tmp_path)
    reserved = _reserve(store, facts)
    first = _consume(store, facts, reserved['run_id'])
    assert first['consumed'] and technical.inspect_existing(store, reserved['run_id'])['state'] == 'UNKNOWN'
    calls=[]
    def closed_transport():
        assert technical.inspect_existing(store, reserved['run_id'])['state'] == 'UNKNOWN'
        calls.append('owned-fake-only')
        raise TimeoutError('closed transport uncertain result')
    with pytest.raises(TimeoutError):
        if first['consumed']:closed_transport()
    live['restart']()
    restarted=release_store.ReleaseStore(store.path)
    second=_consume(restarted, facts, reserved['run_id'])
    if second['consumed']:closed_transport()
    assert second['state'] == 'UNKNOWN' and not second['consumed'] and len(calls) == 1
    with technical._existing_transaction(restarted) as db:
        with pytest.raises(release_store.ReleaseAuthorizationError, match='NOT_DISPATCHED_UNPROVEN'):
            technical._not_dispatched_in_transaction(db, facts, reserved['run_id'])


def test_unknown_on_another_preparation_root_or_plan_requires_reconciliation(live, monkeypatch, tmp_path):
    store, plan, payload, facts = _setup(live, monkeypatch, tmp_path)
    reserved=_reserve(store, facts);_consume(store, facts, reserved['run_id'])
    with pytest.raises(release_store.ReleaseAuthorizationError, match='RECONCILIATION_REQUIRED'):
        _successor(store, payload, 'must-not-reset-unknown')
    with technical._existing_transaction(store) as db:
        with pytest.raises(release_store.ReleaseAuthorizationError, match='PREPARATION_BINDING_CHANGED'):
            technical._reserve_in_transaction(store, db, replace(facts, root_id='caller-new-root'))


def test_proven_not_dispatched_allows_same_scope_attempt_without_spending_confirmed_cap(live, monkeypatch, tmp_path):
    store, plan, payload, facts = _setup(live, monkeypatch, tmp_path)
    reserved=_reserve(store, facts)
    with technical._existing_transaction(store) as db:
        failed=technical._not_dispatched_in_transaction(db, facts, reserved['run_id'])
    assert failed['state'] == 'PROVEN_NOT_DISPATCHED'
    again=_reserve(store, facts)
    assert again['run_id'] == reserved['run_id'] and again['state'] == 'RESERVED'
    with store._connect_readonly() as db:
        target=db.execute('SELECT * FROM release_target_runs').fetchone()
        assert target['attempts'] == 2
        assert db.execute('SELECT COUNT(*) FROM release_target_failure_events').fetchone()[0] == 1
    assert _consume(store, facts, again['run_id'])['consumed']


def test_confirmed_cap_and_exact_readonly_reuse_are_distinct(live, monkeypatch, tmp_path):
    store, plan, payload, facts = _setup(live, monkeypatch, tmp_path)
    reserved=_reserve(store, facts);_consume(store, facts, reserved['run_id'])
    _retain_owned_comparison(store, reserved['run_id'], _normal_historical_evidence(payload, monkeypatch))
    assert _recover(store, facts, reserved['run_id'])['state'] == 'CONFIRMED_WRITE'
    new_plan, new_payload, new_facts = _successor(store, payload, 'readonly-reuse')
    with pytest.raises(release_store.ReleaseAuthorizationError, match='CONFIRMED_WRITE_CAP_EXHAUSTED'):
        _reserve(store, new_facts)
    reused=_reserve(store, new_facts, readonly_reuse=True)
    assert reused['state'] == 'READONLY_RESERVED'
    assert not _consume(store, new_facts, reused['run_id'])['consumed']
    source=_closed_comparison(new_payload, monkeypatch)
    summary={k:v for k,v in source.items() if k not in {'native_common_observation','stored_common_lineage'}}
    old_target=store.get_run(reserved['run_id'])['targets'][0]
    summary['predecessor']={'plan_id':plan['plan_id'],'run_id':reserved['run_id'],
        'payload_digest':plan['payload_digest'],'common_status':old_target['status'],
        'common_external_id':old_target['external_id'],
        'common_readback_evidence_digest':old_target['readback']['evidence_digest'],
        'common_readback_verified_at':old_target['readback']['verified_at']}
    evidence=release_adapters.bind_native_common_readback(source, summary)
    _retain_owned_comparison(store, reused['run_id'], evidence)
    assert _recover(store, new_facts, reused['run_id'])['state'] == 'READONLY_REUSE'
    assert technical.inspect_existing(store, reserved['run_id'])['state'] == 'CONFIRMED_WRITE'
    with store._connect_readonly() as db:
        assert db.execute("SELECT COUNT(*) FROM release_runs WHERE technical_execution_state='CONFIRMED_WRITE'").fetchone()[0] == 1


def test_mutation_or_account_change_cannot_borrow_a_persistent_reservation(live, monkeypatch, tmp_path):
    store, plan, payload, facts = _setup(live, monkeypatch, tmp_path)
    reserved=_reserve(store, facts)
    with technical._existing_transaction(store) as db:
        with pytest.raises(release_store.ReleaseAuthorizationError, match='MUTATION_CHANGED'):
            technical._consume_in_transaction(db, replace(facts, mutation_bytes=b'{}'), reserved['run_id'])
        with pytest.raises(release_store.ReleaseAuthorizationError, match='PREPARATION_BINDING_CHANGED'):
            technical._consume_in_transaction(db, replace(facts, account_identity_digest='caller-account'), reserved['run_id'])
    assert technical.inspect_existing(store, reserved['run_id'])['state'] == 'RESERVED'


def test_changed_retained_bytes_cannot_confirm_unknown_or_release_another_request(live, monkeypatch, tmp_path):
    store, plan, payload, facts = _setup(live, monkeypatch, tmp_path)
    reserved = _reserve(store, facts); _consume(store, facts, reserved['run_id'])
    _retain_owned_comparison(store, reserved['run_id'], _normal_historical_evidence(payload, monkeypatch))
    with technical._existing_transaction(store) as db:
        db.execute('UPDATE release_target_readbacks SET evidence_json=? WHERE run_id=?',
                   ('{}', reserved['run_id']))
    with pytest.raises(release_store.ReleaseAuthorizationError, match='RETAINED_READBACK_CHANGED'):
        _recover(store, facts, reserved['run_id'])
    assert technical.inspect_existing(store, reserved['run_id'])['state'] == 'UNKNOWN'
    assert not _consume(store, facts, reserved['run_id'])['consumed']
