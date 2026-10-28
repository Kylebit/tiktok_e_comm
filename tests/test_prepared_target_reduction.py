"""Cancellation intake must preserve history and never retire an unknown writer."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import sqlite3

import pytest

from shared_platform.workbench_engine import WorkbenchEngine


TARGETS = sorted([
    'tiktok:LH_PH', 'tiktok:LH_MY', 'tiktok:LH_TH', 'tiktok:LH_VN',
    'tiktok:HB_PH', 'tiktok:HB_MY', 'tiktok:HB_TH', 'tiktok:HB_VN',
    'tiktok:GB', 'tiktok:MX', 'shopee:PH', 'shopee:MY', 'shopee:TH',
    'shopee:VN', 'ozon:RU',
])


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


@pytest.fixture
def prepared(tmp_path):
    engine = WorkbenchEngine(tmp_path / 'tasks.db', {'code_version': 'pinned-original'}, clock=lambda: 1000)
    task = engine.create({'template': 'publication', 'source_key': 'original',
                         'scope': {'offer_id': '4039931504', 'shops': TARGETS, 'skus': []}})
    checkpoint = {'native_preparation': {'prepared_reference': 'r1-prepared:' + '1' * 64}}
    with engine.transaction() as db:
        db.execute('UPDATE workbench_execution SET checkpoint_json=? WHERE task_id=?',
                   (encoded(checkpoint).decode(), task['task_id']))
    identity = {'offer_id': '4039931504', 'round1_snapshot_digest': 'sha256:' + '2' * 64,
                'first_review_digest': 'sha256:' + '3' * 64, 'identity_digest': 'sha256:' + '4' * 64}
    predecessor = {'common_collectbox_id': '4039931504', 'product_center_revision': 2,
                   'snapshot_digest': identity['round1_snapshot_digest'],
                   'first_review_digest': identity['first_review_digest'], 'R2_identity': identity,
                   'configured_targets': [{'target': label, 'configured_shop_id':
                                          '16075432' if label == 'ozon:RU' else str(i),
                                          'frozen_copy': {'title': 'retained-' + label},
                                          'frozen_price': {'amount': i + 10},
                                          'frozen_category': {'id': str(i)},
                                          'assets': ['retained-original-image']}
                                         for i, label in enumerate(TARGETS)]}
    raw = encoded(predecessor)
    request = {
        'schema': 'detached-product-task-target-reduction-request/v1', 'task_id': task['task_id'],
        'scope_authority': {
            'schema': 'exact-product-scope-reduction-authority/v1',
            'common_collect_box_id': '4039931504', 'user_message_id': 'exact-user-message',
            'user_message_at_utc': '2026-10-09T05:43:58Z', 'verbatim_user_instruction': 'OZON不做了你跳过',
            'received_via_parent_thread': 'coordinator-thread',
            'removed_target': {'target': 'ozon:RU', 'configured_shop_id': '16075432',
                               'status': 'USER_CANCELLED_THIS_PRODUCT_ONLY'},
            'remaining_target_labels': [x for x in TARGETS if x != 'ozon:RU'],
            'remaining_target_count': 14, 'other_Ozon_business_changed': False,
            'new_publication_approval_granted': False, 'new_paid_or_refresh_authorization_granted': False,
        },
        'predecessor_lineage': {
            'predecessor_review_file_sha256': hashlib.sha256(raw).hexdigest(),
            'predecessor_target_labels': TARGETS, 'predecessor_target_count': 15,
            'predecessor_product_center_revision': 2,
            'predecessor_R1_snapshot_digest': identity['round1_snapshot_digest'],
            'predecessor_first_review_digest': identity['first_review_digest'],
            'predecessor_R2_identity': identity, 'new_R1_or_R2_approval_or_digest_created': False,
        },
        'desired_business_scope': {**task['scope'], 'shops': [x for x in TARGETS if x != 'ozon:RU']},
        'execution_authority': False, 'user_final_publication_approval': False,
        'business_writes': 0, 'paid_calls': 0, 'new_refreshes': 0,
    }
    return engine, task['task_id'], request, raw, hashlib.sha256(encoded(checkpoint)).hexdigest()


def record(prepared, **changes):
    engine, task_id, request, raw, checkpoint = prepared
    arguments = dict(request=request, expected_version=engine.release,
                     expected_checkpoint_sha256=checkpoint, predecessor_review_bytes=raw)
    arguments.update(changes)
    return engine.record_publication_target_reduction(task_id, **arguments)


def rows(engine):
    with engine.transaction() as db:
        return {name: [tuple(row) for row in db.execute('SELECT * FROM ' + name)]
                for name in ('workbench_tasks', 'workbench_execution', 'workbench_resource_locks',
                             'workbench_events', 'workbench_domain_operations',
                             'workbench_domain_locks', 'workbench_publication_scope_reductions')}


def test_only_this_product_ozon_is_cancelled_with_original_task_unchanged(prepared):
    engine, task_id, _, _, _ = prepared
    other = engine.create({'template': 'publication', 'source_key': 'other',
                           'scope': {'offer_id': 'another-offer', 'shops': ['ozon:RU']}})
    before = rows(engine)
    result = record(prepared)
    after = rows(engine)
    for table in ('workbench_tasks', 'workbench_execution', 'workbench_resource_locks'):
        assert after[table] == before[table]
    view = engine.get(task_id)
    assert view['scope']['shops'] == TARGETS
    assert view['effective_scope']['shops'] == [x for x in TARGETS if x != 'ozon:RU']
    assert view['target_cancellations'] == [{'target': 'ozon:RU', 'configured_shop_id': '16075432',
                                          'status': 'USER_CANCELLED_THIS_PRODUCT_ONLY'}]
    assert result['original_version'] == engine.release
    assert result['checkpoint_sha256'] == prepared[4]
    assert result['execution_authority'] is False
    assert result['consumer_adopted'] is False
    assert engine.get(other['task_id'])['scope']['shops'] == ['ozon:RU']
    assert 'effective_scope' not in engine.get(other['task_id'])
    assert len(after['workbench_events']) == len(before['workbench_events']) + 1


def test_exact_replay_and_concurrent_replay_are_idempotent(prepared):
    first = record(prepared)
    before = rows(prepared[0])
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: record(prepared), range(2)))
    assert results == [first, first]
    assert rows(prepared[0]) == before
    altered = deepcopy(prepared[2]); altered['scope_authority']['user_message_id'] = 'another-message'
    with pytest.raises(ValueError, match='already has a different'):
        record(prepared, request=altered)
    assert rows(prepared[0]) == before


@pytest.mark.parametrize('change', ['offer', 'task', 'shop', 'remaining', 'r2', 'r1', 'approval', 'paid', 'common'])
def test_conflicting_or_broader_request_is_rejected_without_mutation(prepared, change):
    request = deepcopy(prepared[2]); before = rows(prepared[0])
    if change == 'offer': request['desired_business_scope']['offer_id'] = 'other'
    if change == 'task': request['task_id'] = 'other-task'
    if change == 'shop': request['scope_authority']['removed_target']['configured_shop_id'] = 'other-shop'
    if change == 'remaining': request['desired_business_scope']['shops'].remove('tiktok:GB')
    if change == 'r2': request['predecessor_lineage']['predecessor_R2_identity']['identity_digest'] = 'sha256:' + '9' * 64
    if change == 'r1': request['predecessor_lineage']['predecessor_R1_snapshot_digest'] = 'sha256:' + '9' * 64
    if change == 'approval': request['scope_authority']['new_publication_approval_granted'] = True
    if change == 'paid': request['paid_calls'] = 1
    if change == 'common': request['scope_authority']['removed_target']['target'] = 'miaoshou:COMMON'
    with pytest.raises(ValueError): record(prepared, request=request)
    assert rows(prepared[0]) == before


@pytest.mark.parametrize('state', ['running', 'waiting_domain', 'reconciliation_required', 'completed', 'cancelled'])
def test_active_expired_or_unresolved_writer_is_never_retired(prepared, state):
    engine, task_id, *_ = prepared
    with engine.transaction() as db:
        db.execute('UPDATE workbench_execution SET state=?,worker=?,lease_token=?,lease_until=? WHERE task_id=?',
                   (state, 'original-writer', 'retained-token', 999, task_id))
        db.execute('INSERT INTO workbench_resource_locks VALUES(?,?)', ('product-offer:4039931504', task_id))
    before = rows(engine)
    with pytest.raises(ValueError, match='writer|unresolved|inactive'):
        record(prepared)
    assert rows(engine) == before


@pytest.mark.parametrize('guard', ['external', 'lock', 'attached', 'domain', 'lease', 'late_checkpoint',
                                   'final_review', 'version', 'checkpoint', 'predecessor'])
def test_boundary_conflicts_leave_all_records_unchanged(prepared, guard):
    engine, task_id, *_ = prepared; changes = {}
    with engine.transaction() as db:
        if guard == 'external': db.execute('UPDATE workbench_execution SET external_started=1 WHERE task_id=?', (task_id,))
        if guard == 'lock': db.execute('INSERT INTO workbench_resource_locks VALUES(?,?)', ('product-offer:4039931504', task_id))
        if guard == 'attached': db.execute('INSERT INTO workbench_external_tasks VALUES(?,?)', (task_id, '{}'))
        if guard == 'lease': db.execute('UPDATE workbench_execution SET worker=?,lease_token=?,lease_until=? WHERE task_id=?',
                                       ('stale-but-unretired', 'retained', 999, task_id))
        if guard == 'late_checkpoint': db.execute("UPDATE workbench_execution SET checkpoint_json='{}' WHERE task_id=?", (task_id,))
        if guard == 'final_review': db.execute('INSERT INTO workbench_review_identity VALUES(?,?,?)',
                                              (task_id, 'single-final-review/v1', 1))
        if guard == 'domain':
            # An unresolved historical operation must block intake even if its
            # original task no longer has enough scope to recreate the claim.
            db.execute('INSERT INTO workbench_domain_operations VALUES(?,?,?,?,?,?)',
                       ('old-domain', '[]', task_id, 'inflight', '', '2026-10-09T00:00:00Z'))
    if guard == 'version': changes['expected_version'] = {'code_version': 'another-version'}
    if guard == 'checkpoint': changes['expected_checkpoint_sha256'] = '0' * 64
    if guard == 'predecessor': changes['predecessor_review_bytes'] = b'{}'
    before = rows(engine)
    with pytest.raises(ValueError): record(prepared, **changes)
    assert rows(engine) == before


def test_cancelled_scope_cannot_be_consumed_by_unmodified_worker(prepared):
    engine, task_id, *_ = prepared
    record(prepared)
    engine.register_executor('legacy', ['publication'], engine.release)
    assert engine.claim(task_id, 'legacy') is None
    assert engine.get(task_id)['scope_reduction_consumer_required'] is True
    assert engine.get(task_id)['execution_state'] == 'scope_reduction_pending_consumer'


def test_cancellation_record_is_append_only(prepared):
    engine, task_id, *_ = prepared; record(prepared)
    for statement in ('DELETE FROM workbench_publication_scope_reductions WHERE task_id=?',
                      "UPDATE workbench_publication_scope_reductions SET request_digest='changed' WHERE task_id=?"):
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            with engine.transaction() as db: db.execute(statement, (task_id,))


def test_inert_marketplace_selector_keeps_all_fourteen_facts_and_r1_r2(prepared):
    from shared_platform.prepared_target_reduction import select_marketplace_targets
    result = record(prepared)
    predecessor = json.loads(prepared[3])
    before = deepcopy(predecessor)
    labels = select_marketplace_targets(['miaoshou:COMMON', *TARGETS], offer_id='4039931504',
        r2_identity=predecessor['R2_identity'], reduction=result)
    retained = [row for row in predecessor['configured_targets'] if row['target'] in labels]
    assert len(retained) == 14
    assert retained == [row for row in before['configured_targets'] if row['target'] != 'ozon:RU']
    assert predecessor == before


@pytest.mark.parametrize('change', ['digest', 'offer', 'identity', 'labels'])
def test_inert_marketplace_selector_rejects_other_product_or_frozen_lineage(prepared, change):
    from shared_platform.prepared_target_reduction import select_marketplace_targets
    result = record(prepared)
    args = dict(original_targets=TARGETS, offer_id='4039931504',
                r2_identity=json.loads(prepared[3])['R2_identity'], reduction=result)
    if change == 'digest': result['effective_scope']['shops'].append('another:store')
    if change == 'offer': args['offer_id'] = 'other'
    if change == 'identity': args['r2_identity'] = {'offer_id': 'other'}
    if change == 'labels': args['original_targets'] = TARGETS[:-1]
    with pytest.raises(ValueError): select_marketplace_targets(**args)


def test_direct_legacy_publication_cannot_bypass_cancellation_or_mutate_lease(prepared, monkeypatch):
    from shared_platform import operations_domain_guard as guard
    engine, *_ = prepared; record(prepared)
    before = rows(engine)
    monkeypatch.setattr(guard, 'engine_for', lambda _: engine)
    monkeypatch.setattr(engine, 'dashboard', lambda: pytest.fail('guard must run before mutating dashboard'))
    for labels in (TARGETS, [x for x in TARGETS if x != 'ozon:RU'], ['ozon:RU']):
        with pytest.raises(ValueError, match='CONSUMER_NOT_ADOPTED'):
            guard.begin_publication({'product_id': '4039931504', 'seller_sku': '0989', 'plan_id': 'old'}, labels, 'isolated')
    engine.assert_publication_scope_consumable('another-offer')
    assert rows(engine) == before


def test_direct_native_publication_cannot_bypass_cancellation(prepared, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from shared_platform.native_sole_final_service import check_current_rounds
    from shared_platform.release_store import ReleaseStore
    from shared_platform import workbench_publication_native
    engine, *_ = prepared; record(prepared)
    store = ReleaseStore(tmp_path / 'native.db')
    # The guard must precede current-round file loading or provider activity.
    class ReadOnlyStore:
        def _connect_readonly(self):
            conn = sqlite3.connect(':memory:'); conn.row_factory = sqlite3.Row
            conn.execute('CREATE TABLE release_plans(plan_id TEXT,payload_json TEXT)')
            conn.execute('INSERT INTO release_plans VALUES(?,?)',
                         ('old', json.dumps({'product_id': '4039931504'})))
            conn.commit(); conn.execute('PRAGMA query_only=ON'); return conn
    monkeypatch.setattr(workbench_publication_native, 'load_service_r2_documents',
                        lambda *_: pytest.fail('no R1/R2 reload or provider call after cancellation'))
    before = rows(engine)
    with pytest.raises(ValueError, match='CONSUMER_NOT_ADOPTED'):
        check_current_rounds(ReadOnlyStore(), 'old', operations=SimpleNamespace(engine=engine))
    assert rows(engine) == before
    assert not store.path.exists()
