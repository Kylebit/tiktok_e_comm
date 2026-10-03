"""Synthetic read-only recovery of an exact, unapproved COMMON Store plan."""

from copy import deepcopy
from hashlib import sha256
import gc
import json
import os
import sqlite3
from pathlib import Path
import stat

import pytest

from modules.products import server
from shared_platform import publication_final_review_pending as pending_adapter
from shared_platform.publication_final_review_pending import (
    build_pending_final_review_preview,
    require_current_pending_final_review_preview,
)
from shared_platform.release_store import ReleaseStore
from test_b4b_common_stage import context


def pending(tmp_path, monkeypatch):
    documents, dashboard, store, request = context(tmp_path, monkeypatch)
    status, initial = server._preview_r3_common_stage(request)
    assert status == 200, initial
    payload = initial['common']['plan']['payload']
    store.create_plan(payload)
    status, view = server._preview_r3_common_stage(request)
    assert status == 200, view
    assert view['common']['plan']['status'] == 'PENDING_APPROVAL'
    return documents, dashboard, store, request, payload, view


def files_hashes(store):
    return {p.name: sha256(p.read_bytes()).hexdigest()
            for p in store.path.parent.iterdir() if p.is_file()}


def full_inventory(directory):
    result = {}
    for path in sorted(directory.iterdir()):
        info = path.lstat()
        entry = {'mode': info.st_mode, 'ino': info.st_ino, 'nlink': info.st_nlink,
                 'size': info.st_size, 'attributes': getattr(info, 'st_file_attributes', 0)}
        if stat.S_ISREG(info.st_mode) and not entry['attributes'] & 0x400:
            entry['sha256'] = sha256(path.read_bytes()).hexdigest()
        result[path.name] = entry
    return result


def test_hardlink_alias_cannot_hide_original_name_approved_wal(tmp_path, monkeypatch):
    documents, dashboard, store, _, payload, view = pending(tmp_path, monkeypatch)
    gc.collect()
    source = store.path
    writer = sqlite3.connect(source)
    reader = None
    try:
        assert writer.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
        writer.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        alias = tmp_path / 'alias.db'
        os.link(source, alias)
        plan = view['common']['plan']
        store.approve_plan(plan['plan_id'], user_approved=True, approved_by='Kyle',
                           confirmation_token=plan['confirmation_token'])
        gc.collect()
        reader = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
        reader.execute('BEGIN')
        assert reader.execute('SELECT status FROM release_plans WHERE plan_id=?',
                              (plan['plan_id'],)).fetchone()[0] == 'APPROVED'
        writer.close()
        writer = None
        gc.collect()
        assert Path(str(source) + '-wal').stat().st_size > 0
        before = full_inventory(tmp_path)
        with pytest.raises(ValueError, match='FINAL_REVIEW_PENDING_SOURCE_UNSTABLE'):
            build(documents, dashboard, ReleaseStore(alias), payload, view)
        assert full_inventory(tmp_path) == before
    finally:
        if reader is not None:
            reader.close()
        if writer is not None:
            writer.close()


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction test')
def test_dangling_junction_sidecar_fails_closed(tmp_path, monkeypatch):
    import _winapi

    documents, dashboard, store, _, payload, view = pending(tmp_path, monkeypatch)
    sidecar = Path(str(store.path) + '-wal')
    gone = tmp_path / 'gone'
    gone.mkdir()
    _winapi.CreateJunction(str(gone), str(sidecar))
    gone.rmdir()
    assert sidecar.lstat().st_file_attributes & 0x400
    before = full_inventory(tmp_path)
    with pytest.raises(ValueError, match='FINAL_REVIEW_PENDING_SOURCE_UNSTABLE'):
        build(documents, dashboard, store, payload, view)
    assert full_inventory(tmp_path) == before


def test_wal_db_without_sidecars_keeps_source_directory_exact(tmp_path, monkeypatch):
    documents, dashboard, store, _, payload, view = pending(tmp_path, monkeypatch)
    connection = sqlite3.connect(store.path)
    try:
        assert connection.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
        connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    finally:
        connection.close()
    before = files_hashes(store)
    assert store.path.name + '-wal' not in before
    assert store.path.name + '-shm' not in before
    preview = build(documents, dashboard, store, payload, view)
    assert preview.as_dict()['status'] == 'UNAPPROVED_PREVIEW'
    after = files_hashes(store)
    print('SOURCE_INVENTORY_DB_ONLY=' + json.dumps({'before': before, 'after': after}, sort_keys=True))
    assert after == before


def test_active_wal_fails_closed_without_source_changes(tmp_path, monkeypatch):
    documents, dashboard, store, _, payload, view = pending(tmp_path, monkeypatch)
    connection = sqlite3.connect(store.path)
    try:
        assert connection.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
        connection.execute('PRAGMA wal_autocheckpoint=0')
        connection.execute('CREATE TABLE pending_wal_latest(value TEXT NOT NULL)')
        connection.execute("INSERT INTO pending_wal_latest VALUES ('latest-committed')")
        connection.commit()
        before = files_hashes(store)
        assert store.path.name + '-wal' in before
        with pytest.raises(ValueError, match='FINAL_REVIEW_PENDING_SOURCE_UNSTABLE'):
            build(documents, dashboard, store, payload, view)
        after = files_hashes(store)
        print('SOURCE_INVENTORY_ACTIVE_WAL=' + json.dumps({'before': before, 'after': after}, sort_keys=True))
        assert after == before
    finally:
        connection.close()


def test_open_writer_handle_fails_closed_without_source_changes(tmp_path, monkeypatch):
    documents, dashboard, store, _, payload, view = pending(tmp_path, monkeypatch)
    connection = sqlite3.connect(store.path)
    try:
        connection.execute('PRAGMA schema_version').fetchone()
        before = files_hashes(store)
        with pytest.raises(ValueError, match='FINAL_REVIEW_PENDING_SOURCE_UNSTABLE'):
            build(documents, dashboard, store, payload, view)
        after = files_hashes(store)
        print('SOURCE_INVENTORY_OPEN_WRITER=' + json.dumps({'before': before, 'after': after}, sort_keys=True))
        assert after == before
    finally:
        connection.close()


def test_rollback_journal_fails_closed_without_source_changes(tmp_path, monkeypatch):
    documents, dashboard, store, _, payload, view = pending(tmp_path, monkeypatch)
    store.path.with_name(store.path.name + '-journal').write_bytes(b'synthetic-hot-journal')
    before = files_hashes(store)
    with pytest.raises(ValueError, match='FINAL_REVIEW_PENDING_SOURCE_UNSTABLE'):
        build(documents, dashboard, store, payload, view)
    after = files_hashes(store)
    print('SOURCE_INVENTORY_ROLLBACK_JOURNAL=' + json.dumps({'before': before, 'after': after}, sort_keys=True))
    assert after == before


def test_isolated_database_copy_is_removed_after_projection(tmp_path, monkeypatch):
    documents, dashboard, store, _, payload, view = pending(tmp_path, monkeypatch)
    original = pending_adapter.tempfile.TemporaryDirectory
    copies = []

    def tracked_directory(*args, **kwargs):
        created = original(*args, **kwargs)
        copies.append(Path(created.name))
        return created

    monkeypatch.setattr(pending_adapter.tempfile, 'TemporaryDirectory', tracked_directory)
    assert build(documents, dashboard, store, payload, view).as_dict()['execution_authority'] is False
    assert len(copies) == 1
    assert not copies[0].exists()


def build(documents, dashboard, store, payload, view):
    return build_pending_final_review_preview(
        documents=documents, dashboard=dashboard, store=store,
        current_payload=payload, common_view=view,
    )


def test_exact_pending_plan_projects_unapproved_without_sqlite_mutation(tmp_path, monkeypatch):
    documents, dashboard, store, _, payload, view = pending(tmp_path, monkeypatch)
    before = files_hashes(store)
    preview = build(documents, dashboard, store, payload, view)
    assert files_hashes(store) == before
    body = preview.as_dict()
    assert body['status'] == 'UNAPPROVED_PREVIEW'
    assert body['execution_authority'] is False
    assert body['external_writes_performed'] == []
    assert body['common']['plan_id'] == view['common']['plan']['plan_id']
    assert body['common']['payload'] == payload
    assert require_current_pending_final_review_preview(
        preview, documents=documents, dashboard=dashboard, store=store,
        current_payload=payload, common_view=view,
    )
    assert files_hashes(store) == before
    assert store.get_plan(body['common']['plan_id'])['status'] == 'PENDING_APPROVAL'


@pytest.mark.parametrize('drift', [
    'offer', 'revision', 'r1', 'r2_image', 'targets', 'payload',
    'plan_id', 'token', 'digest', 'view_plan', 'duplicate_identity',
])
def test_pending_identity_drift_fails_closed(tmp_path, monkeypatch, drift):
    documents, dashboard, store, _, payload, view = pending(tmp_path, monkeypatch)
    documents, dashboard, payload, view = map(deepcopy, (documents, dashboard, payload, view))
    plan = view['common']['plan']
    if drift == 'offer':
        dashboard['product']['offer_id'] = '123'
    elif drift == 'revision':
        dashboard['product']['revision'] += 1
    elif drift == 'r1':
        documents['round1_snapshot']['offer_id'] = '123'
    elif drift == 'r2_image':
        documents['generation_result']['assets'][0]['artifact_digest'] = 'sha256:' + '0' * 64
    elif drift == 'targets':
        payload['r3_stage_binding']['marketplace_targets'] = ['tiktok:OTHER']
    elif drift == 'payload':
        payload['product_facts']['title'] = 'changed'
    elif drift == 'plan_id':
        plan['plan_id'] += '-other'
    elif drift == 'token':
        plan['confirmation_token'] += '-other'
    elif drift == 'digest':
        plan['payload_digest'] = '0' * 64
    elif drift == 'view_plan':
        plan['payload']['product_facts']['title'] = 'changed'
    else:
        plan['product_id'] = '123'
    with pytest.raises(ValueError):
        build(documents, dashboard, store, payload, view)


@pytest.mark.parametrize('state', [
    'approved_status', 'approval_receipt', 'approved_at', 'run',
    'reconciliation', 'superseded', 'forged_approved_flag',
])
def test_pending_never_inherits_approval_or_execution(tmp_path, monkeypatch, state):
    documents, dashboard, store, request, payload, view = pending(tmp_path, monkeypatch)
    view = deepcopy(view)
    plan = view['common']['plan']
    if state == 'approved_status':
        plan['status'] = 'APPROVED'
    elif state == 'approval_receipt':
        plan['approval'] = {'approved_by': 'Kyle', 'user_approved': True}
    elif state == 'approved_at':
        plan['approved_at'] = '2026-09-24T00:00:00Z'
    elif state == 'run':
        view['common']['run'] = {'status': 'RUNNING'}
    elif state == 'reconciliation':
        view['common']['reconciliation_reference'] = {'plan_id': plan['plan_id']}
    elif state == 'superseded':
        plan['superseded_at'] = '2026-09-24T00:00:00Z'
    else:
        plan['approved'] = True
    with pytest.raises(ValueError):
        build(documents, dashboard, store, payload, view)


def test_stale_view_cannot_borrow_old_pending_after_durable_kyle_approval(tmp_path, monkeypatch):
    documents, dashboard, store, request, payload, view = pending(tmp_path, monkeypatch)
    plan = view['common']['plan']
    store.approve_plan(plan['plan_id'], user_approved=True, approved_by='Kyle',
                       confirmation_token=plan['confirmation_token'])
    with pytest.raises(ValueError):
        build(documents, dashboard, store, payload, view)
    status, current_view = server._preview_r3_common_stage(request)
    assert status == 200
    assert current_view['common']['status'] == 'READY_TO_SYNC'
    with pytest.raises(ValueError):
        build(documents, dashboard, store, payload, current_view)


def test_current_pending_revalidation_rejects_new_payload(tmp_path, monkeypatch):
    documents, dashboard, store, _, payload, view = pending(tmp_path, monkeypatch)
    preview = build(documents, dashboard, store, payload, view)
    changed = deepcopy(payload)
    changed['product_facts']['title'] = 'changed'
    with pytest.raises(ValueError):
        require_current_pending_final_review_preview(
            preview, documents=documents, dashboard=dashboard, store=store,
            current_payload=changed, common_view=view,
        )


def test_actual_common_producer_drift_cannot_reuse_pending_plan(tmp_path, monkeypatch):
    documents, dashboard, store, request, payload, view = pending(tmp_path, monkeypatch)
    preview = build(documents, dashboard, store, payload, view)
    dashboard['product']['source_title_zh'] = '新中文源标题'
    status, refreshed = server._preview_r3_common_stage(request)
    assert status == 200, refreshed
    current_payload = refreshed['common']['plan']['payload']
    assert current_payload['plan_id'] != payload['plan_id']
    with pytest.raises(ValueError):
        require_current_pending_final_review_preview(
            preview, documents=documents, dashboard=dashboard, store=store,
            current_payload=current_payload, common_view=view,
        )
