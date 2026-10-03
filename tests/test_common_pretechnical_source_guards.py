"""Real persisted COMMON origin identity may not be repaired by a caller."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager

import pytest

from modules.products import server
from shared_platform.r3_common_source_facts import NativeCommonSourceReader
from shared_platform.r3_frozen_review_producer import DomainReviewBlocked, _bytes
from test_b4b_common_stage import context
from test_r3_common_source_facts import connect, rows


def _origin(tmp_path, monkeypatch):
    documents, dashboard, store, request = context(tmp_path, monkeypatch)
    code, view = server._preview_r3_common_stage(request)
    assert code == 200, view
    return store, store.create_plan(view['common']['plan']['payload'])


@contextmanager
def _corrupt_private_origin(store, tmp_path):
    """Model damaged retained bytes; restore the real immutable SQL guard.

    A valid ReleaseStore prevents application UPDATEs. These tests need to
    exercise the reader against a damaged/restored historical record instead.
    The only affected database is the declared pytest fixture directory.
    """
    assert store.path.resolve(strict=True).is_relative_to(tmp_path.resolve(strict=True))
    with sqlite3.connect(store.path) as db:
        trigger = db.execute('SELECT tbl_name,sql FROM sqlite_master WHERE type=? AND name=?',
                             ('trigger', 'trg_release_plan_immutable')).fetchone()
        assert trigger is not None and trigger[0] == 'release_plans'
        assert 'release plan payload is immutable' in trigger[1]
        db.execute('DROP TRIGGER trg_release_plan_immutable')
        try:
            yield db
        finally:
            db.execute(trigger[1])
            assert db.execute('SELECT tbl_name,sql FROM sqlite_master WHERE type=? AND name=?',
                              ('trigger', 'trg_release_plan_immutable')).fetchone() == trigger


def test_common_origin_bytes_cannot_be_reencoded_without_digest(tmp_path, monkeypatch):
    store, plan = _origin(tmp_path, monkeypatch)
    with _corrupt_private_origin(store, tmp_path) as db:
        db.execute('UPDATE release_plans SET payload_json=payload_json || ? WHERE plan_id=?',
                   ('\n', plan['plan_id']))
    with connect(store) as db:
        before = rows(db)
        with pytest.raises(DomainReviewBlocked, match='COMMON_SOURCE_ORIGIN_BYTES_CHANGED'):
            NativeCommonSourceReader(store).read_source_facts(db, plan['plan_id'])
        assert rows(db) == before


def test_common_origin_valid_json_digest_cannot_replace_producer_identity(tmp_path, monkeypatch):
    store, plan = _origin(tmp_path, monkeypatch)
    payload = json.loads(json.dumps(plan['payload']))
    payload['r3_stage_binding']['r2_identity']['offer_id'] = '42'
    raw = _bytes(payload)
    with _corrupt_private_origin(store, tmp_path) as db:
        db.execute('UPDATE release_plans SET payload_json=?,payload_digest=? WHERE plan_id=?',
                   (raw.decode(), hashlib.sha256(raw).hexdigest(), plan['plan_id']))
    with connect(store) as db:
        before = rows(db)
        with pytest.raises(DomainReviewBlocked, match='COMMON_SOURCE_ORIGIN_IDENTITY_INVALID'):
            NativeCommonSourceReader(store).read_source_facts(db, plan['plan_id'])
        assert rows(db) == before


def test_common_origin_target_membership_cannot_be_adopted_as_marketplace_graph(tmp_path, monkeypatch):
    store, plan = _origin(tmp_path, monkeypatch)
    with _corrupt_private_origin(store, tmp_path) as db:
        db.execute('UPDATE release_plans SET target_labels_json=? WHERE plan_id=?',
                   (json.dumps(['miaoshou:COMMON', 'tiktok:LH_PH']), plan['plan_id']))
    with connect(store) as db:
        before = rows(db)
        with pytest.raises(DomainReviewBlocked, match='COMMON_SOURCE_ORIGIN_IDENTITY_INVALID'):
            NativeCommonSourceReader(store).read_source_facts(db, plan['plan_id'])
        assert rows(db) == before
