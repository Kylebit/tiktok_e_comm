"""Synthetic source-image tests; no task service, domain store, or provider."""

from dataclasses import FrozenInstanceError
import hashlib
import os
import sqlite3

import pytest

from shared_platform import workbench_final_review_evidence as reader
from shared_platform.workbench_engine import ReceiptSnapshotUnavailable, WorkbenchEngine
from shared_platform.workbench_final_review_evidence import read_final_review_task_evidence
from shared_platform.workbench_store import _json


def frozen():
    return {'offer_id': 'offer-1', 'revision': 'revision-1', 'sku': '0001',
            'round1_digest': 'r1', 'round2_digest': 'r2',
            'targets': ['tiktok:MY', 'shopee:MY'], 'common_plan_id': 'plan-1',
            'common_payload_digest': 'payload-1', 'common_token_digest': 'token-1',
            'preview_digest': 'preview-1'}


def ready(tmp_path):
    engine = WorkbenchEngine(tmp_path / 'tasks.db', {'code_version': 'synthetic'})
    engine.register_executor('fixture', ['publication'], engine.release)
    task = engine.create({'template': 'publication', 'source_key': 'first',
                          'scope': {'offer_id': 'offer-1', 'skus': ['0001'],
                                    'shops': ['tiktok:MY', 'shopee:MY']}})['task_id']
    token = engine.claim(task, 'fixture')['lease_token']
    engine.complete_step(task, token, expected_step='facts', checkpoint={'r1': 'synthetic'})
    engine.complete_step(task, token, expected_step='images', checkpoint={'r2': 'synthetic'})
    action = engine.wait_for_final_review(task, token, label='Review', reason='frozen',
                                          receipt_binding=frozen())
    return engine, task, action


def directory_evidence(path):
    directory = tuple(getattr(path.lstat(), key) for key in
                      ('st_mode', 'st_dev', 'st_ino', 'st_nlink',
                       'st_size', 'st_ctime_ns', 'st_mtime_ns'))
    entries = tuple((item.name, tuple(getattr(item.lstat(), key) for key in
                                     ('st_mode', 'st_dev', 'st_ino', 'st_nlink',
                                      'st_size', 'st_ctime_ns', 'st_mtime_ns')),
                  hashlib.sha256(item.read_bytes()).hexdigest())
                 for item in sorted(path.iterdir()) if item.is_file())
    return directory, entries


def test_reader_returns_original_typed_immutable_evidence_without_source_changes(tmp_path):
    engine, task, action = ready(tmp_path)
    before = directory_evidence(tmp_path)
    evidence = read_final_review_task_evidence(engine.store.path, task)
    assert directory_evidence(tmp_path) == before
    assert evidence.task_id == task and evidence.action_id == action['action_id']
    assert evidence.generation == 1 and evidence.step_index == 2
    assert evidence.review_mode == 'single-final-review/v1'
    assert evidence.reference.targets == ('tiktok:MY', 'shopee:MY')
    assert evidence.reference.common_plan_id == 'plan-1'
    with pytest.raises(FrozenInstanceError):
        evidence.action_id = 'other'


def test_engine_get_expires_a_lease_but_evidence_reader_does_not(tmp_path):
    now = [1000.0]
    engine = WorkbenchEngine(tmp_path / 'tasks.db', {'code_version': 'synthetic'}, clock=lambda: now[0])
    engine.register_executor('fixture', ['publication'], engine.release)
    task = engine.create({'template': 'publication', 'source_key': 'expiring',
                          'scope': {'offer_id': 'offer-1'}})['task_id']
    engine.claim(task, 'fixture', ttl=1)
    now[0] = 1002.0
    before = directory_evidence(tmp_path)
    with pytest.raises(KeyError):
        read_final_review_task_evidence(engine.store.path, task)
    assert directory_evidence(tmp_path) == before
    assert engine.get(task)['execution_state'] == 'queued'
    assert directory_evidence(tmp_path) != before
    assert any(event['event_type'] == 'lease_expired' for event in engine.store.events(task))


@pytest.mark.parametrize('change', [
    'copied_action', 'stale_step', 'stale_generation', 'stale_scope',
    'stale_status', 'stale_lease', 'missing_event', 'changed_event',
    'external_task', 'unresolved_operation',
])
def test_stale_or_unowned_action_fails_closed(tmp_path, change):
    engine, task, action = ready(tmp_path)
    candidate = task
    with engine.transaction() as conn:
        if change == 'copied_action':
            candidate = 'forged-task'
            # A forged row on a second task must not borrow the first event.
            conn.execute('INSERT INTO workbench_tasks SELECT ?,title,project,business_line,owner,priority,status,'
                         'due_date,related_url,definition_of_done_json,blocked_reason,approval_status,'
                         'execution_notes,is_top3,?,created_at,updated_at,completed_at '
                         'FROM workbench_tasks WHERE task_id=?', (candidate, 'forged-source', task))
            conn.execute('INSERT INTO workbench_execution SELECT ?,template,scope_json,version_json,'
                         'request_digest,state,step_index,steps_json,action_json,checkpoint_json,result_url,'
                         'worker,lease_token,lease_until,external_started FROM workbench_execution WHERE task_id=?',
                         (candidate, task))
            conn.execute('INSERT INTO workbench_review_identity VALUES(?,?,?)',
                         (candidate, 'single-final-review/v1', 1))
            copied = dict(action)
            copied['receipt_binding'] = dict(action['receipt_binding'], task_id=candidate)
            conn.execute('UPDATE workbench_execution SET action_json=? WHERE task_id=?',
                         (_json(copied), candidate))
        elif change == 'stale_step':
            conn.execute('UPDATE workbench_execution SET step_index=3 WHERE task_id=?', (task,))
        elif change == 'stale_generation':
            conn.execute('UPDATE workbench_review_identity SET generation=2 WHERE task_id=?', (task,))
        elif change == 'stale_scope':
            conn.execute('UPDATE workbench_execution SET scope_json=? WHERE task_id=?',
                         (_json({'offer_id': 'other', 'skus': ['0001'], 'shops': []}), task))
        elif change == 'stale_status':
            conn.execute("UPDATE workbench_tasks SET status='todo' WHERE task_id=?", (task,))
        elif change == 'stale_lease':
            conn.execute("UPDATE workbench_execution SET lease_token='stale' WHERE task_id=?", (task,))
        elif change == 'missing_event':
            conn.execute("DELETE FROM workbench_events WHERE task_id=? AND event_type='user_action_required'", (task,))
        elif change == 'changed_event':
            conn.execute("UPDATE workbench_events SET detail_json=? WHERE task_id=? "
                         "AND event_type='user_action_required'", (_json(dict(action, label='changed')), task))
        elif change == 'external_task':
            conn.execute('INSERT INTO workbench_external_tasks VALUES(?,?)', (task, '{}'))
        else:
            conn.execute("INSERT INTO workbench_domain_operations VALUES(?,?,?,?,?,?)",
                         ('operation-1', '[]', task, 'inflight', '', 'now'))
    before = directory_evidence(tmp_path)
    with pytest.raises((KeyError, ValueError)):
        read_final_review_task_evidence(engine.store.path, candidate)
    assert directory_evidence(tmp_path) == before


@pytest.mark.parametrize('suffix', ['-wal', '-shm', '-journal'])
def test_any_source_sidecar_blocks_before_sqlite_open(tmp_path, suffix):
    engine, task, _ = ready(tmp_path)
    sidecar = tmp_path / ('tasks.db' + suffix)
    sidecar.write_bytes(b'active')
    before = directory_evidence(tmp_path)
    with pytest.raises(ReceiptSnapshotUnavailable):
        read_final_review_task_evidence(engine.store.path, task)
    assert directory_evidence(tmp_path) == before


def test_hardlink_alias_is_rejected(tmp_path):
    engine, task, _ = ready(tmp_path)
    alias = tmp_path / 'alias.db'
    os.link(engine.store.path, alias)
    before = directory_evidence(tmp_path)
    with pytest.raises(ReceiptSnapshotUnavailable):
        read_final_review_task_evidence(alias, task)
    assert directory_evidence(tmp_path) == before


def test_symlink_alias_is_rejected(tmp_path):
    engine, task, _ = ready(tmp_path)
    alias = tmp_path / 'alias.db'
    try:
        alias.symlink_to(engine.store.path)
    except OSError:
        pytest.skip('host cannot create a synthetic symlink')
    with pytest.raises(ValueError):
        read_final_review_task_evidence(alias, task)


def test_other_directory_file_drift_blocks_image(tmp_path, monkeypatch):
    engine, task, _ = ready(tmp_path)
    auxiliary = tmp_path / 'auxiliary.txt'
    auxiliary.write_text('before', encoding='utf-8')
    original = reader._receipt_database_image

    def changing_image(path):
        image = original(path)
        auxiliary.write_text('after', encoding='utf-8')
        return image

    monkeypatch.setattr(reader, '_receipt_database_image', changing_image)
    with pytest.raises(ReceiptSnapshotUnavailable):
        read_final_review_task_evidence(engine.store.path, task)


def test_open_writer_blocks_snapshot(tmp_path):
    engine, task, _ = ready(tmp_path)
    with sqlite3.connect(engine.store.path) as writer:
        writer.execute('BEGIN IMMEDIATE')
        before = directory_evidence(tmp_path)
        with pytest.raises(ReceiptSnapshotUnavailable):
            read_final_review_task_evidence(engine.store.path, task)
        assert directory_evidence(tmp_path) == before


def test_reader_rejects_invalid_image_and_missing_task(tmp_path):
    engine, task, _ = ready(tmp_path)
    with pytest.raises(ValueError):
        read_final_review_task_evidence('tasks.db', task)
    with pytest.raises(KeyError):
        read_final_review_task_evidence(engine.store.path, 'missing-task')
    other = tmp_path / 'invalid.db'
    other.write_bytes(b'invalid')
    with pytest.raises(ValueError):
        read_final_review_task_evidence(other, task)
