from __future__ import annotations

import sqlite3

import pytest

from domains import product_operations
from domains.product_operations import (
    ApprovedPublicationSnapshotError,
    PUBLICATION_BUSINESS_SNAPSHOT_SCHEMA_VERSION,
)
from shared_platform import release_store
from shared_platform.release_store import ReleaseStore
from test_approved_publication_snapshot_integration import (
    _production_dashboard_with_exact_v4_inputs,
)
from modules.products import server as product_server


def _business_plan_payload() -> dict:
    payload, blockers = product_server._release_plan_payload_from_dashboard(
        _production_dashboard_with_exact_v4_inputs()
    )
    assert blockers == []
    payload.pop("approved_publication_snapshot_schema_version", None)
    payload["publication_business_snapshot_schema_version"] = (
        PUBLICATION_BUSINESS_SNAPSHOT_SCHEMA_VERSION
    )
    return payload


def _stored_bytes(path) -> tuple[str, str]:
    with sqlite3.connect(path) as connection:
        return connection.execute(
            """
            SELECT snapshot_json, snapshot_digest
            FROM publication_business_snapshots
            """
        ).fetchone()


def test_create_persists_business_snapshot_and_approval_keeps_exact_bytes(
    tmp_path, monkeypatch
):
    store = ReleaseStore(tmp_path / "release.db")
    payload = _business_plan_payload()
    plan = store.create_plan(payload)
    before = store.publication_business_snapshot(
        offer_id=payload["product_id"],
        plan_id=plan["plan_id"],
    )
    before_bytes = _stored_bytes(store.path)
    assert before["schema_version"] == PUBLICATION_BUSINESS_SNAPSHOT_SCHEMA_VERSION
    assert "approved_at" not in before
    assert "approved_by" not in before

    def rebuild_forbidden(_payload):
        raise AssertionError("approval must not rebuild the business snapshot")

    monkeypatch.setattr(
        product_operations,
        "build_publication_business_snapshot",
        rebuild_forbidden,
    )
    approval = store.approve_plan(
        plan["plan_id"],
        approved_by="Kyle",
        user_approved=True,
        confirmation_token=plan["confirmation_token"],
    )
    after = store.publication_business_snapshot(
        offer_id=payload["product_id"],
        business_snapshot_digest=before["business_snapshot_digest"],
    )
    assert approval["publication_business_snapshot"] == before
    assert after == before
    assert _stored_bytes(store.path) == before_bytes


def test_create_business_snapshot_is_atomic_and_idempotent(tmp_path, monkeypatch):
    store = ReleaseStore(tmp_path / "release.db")
    payload = _business_plan_payload()

    def fail(_payload):
        raise ApprovedPublicationSnapshotError("fault injection")

    monkeypatch.setattr(
        product_operations,
        "build_publication_business_snapshot",
        fail,
    )
    with pytest.raises(ApprovedPublicationSnapshotError, match="fault injection"):
        store.create_plan(payload)
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM release_plans").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM publication_business_snapshots"
        ).fetchone()[0] == 0

    monkeypatch.undo()
    first = store.create_plan(payload)
    first_snapshot = store.publication_business_snapshot(
        offer_id=payload["product_id"], plan_id=first["plan_id"]
    )
    repeated = ReleaseStore(store.path).create_plan(payload)
    repeated_snapshot = ReleaseStore(store.path).publication_business_snapshot(
        offer_id=payload["product_id"], plan_id=first["plan_id"]
    )
    assert first["created"] is True
    assert repeated["created"] is False
    assert repeated_snapshot == first_snapshot
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM publication_business_snapshots"
        ).fetchone()[0] == 1


def test_approval_validation_crash_rolls_back_authority_and_retry_reuses_snapshot(
    tmp_path, monkeypatch
):
    store = ReleaseStore(tmp_path / "release.db")
    payload = _business_plan_payload()
    plan = store.create_plan(payload)
    before = _stored_bytes(store.path)
    original = release_store._validated_business_snapshot_row

    def crash(*_args, **_kwargs):
        raise RuntimeError("simulated approval crash")

    monkeypatch.setattr(release_store, "_validated_business_snapshot_row", crash)
    with pytest.raises(RuntimeError, match="simulated approval crash"):
        store.approve_plan(
            plan["plan_id"],
            approved_by="Kyle",
            user_approved=True,
            confirmation_token=plan["confirmation_token"],
        )
    assert store.get_plan(plan["plan_id"])["status"] == "PENDING_APPROVAL"
    assert store.get_plan(plan["plan_id"])["approval"] is None
    assert _stored_bytes(store.path) == before

    monkeypatch.setattr(
        release_store,
        "_validated_business_snapshot_row",
        original,
    )
    approval = store.approve_plan(
        plan["plan_id"],
        approved_by="Kyle",
        user_approved=True,
        confirmation_token=plan["confirmation_token"],
    )
    assert approval["created"] is True
    assert _stored_bytes(store.path) == before
