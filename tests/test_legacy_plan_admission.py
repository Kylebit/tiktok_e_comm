"""Synthetic admission evidence; no Product Center route or provider is invoked."""

import hashlib
import json
import sqlite3

import pytest

from shared_platform.legacy_plan_admission import classify_legacy_plan_admission
from shared_platform.release_store import preview_release_plan


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


R1_DIGEST = 'sha256:' + '0' * 64
R2_FIELDS = {'schema_version': 'publication-r2-identity/v1', 'offer_id': 'offer-a',
             'round1_snapshot_digest': R1_DIGEST,
             'first_review_digest': 'sha256:' + '1' * 64,
             'generation_identity_digest': 'sha256:' + '2' * 64,
             'generation_digest': 'sha256:' + '3' * 64,
             'translation_plan_digest': 'sha256:' + '4' * 64,
             'translation_result_digest': 'sha256:' + '5' * 64,
             'qa_digest': 'sha256:' + '6' * 64,
             'artifact_digests': ['sha256:' + '7' * 64]}
R2_IDENTITY = {**R2_FIELDS, 'identity_digest': 'sha256:' + hashlib.sha256(
    encoded(R2_FIELDS).encode()).hexdigest()}
NATIVE_R1 = {'offer_id': 'offer-a', 'targets': ['tiktok:LH_MY'],
             'snapshot_digest': R1_DIGEST, 'prepared_reference': 'prepared-a',
             'internal_sku': '1234', 'seller_sku': '1234'}

COMMON_PAYLOAD = {'plan_id': 'common-a', 'product_id': 'offer-a', 'product_revision': 1,
                  'seller_sku': '1234', 'product_package_id': 'product-package',
                  'content_package_id': 'content-package', 'targets': ['miaoshou:COMMON'],
                  'r3_stage_binding': {'schema_version': 'r3-common-stage/v1',
                                       'marketplace_targets': ['tiktok:LH_MY'],
                                       'execution_scope': ['miaoshou:COMMON'],
                                       'image_approval_scope': 'ROUND2_IMAGES_ONLY',
                                       'write_approval_source': 'ReleaseStore',
                                       'round1_snapshot_digest': R1_DIGEST,
                                       'r2_identity': R2_IDENTITY}}
COMMON_PLAN = preview_release_plan(COMMON_PAYLOAD)


def fixture(tmp_path):
    tasks = tmp_path / 'tasks.db'
    release = tmp_path / 'release.db'
    scope = {'offer_id': 'offer-a', 'skus': ['1234'], 'shops': ['tiktok:LH_MY']}
    task_conn = sqlite3.connect(tasks)
    task_conn.executescript('''
        CREATE TABLE workbench_tasks(task_id TEXT PRIMARY KEY,status TEXT);
        CREATE TABLE workbench_execution(task_id TEXT PRIMARY KEY,template TEXT,scope_json TEXT,
          state TEXT,step_index INTEGER,steps_json TEXT,action_json TEXT,checkpoint_json TEXT,
          external_started INTEGER,worker TEXT,lease_token TEXT,lease_until REAL);
        CREATE TABLE workbench_review_identity(task_id TEXT PRIMARY KEY,review_mode TEXT,generation INTEGER);
        CREATE TABLE workbench_events(id INTEGER PRIMARY KEY,task_id TEXT,event_type TEXT,detail_json TEXT);
        CREATE TABLE workbench_external_tasks(task_id TEXT PRIMARY KEY);
        CREATE TABLE workbench_domain_operations(operation_id TEXT PRIMARY KEY,owner_task_id TEXT,state TEXT);
    ''')
    task_conn.commit()
    task_conn.close()
    release_conn = sqlite3.connect(release)
    release_conn.executescript('''
        CREATE TABLE release_plans(plan_id TEXT PRIMARY KEY,product_id TEXT,seller_sku TEXT,
          target_labels_json TEXT,payload_json TEXT,payload_digest TEXT,confirmation_token TEXT,status TEXT);
        CREATE TABLE release_approvals(plan_id TEXT PRIMARY KEY,payload_digest TEXT,
          confirmation_token TEXT,approved_by TEXT,user_approved INTEGER,status TEXT);
        CREATE TABLE release_final_review_decisions(decision_id TEXT PRIMARY KEY,common_plan_id TEXT,
          offer_id TEXT,seller_sku TEXT,common_payload_digest TEXT);
    ''')
    release_conn.execute('INSERT INTO release_plans VALUES(?,?,?,?,?,?,?,?)',
                         ('common-a', 'offer-a', '1234', encoded(['miaoshou:COMMON']),
                          encoded(COMMON_PAYLOAD), COMMON_PLAN['payload_digest'],
                          COMMON_PLAN['confirmation_token'], 'PENDING_APPROVAL'))
    release_conn.commit()
    release_conn.close()
    return tasks, release, scope


def add_task(path, task_id, scope, *, mode='legacy', plan='common-a', copied_action=None):
    binding = ({'adapter': 'publication-common/v1', 'offer_id': scope['offer_id'],
                'plan_id': plan, 'payload_digest': COMMON_PLAN['payload_digest'],
                'scope_digest': hashlib.sha256(encoded(scope).encode()).hexdigest(),
                'round1_snapshot_digest': R1_DIGEST} if mode == 'legacy' else
               {'adapter': 'single-final-review/v1', 'review_mode': 'single-final-review/v1',
                'offer_id': scope['offer_id'], 'sku': scope['skus'][0], 'targets': ['tiktok:LH_MY'],
                'common_plan_id': plan, 'common_payload_digest': COMMON_PLAN['payload_digest'],
                'round1_digest': R1_DIGEST, 'round2_digest': R2_IDENTITY['identity_digest'], 'revision': '1',
                'common_token_digest': 'sha256:' + hashlib.sha256(
                    COMMON_PLAN['confirmation_token'].encode()).hexdigest(),
                'preview_digest': 'preview',
                'task_id': task_id, 'action_id': task_id + '-action', 'generation': 1,
                'step_index': 2, 'scope_digest': hashlib.sha256(encoded(scope).encode()).hexdigest()})
    action = copied_action or {'kind': 'review', 'action_id': task_id + '-action',
                               'receipt_binding': binding}
    steps = [{'key': 'facts', 'checkpoint': {'native_r1': NATIVE_R1}},
             {'key': 'images', 'checkpoint': {'native_r2': R2_IDENTITY}},
             {'key': 'release'}]
    db = sqlite3.connect(path)
    try:
        db.execute('INSERT INTO workbench_tasks VALUES(?,?)', (task_id, 'waiting_approval'))
        db.execute('INSERT INTO workbench_execution VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   (task_id, 'publication', encoded(scope), 'waiting_user', 2,
                    encoded(steps),
                    encoded(action), '{}', 0, None, None, None))
        if mode != 'legacy':
            db.execute('INSERT INTO workbench_review_identity VALUES(?,?,?)',
                       (task_id, 'single-final-review/v1', 1))
        db.execute('INSERT INTO workbench_events(task_id,event_type,detail_json) VALUES(?,?,?)',
                   (task_id, 'user_action_required', encoded(action)))
        db.commit()
    finally:
        db.close()
    return action


def classify(tasks, release, offer='offer-a', plan='common-a'):
    return classify_legacy_plan_admission(tasks, release, offer_id=offer, plan_id=plan).status


def test_exact_legacy_and_unrelated_offer(tmp_path):
    tasks, release, scope = fixture(tmp_path)
    add_task(tasks, 'legacy-a', scope)
    add_task(tasks, 'new-b', {'offer_id': 'offer-b', 'skus': ['9999'], 'shops': ['tiktok:LH_MY']},
             mode='new', plan='common-b')
    assert classify(tasks, release) == 'LEGACY_ALLOWED'


def test_exact_new_and_cas(tmp_path):
    tasks, release, scope = fixture(tmp_path)
    add_task(tasks, 'new-a', scope, mode='new')
    assert classify(tasks, release) == 'NEW_MODE_FORBIDDEN'
    db = sqlite3.connect(release)
    try:
        db.execute('INSERT INTO release_final_review_decisions VALUES(?,?,?,?,?)',
                   ('decision-a', 'common-a', 'offer-a', '1234', COMMON_PLAN['payload_digest']))
        db.commit()
    finally:
        db.close()
    assert classify(tasks, release) == 'NEW_MODE_FORBIDDEN'


def test_mixed_duplicate_copy_and_missing(tmp_path):
    tasks, release, scope = fixture(tmp_path)
    action = add_task(tasks, 'legacy-a', scope)
    add_task(tasks, 'legacy-copy', scope, copied_action=action)
    assert classify(tasks, release) == 'AMBIGUOUS/UNAVAILABLE'
    assert classify(tmp_path / 'absent.db', release) == 'AMBIGUOUS/UNAVAILABLE'


def test_source_bytes_and_directory_unchanged(tmp_path):
    tasks, release, scope = fixture(tmp_path)
    add_task(tasks, 'legacy-a', scope)
    def inventory():
        root = tmp_path.stat()
        entries = {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(),
                            p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns)
                   for p in tmp_path.iterdir()}
        return (root.st_mtime_ns, root.st_ctime_ns), entries
    before = inventory()
    assert classify(tasks, release) == 'LEGACY_ALLOWED'
    assert inventory() == before  # Access time is deliberately excluded on Windows.


def edit(path, statement, params=()):
    db = sqlite3.connect(path)
    try:
        db.execute(statement, params)
        db.commit()
    finally:
        db.close()


def change_stage(release, change):
    db = sqlite3.connect(release)
    try:
        payload = json.loads(db.execute('SELECT payload_json FROM release_plans').fetchone()[0])
        change(payload['r3_stage_binding'])
        preview = preview_release_plan(payload)
        db.execute('UPDATE release_plans SET payload_json=?,payload_digest=?,confirmation_token=?',
                   (encoded(payload), preview['payload_digest'], preview['confirmation_token']))
        db.commit()
    finally:
        db.close()


def sync_action_to_stage(tasks, release):
    db = sqlite3.connect(release)
    try:
        payload_json, digest = db.execute('SELECT payload_json,payload_digest FROM release_plans').fetchone()
    finally:
        db.close()
    r1 = json.loads(payload_json)['r3_stage_binding']['round1_snapshot_digest']
    db = sqlite3.connect(tasks)
    try:
        action = json.loads(db.execute('SELECT action_json FROM workbench_execution').fetchone()[0])
        action['receipt_binding']['payload_digest'] = digest
        action['receipt_binding']['round1_snapshot_digest'] = r1
        db.execute('UPDATE workbench_execution SET action_json=?', (encoded(action),))
        db.execute('UPDATE workbench_events SET detail_json=?', (encoded(action),))
        db.commit()
    finally:
        db.close()


def change_step(tasks, index, change):
    db = sqlite3.connect(tasks)
    try:
        steps = json.loads(db.execute('SELECT steps_json FROM workbench_execution').fetchone()[0])
        change(steps[index]['checkpoint'])
        db.execute('UPDATE workbench_execution SET steps_json=?', (encoded(steps),))
        db.commit()
    finally:
        db.close()


@pytest.mark.parametrize('missing', ['r1_null', 'r2_empty', 'r2_digest_missing',
                                     'r2_digest_invalid', 'r2_offer_missing'])
def test_missing_or_malformed_common_evidence_never_allows_legacy(tmp_path, missing):
    tasks, release, scope = fixture(tmp_path)
    add_task(tasks, 'legacy-a', scope)
    assert classify(tasks, release) == 'LEGACY_ALLOWED'
    if missing == 'r1_null':
        change_stage(release, lambda stage: stage.update(round1_snapshot_digest=None))
        change_step(tasks, 0, lambda checkpoint: checkpoint.update(native_r1={}))
    elif missing == 'r2_empty':
        change_stage(release, lambda stage: stage.update(r2_identity={}))
        change_step(tasks, 1, lambda checkpoint: checkpoint.update(native_r2={}))
    elif missing == 'r2_digest_missing':
        change_stage(release, lambda stage: stage['r2_identity'].pop('identity_digest'))
    elif missing == 'r2_digest_invalid':
        change_stage(release, lambda stage: stage['r2_identity'].update(identity_digest='sha256:' + 'f' * 64))
    else:
        change_stage(release, lambda stage: stage['r2_identity'].pop('offer_id'))
    sync_action_to_stage(tasks, release)
    assert classify(tasks, release) == 'AMBIGUOUS/UNAVAILABLE'


@pytest.mark.parametrize('missing', ['r1_offer', 'r1_sku', 'r1_targets', 'r2_digest'])
def test_incomplete_frozen_task_evidence_never_allows_legacy(tmp_path, missing):
    tasks, release, scope = fixture(tmp_path)
    add_task(tasks, 'legacy-a', scope)
    if missing == 'r2_digest':
        change_step(tasks, 1, lambda checkpoint: checkpoint['native_r2'].pop('identity_digest'))
    else:
        field = {'r1_offer': 'offer_id', 'r1_sku': 'seller_sku',
                 'r1_targets': 'targets'}[missing]
        change_step(tasks, 0, lambda checkpoint: checkpoint['native_r1'].pop(field))
    assert classify(tasks, release) == 'AMBIGUOUS/UNAVAILABLE'


@pytest.mark.parametrize('label', ['unknown:PLACE', 'tiktok:LH_XX', 'shopee:US',
                                   'ozon:MY', 'miaoshou:COMMON'])
def test_unsupported_nested_marketplace_target_never_allows_legacy(tmp_path, label):
    tasks, release, scope = fixture(tmp_path)
    add_task(tasks, 'legacy-a', scope)
    assert classify(tasks, release) == 'LEGACY_ALLOWED'
    change_stage(release, lambda stage: stage.update(marketplace_targets=[label]))
    changed_scope = dict(scope, shops=[label])
    db = sqlite3.connect(tasks)
    try:
        steps = json.loads(db.execute('SELECT steps_json FROM workbench_execution').fetchone()[0])
        steps[0]['checkpoint']['native_r1']['targets'] = [label]
        db.execute('UPDATE workbench_execution SET scope_json=?,steps_json=?',
                   (encoded(changed_scope), encoded(steps)))
        db.commit()
    finally:
        db.close()
    sync_action_to_stage(tasks, release)
    db = sqlite3.connect(tasks)
    try:
        action = json.loads(db.execute('SELECT action_json FROM workbench_execution').fetchone()[0])
        action['receipt_binding']['scope_digest'] = hashlib.sha256(
            encoded(changed_scope).encode()).hexdigest()
        db.execute('UPDATE workbench_execution SET action_json=?', (encoded(action),))
        db.execute('UPDATE workbench_events SET detail_json=?', (encoded(action),))
        db.commit()
    finally:
        db.close()
    assert classify(tasks, release) == 'AMBIGUOUS/UNAVAILABLE'


def replace_with_registered_receipts(tasks, *, approved_revision=1):
    first = {'offer_id': 'offer-a', 'snapshot_digest': R1_DIGEST,
             'approved_revision': approved_revision, 'binding_sha256': 'b' * 64,
             'targets': ['tiktok:LH_MY'], 'seller_sku': '1234', 'internal_sku': '1234'}
    receipt = {'revision': 1, 'binding_sha256': first['binding_sha256'],
               'consumer_identity': R2_IDENTITY, 'selection_digest': 'c' * 64}
    db = sqlite3.connect(tasks)
    try:
        steps = json.loads(db.execute('SELECT steps_json FROM workbench_execution').fetchone()[0])
        steps[0]['checkpoint'] = {'publication_identity': first}
        steps[1]['checkpoint'] = {'publication_identity': first, 'image_receipt': receipt}
        db.execute('UPDATE workbench_execution SET steps_json=?', (encoded(steps),))
        db.commit()
    finally:
        db.close()


def test_complete_registered_receipts_preserve_exact_legacy_claim(tmp_path):
    tasks, release, scope = fixture(tmp_path)
    add_task(tasks, 'legacy-a', scope)
    replace_with_registered_receipts(tasks)
    assert classify(tasks, release) == 'LEGACY_ALLOWED'
    change_step(tasks, 1, lambda checkpoint: checkpoint['image_receipt'].update(binding_sha256=''))
    assert classify(tasks, release) == 'AMBIGUOUS/UNAVAILABLE'


def test_registered_frozen_revision_must_match_common_revision(tmp_path):
    tasks, release, scope = fixture(tmp_path)
    add_task(tasks, 'legacy-a', scope)
    replace_with_registered_receipts(tasks, approved_revision=2)
    assert classify(tasks, release) == 'AMBIGUOUS/UNAVAILABLE'


def test_missing_plan_revision_cannot_match_new_mode_stringified_null(tmp_path):
    tasks, release, scope = fixture(tmp_path)
    add_task(tasks, 'new-a', scope, mode='new')
    assert classify(tasks, release) == 'NEW_MODE_FORBIDDEN'
    db = sqlite3.connect(release)
    try:
        payload = json.loads(db.execute('SELECT payload_json FROM release_plans').fetchone()[0])
        payload['product_revision'] = None
        preview = preview_release_plan(payload)
        db.execute('UPDATE release_plans SET payload_json=?,payload_digest=?,confirmation_token=?',
                   (encoded(payload), preview['payload_digest'], preview['confirmation_token']))
        db.commit()
    finally:
        db.close()
    db = sqlite3.connect(tasks)
    try:
        action = json.loads(db.execute('SELECT action_json FROM workbench_execution').fetchone()[0])
        binding = action['receipt_binding']
        binding['revision'] = 'None'
        binding['common_payload_digest'] = preview['payload_digest']
        binding['common_token_digest'] = 'sha256:' + hashlib.sha256(
            preview['confirmation_token'].encode()).hexdigest()
        db.execute('UPDATE workbench_execution SET action_json=?', (encoded(action),))
        db.execute('UPDATE workbench_events SET detail_json=?', (encoded(action),))
        db.commit()
    finally:
        db.close()
    assert classify(tasks, release) == 'AMBIGUOUS/UNAVAILABLE'


def test_mixed_and_duplicate_claims(tmp_path):
    tasks, release, scope = fixture(tmp_path)
    add_task(tasks, 'legacy-a', scope)
    add_task(tasks, 'new-a', scope, mode='new')
    assert classify(tasks, release) == 'AMBIGUOUS/UNAVAILABLE'
    edit(tasks, 'DELETE FROM workbench_review_identity WHERE task_id=?', ('new-a',))
    assert classify(tasks, release) == 'AMBIGUOUS/UNAVAILABLE'


@pytest.mark.parametrize('change', ['event', 'generation', 'scope', 'r1', 'r2',
                                    'lease', 'external', 'operation', 'missing_action'])
def test_stale_or_unowned_action_is_ambiguous(tmp_path, change):
    tasks, release, scope = fixture(tmp_path)
    action = add_task(tasks, 'new-a', scope, mode='new')
    if change == 'event':
        edit(tasks, 'UPDATE workbench_events SET detail_json=?', (encoded(dict(action, label='changed')),))
    elif change == 'generation':
        edit(tasks, 'UPDATE workbench_review_identity SET generation=2')
    elif change == 'scope':
        edit(tasks, 'UPDATE workbench_execution SET scope_json=?',
             (encoded({'offer_id': 'offer-a', 'skus': ['1234'], 'shops': ['shopee:MY']}),))
    elif change in ('r1', 'r2'):
        column = 0 if change == 'r1' else 1
        db = sqlite3.connect(tasks)
        try:
            steps = json.loads(db.execute('SELECT steps_json FROM workbench_execution').fetchone()[0])
            steps[column]['checkpoint'] = {}
            db.execute('UPDATE workbench_execution SET steps_json=?', (encoded(steps),))
            db.commit()
        finally:
            db.close()
    elif change == 'lease':
        edit(tasks, "UPDATE workbench_execution SET lease_token='stale'")
    elif change == 'external':
        edit(tasks, "INSERT INTO workbench_external_tasks VALUES('new-a')")
    elif change == 'operation':
        edit(tasks, "INSERT INTO workbench_domain_operations VALUES('op','new-a','running')")
    else:
        edit(tasks, 'UPDATE workbench_execution SET action_json=NULL')
    assert classify(tasks, release) == 'AMBIGUOUS/UNAVAILABLE'


def test_exact_marketplace_plan_and_other_common_history(tmp_path):
    tasks, release, scope = fixture(tmp_path)
    add_task(tasks, 'legacy-a', scope)
    market_payload = {'plan_id': 'market-a', 'product_id': 'offer-a', 'seller_sku': '1234',
                      'product_package_id': 'product-package',
                      'content_package_id': 'content-package', 'targets': ['tiktok:LH_MY'],
                      'r3_marketplace_binding': {'schema_version': 'r3-marketplace-stage/v1',
                                                 'common_plan_id': 'common-a',
                                                 'common_payload_digest': COMMON_PLAN['payload_digest']}}
    market_plan = preview_release_plan(market_payload)
    common_b_payload = dict(COMMON_PAYLOAD, plan_id='common-b')
    common_b_plan = preview_release_plan(common_b_payload)
    db = sqlite3.connect(release)
    try:
        db.execute("UPDATE release_plans SET status='APPROVED' WHERE plan_id='common-a'")
        db.execute('INSERT INTO release_approvals VALUES(?,?,?,?,?,?)',
                   ('common-a', COMMON_PLAN['payload_digest'], COMMON_PLAN['confirmation_token'],
                    'Kyle', 1, 'APPROVED'))
        db.execute('INSERT INTO release_plans VALUES(?,?,?,?,?,?,?,?)',
                   ('market-a', 'offer-a', '1234', encoded(['tiktok:LH_MY']),
                    encoded(market_payload), market_plan['payload_digest'],
                    market_plan['confirmation_token'], 'APPROVED'))
        db.execute('INSERT INTO release_approvals VALUES(?,?,?,?,?,?)',
                   ('market-a', market_plan['payload_digest'],
                    market_plan['confirmation_token'], 'Kyle', 1, 'APPROVED'))
        db.execute('INSERT INTO release_plans VALUES(?,?,?,?,?,?,?,?)',
                   ('common-b', 'offer-a', '1234', encoded(['miaoshou:COMMON']),
                    encoded(common_b_payload), common_b_plan['payload_digest'],
                    common_b_plan['confirmation_token'], 'PENDING_APPROVAL'))
        db.commit()
    finally:
        db.close()
    add_task(tasks, 'new-b', scope, mode='new', plan='common-b')
    assert classify(tasks, release, plan='market-a') == 'LEGACY_ALLOWED'
    assert classify(tasks, release) == 'LEGACY_ALLOWED'


def test_unassociated_plan_and_wal_fail_closed(tmp_path):
    tasks, release, scope = fixture(tmp_path)
    assert classify(tasks, release) == 'AMBIGUOUS/UNAVAILABLE'
    add_task(tasks, 'legacy-a', scope)
    sidecar = tmp_path / 'tasks.db-wal'
    sidecar.write_bytes(b'active')
    before = sidecar.read_bytes()
    assert classify(tasks, release) == 'AMBIGUOUS/UNAVAILABLE'
    assert sidecar.read_bytes() == before


def test_release_wal_and_approval_conflict_fail_closed(tmp_path):
    tasks, release, scope = fixture(tmp_path)
    add_task(tasks, 'legacy-a', scope)
    edit(release, 'INSERT INTO release_approvals VALUES(?,?,?,?,?,?)',
         ('common-a', COMMON_PLAN['payload_digest'], COMMON_PLAN['confirmation_token'],
          'Kyle', 1, 'APPROVED'))
    assert classify(tasks, release) == 'AMBIGUOUS/UNAVAILABLE'
    edit(release, 'DELETE FROM release_approvals')
    (tmp_path / 'release.db-wal').write_bytes(b'active')
    assert classify(tasks, release) == 'AMBIGUOUS/UNAVAILABLE'


def test_cas_conflicts_with_old_action_and_protects_unassociated_plan(tmp_path):
    tasks, release, scope = fixture(tmp_path)
    edit(release, 'INSERT INTO release_final_review_decisions VALUES(?,?,?,?,?)',
         ('decision-a', 'common-a', 'offer-a', '1234', COMMON_PLAN['payload_digest']))
    assert classify(tasks, release) == 'NEW_MODE_FORBIDDEN'
    add_task(tasks, 'legacy-a', scope)
    assert classify(tasks, release) == 'AMBIGUOUS/UNAVAILABLE'


def test_tampered_plan_digest_and_copied_cross_offer_plan_fail_closed(tmp_path):
    tasks, release, scope = fixture(tmp_path)
    add_task(tasks, 'legacy-a', scope)
    add_task(tasks, 'copied-b', {'offer_id': 'offer-b', 'skus': ['9999'],
                                 'shops': ['tiktok:LH_MY']}, mode='new')
    assert classify(tasks, release) == 'AMBIGUOUS/UNAVAILABLE'
    edit(tasks, 'DELETE FROM workbench_execution WHERE task_id=?', ('copied-b',))
    assert classify(tasks, release) == 'LEGACY_ALLOWED'
    edit(release, "UPDATE release_plans SET payload_digest='changed' WHERE plan_id='common-a'")
    assert classify(tasks, release) == 'AMBIGUOUS/UNAVAILABLE'
