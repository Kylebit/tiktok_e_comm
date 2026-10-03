"""Service-owned retained inputs remain separate from mutation authority.

All stores/products/transports below are owned fixtures. Neither historical
fixture approval nor persisted transport is a native account/budget receipt.
"""
from copy import deepcopy
import hashlib
import sqlite3

import pytest

from modules.products import server
from shared_platform import operations_publication_common as common
from shared_platform import publication_common_write_admission as admission
from shared_platform.release_store import ReleaseStore
from test_b4b_common_stage import CommonTransport, approve, context
from test_r3_common_source_facts import connect, rows
from test_round1_workspace_freeze import live


def prepared_common(tmp_path, monkeypatch):
    _, _, store, request = context(tmp_path, monkeypatch)
    code, view = server._preview_r3_common_stage(request)
    assert code == 200, view
    assert not store.path.exists()
    return store, store.create_plan(view['common']['plan']['payload']), request


def assert_closed(diagnostic):
    assert diagnostic['status'] == 'BLOCKED'
    assert diagnostic['blockers'][-2:] == [
        'COMMON_STANDING_POLICY_AUTHORITY_UNKNOWN',
        'COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN']
    assert diagnostic['final_review_available'] is False
    assert diagnostic['execution_authority'] is False
    assert not diagnostic.get('receipt_digest')
    assert diagnostic['external_writes_performed'] == []
    assert set(diagnostic['authority_facts'].values()) == {'UNKNOWN'}


def test_service_admission_reads_persisted_common_before_run_without_writes(tmp_path, monkeypatch):
    store, plan, _ = prepared_common(tmp_path, monkeypatch)
    with connect(store) as db:
        before = rows(db)
        expected = db.execute('SELECT payload_json FROM release_plans WHERE plan_id=?',
                              (plan['plan_id'],)).fetchone()[0].encode('utf-8')
        assert db.execute('SELECT COUNT(*) FROM release_runs').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0] == 0
    opens = []
    original = ReleaseStore._connect_readonly
    forbidden = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                 sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE,
                 sqlite3.SQLITE_CREATE_TRIGGER, sqlite3.SQLITE_DROP_TRIGGER}

    def readonly(actual):
        db = original(actual)
        opens.append(actual.path)
        db.set_authorizer(lambda action, *args: sqlite3.SQLITE_DENY
                          if action in forbidden else sqlite3.SQLITE_OK)
        return db

    monkeypatch.setattr(ReleaseStore, '_connect_readonly', readonly)
    diagnostic = admission.inspect_common_write_admission(plan, store=store)
    assert_closed(diagnostic)
    source = diagnostic['source_facts']
    assert source['status'] == 'RETAINED_IDENTITY_VERIFIED'
    assert source['origin_binding'] == diagnostic['binding']
    assert source['source_coverage'] == 'LOCAL_RETAINED_ONLY'
    assert source['official_provenance'] == source['budget_status'] == 'UNKNOWN'
    assert source['execution_authority'] is False
    assert source['common_plan_ids'] == source['unstarted_common_plan_ids'] == [plan['plan_id']]
    record = next(r for r in source['records'] if r['table'] == 'release_plans')
    assert record['evidence_digest'] == hashlib.sha256(expected).hexdigest()
    assert record['transport_observed'] is False
    assert opens == [store.path]
    with connect(store) as db:
        assert rows(db) == before


def test_http_blocked_entry_displays_retained_source_without_dispatch(tmp_path, monkeypatch):
    _, _, store, request = context(tmp_path, monkeypatch)
    io = CommonTransport(monkeypatch, synthetic_technical_authority=False)
    exact = approve(request)  # Historical fixture remains held, not a new decision.
    with connect(store) as db:
        before = rows(db)
    code, result = server._prepare_miaoshou_release({**exact, 'confirm_miaoshou_write': True})
    assert code == 409 and result['error'] == 'COMMON_TECHNICAL_ADMISSION_BLOCKED'
    assert result['source_facts']['status'] == 'RETAINED_IDENTITY_VERIFIED'
    assert result['source_facts']['origin_binding'] == result['binding']
    assert set(result['authority_facts'].values()) == {'UNKNOWN'}
    assert result['external_writes_performed'] == [] and io.mutations == 0
    with connect(store) as db:
        assert rows(db) == before


def test_task_run_consumes_service_store_source_without_upgrading_owner_or_authority(live, monkeypatch):
    from test_common_native_preparation_source import _native_common_chain
    engine, task, profile, _ = _native_common_chain(live, monkeypatch)
    view = common._read(server, task)
    store = server._release_store()
    plan = store.create_plan(view['common']['plan']['payload'])
    assert plan['plan_id'] == view['common']['plan']['plan_id']
    before = engine.get(task['task_id'])
    with connect(store) as db:
        store_before = rows(db)

    class Recorder:
        def __init__(self):
            self.calls = []

        def record_checkpoint(self, task_id, token, checkpoint):
            self.calls.append(('checkpoint', task_id, token, checkpoint))

        def await_domain(self, task_id, token, **values):
            self.calls.append(('wait', task_id, token, values))

    recorder = Recorder()
    assert common.run(recorder, task, None, profile, server_module=server) is False
    diagnostic = recorder.calls[0][3]['common_technical_admission']
    assert_closed(diagnostic)
    assert diagnostic['source_facts']['status'] == 'RETAINED_IDENTITY_VERIFIED'
    assert diagnostic['binding']['preparation_source']['owner_task_id'] == task['task_id']
    assert diagnostic['binding']['preparation_source']['execution_authority'] is False
    assert recorder.calls[1][3]['receipt_binding'] == diagnostic['binding']
    assert common.observe(engine, task, profile, server_module=server) is False
    assert engine.get(task['task_id']) == before
    with connect(store) as db:
        assert rows(db) == store_before


@pytest.mark.parametrize('mode', ['none', 'missing-db', 'dictionary', 'subclass', 'unpersisted'])
def test_preview_and_untrusted_store_cannot_create_db_or_adopt_source(tmp_path, monkeypatch, mode):
    _, _, store, request = context(tmp_path, monkeypatch)
    code, view = server._preview_r3_common_stage(request)
    assert code == 200
    plan = view['common']['plan']
    missing = tmp_path/'never-created'/'release.db'
    class PretendedStore(ReleaseStore):
        pass
    supplied = {'none': None, 'missing-db': ReleaseStore(missing),
                'dictionary': {'verified': True, 'path': str(store.path)},
                'subclass': PretendedStore(missing), 'unpersisted': store}[mode]
    if mode == 'unpersisted':
        store.create_plan({'plan_id': 'unrelated-local-plan', 'product_id': plan['product_id'],
                           'seller_sku': '1099', 'product_package_id': 'owned-product',
                           'content_package_id': 'owned-content', 'product_revision': 1,
                           'targets': ['ozon:RU']})
        with connect(store) as db:
            before = rows(db)
    diagnostic = admission.inspect_common_write_admission(plan, store=supplied)
    assert_closed(diagnostic)
    assert diagnostic['source_facts']['status'] == 'UNKNOWN'
    assert not missing.exists() and not missing.parent.exists()
    if mode == 'unpersisted':
        assert diagnostic['source_facts']['reason'] == 'COMMON_SOURCE_PLAN_NOT_PERSISTED'
        with connect(store) as db:
            assert rows(db) == before
    else:
        assert not store.path.exists()


@pytest.mark.parametrize('field', ['product_id', 'payload_digest', 'payload'])
def test_caller_identity_or_payload_cannot_borrow_persisted_common(tmp_path, monkeypatch, field):
    store, plan, _ = prepared_common(tmp_path, monkeypatch)
    claimed = deepcopy(plan)
    if field == 'payload':
        claimed[field]['title'] = 'Changed caller copy'
    else:
        claimed[field] += '-changed'
    with connect(store) as db:
        before = rows(db)
    diagnostic = admission.inspect_common_write_admission(claimed, store=store)
    assert_closed(diagnostic)
    assert diagnostic['source_facts']['status'] == 'INVALID'
    assert diagnostic['source_facts']['reason'] == 'COMMON_SOURCE_ADMISSION_ORIGIN_MISMATCH'
    with connect(store) as db:
        assert rows(db) == before


def test_wrong_store_and_changed_history_are_not_source_authority(tmp_path, monkeypatch):
    from test_b4b_publication_preview import reviewed_marketplace
    original_inspector = admission.inspect_common_write_admission
    monkeypatch.setattr(server, '_COMMON_DETAIL_OBSERVER_FACTORY', None)
    _, _, store, _, _, market = reviewed_marketplace(tmp_path, monkeypatch)
    monkeypatch.setattr(admission, 'inspect_common_write_admission', original_inspector)
    plan = store.get_plan(market['plan']['payload']['r3_marketplace_binding']['common_plan_id'])
    other = ReleaseStore(tmp_path/'other.db')
    other.create_plan({'plan_id': 'other-local-plan', 'product_id': plan['product_id'],
                      'seller_sku': '1099', 'product_package_id': 'owned-product',
                      'content_package_id': 'owned-content', 'product_revision': 1,
                      'targets': ['ozon:RU']})
    diagnostic = admission.inspect_common_write_admission(plan, store=other)
    assert_closed(diagnostic)
    assert diagnostic['source_facts']['status'] == 'UNKNOWN'
    assert diagnostic['source_facts']['reason'] == 'COMMON_SOURCE_PLAN_NOT_PERSISTED'
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE release_target_readbacks SET evidence_json=evidence_json || ' '")
    with connect(store) as db:
        before = rows(db)
    diagnostic = admission.inspect_common_write_admission(plan, store=store)
    assert_closed(diagnostic)
    assert diagnostic['source_facts']['status'] == 'INVALID'
    assert diagnostic['source_facts']['reason'] == 'COMMON_SOURCE_HISTORY_BYTES_CHANGED'
    with connect(store) as db:
        assert rows(db) == before


def test_normalized_history_is_retained_without_account_or_budget_authority(tmp_path, monkeypatch):
    from test_b4b_publication_preview import reviewed_marketplace
    original_inspector = admission.inspect_common_write_admission
    monkeypatch.setattr(server, '_COMMON_DETAIL_OBSERVER_FACTORY', None)
    _, _, store, _, _, market = reviewed_marketplace(tmp_path, monkeypatch)
    plan = store.get_plan(market['plan']['payload']['r3_marketplace_binding']['common_plan_id'])
    # Discard the fixture's synthetic future authority for the real boundary.
    monkeypatch.setattr(admission, 'inspect_common_write_admission', original_inspector)
    diagnostic = admission.inspect_common_write_admission(plan, store=store)
    assert_closed(diagnostic)
    source = diagnostic['source_facts']
    assert source['status'] == 'RETAINED_IDENTITY_VERIFIED'
    readback = next(r for r in source['records'] if r['table'] == 'release_target_readbacks')
    assert readback['transport_observed'] is False
    assert readback['wire_digest'] is readback['business_digest'] is None
    assert source['official_provenance'] == source['budget_status'] == 'UNKNOWN'
