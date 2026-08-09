from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import sqlite3

import pytest

from domains import product_operations
from domains.product_operations import ApprovedPublicationSnapshotError
from modules.products import server as product_server
from shared_platform import release_control, release_store
from shared_platform.release_store import (
    ImmutableReleaseError,
    ReleaseStore,
)
from test_product_release_v1 import _dashboard


def _sha(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _category_decision(
    label: str,
    *,
    category_id: str | None,
    category_name: str | None,
) -> dict:
    platform, site = label.split(":", 1)
    if category_id is None:
        category = None
        status = "NOT_APPLICABLE"
    else:
        category = {
            "id": category_id,
            "name": category_name,
            "path": [
                {"id": "root", "name": "Root"},
                {"id": category_id, "name": category_name},
            ],
        }
        status = "APPROVED"
    return {
        "target_label": label,
        "platform": platform,
        "site": site,
        "store": site,
        "category": category,
        "decision": {
            "status": status,
            "decision_digest": _sha("category:" + label),
        },
    }


def _production_dashboard_with_exact_v4_inputs() -> dict:
    dashboard = deepcopy(_dashboard())
    dashboard["product"].update(
        {
            "category": {"id": "wall-stickers", "name": "贴饰 > 墙贴"},
            "selected_sku_keys": ["default"],
            "sku_commercial_facts": {
                "default": {
                    "cost_cny": "8.1",
                    "weight_kg": "0.2",
                    "package_cm": ["34", "58", "1"],
                }
            },
            "source_skus": [
                {
                    "key": "default",
                    "label": "34 x 58 cm",
                    "price_cny": "12",
                    "model_sku": "0952",
                    "commercial_facts": {
                        "cost_cny": "8.1",
                        "weight_kg": "0.2",
                        "package_cm": ["34", "58", "1"],
                    },
                }
            ],
        }
    )
    dashboard["pricing_review"]["target_pricing"] = {
        "miaoshou:COMMON": {
            "status": "ready",
            "sku_prices": [
                {"model_sku": "0952", "list_price": "39", "currency": "CNY"}
            ],
        },
        "tiktok:MX": {
            "status": "ready",
            "sku_prices": [
                {"model_sku": "0952", "list_price": "129", "currency": "MXN"}
            ],
        },
    }
    initial, blockers = product_server._release_plan_payload_from_dashboard(
        dashboard
    )
    assert blockers == []
    source_digest = initial["source_product_identity"]["identity_digest"]
    lineage_digest = initial["sku_lineage"]["reservation"][
        "reservation_digest"
    ]
    dashboard["_approved_publication_snapshot_inputs"] = {
        "description": "Approved removable PVC wall sticker for indoor decor.",
        "categories_by_target": {
            "miaoshou:COMMON": _category_decision(
                "miaoshou:COMMON",
                category_id=None,
                category_name=None,
            ),
            "tiktok:MX": _category_decision(
                "tiktok:MX",
                category_id="600009",
                category_name="Festive Decorations",
            ),
        },
        "sku_details_by_key": {
            "default": {
                "specification": {"size": "34 x 58 cm"},
                "image_urls": ["https://assets.example/main.jpg"],
            }
        },
        "digests": {
            "source": source_digest,
            "content": _sha("content:approved"),
            "policy": _sha("policy:v4"),
            "category": _sha("category:target-exact"),
            "pricing": _sha("pricing:approved"),
            "sku_lineage": lineage_digest,
        },
    }
    return dashboard


def _approved_full_store(tmp_path, monkeypatch):
    store = ReleaseStore(tmp_path / "release.db")
    dashboard = _production_dashboard_with_exact_v4_inputs()
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: dashboard,
    )
    payload, blockers = product_server._release_plan_payload_from_dashboard(
        dashboard
    )
    assert blockers == []
    assert payload["approved_publication_snapshot_schema_version"] == (
        "approved-publication-snapshot/v4"
    )
    preview = store.preview_plan(payload)
    status, response = product_server._approve_release_plan_locally(
        {
            "offer_id": payload["product_id"],
            "seller_sku": payload["seller_sku"],
            "publication_targets": list(payload["targets"]),
            "plan_id": preview["plan_id"],
            "confirmation_token": preview["confirmation_token"],
            "approved_by": "Kyle",
            "user_approved": True,
        }
    )
    assert status == 200, response
    return store, payload, response


def test_current_production_projection_reports_exact_missing_owned_facts(
    tmp_path, monkeypatch
):
    """Red evidence became an explicit legacy/unavailable diagnostic."""

    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    payload, blockers = product_server._release_plan_payload_from_dashboard(
        _dashboard()
    )
    assert blockers == []
    projection = product_server._publication_snapshot_plan_projection(payload)
    assert projection.ready is False
    assert "product_facts.description" in projection.missing_fields
    assert "product_facts.category.id+name" in projection.missing_fields
    assert "product_facts.categories_by_target" in projection.missing_fields
    assert "digests" in projection.missing_fields
    assert all(
        not field.startswith("product_facts.category.provider_fallback")
        for field in projection.missing_fields
    )

    plan = store.create_plan(payload)
    store.approve_plan(
        plan["plan_id"],
        approved_by="Kyle",
        user_approved=True,
        confirmation_token=plan["confirmation_token"],
    )
    assert store.approved_publication_snapshot(
        offer_id=payload["product_id"], plan_id=plan["plan_id"]
    ) is None
    summary = store.publication_snapshot_projection(
        offer_id=payload["product_id"], plan_id=plan["plan_id"]
    )
    assert summary["status"] == "SNAPSHOT_UNAVAILABLE"
    assert summary["reason_code"] == "legacy_approval_without_v4_snapshot"


def test_real_server_approval_path_atomically_freezes_and_reopens_v4(
    tmp_path, monkeypatch
):
    store, payload, response = _approved_full_store(tmp_path, monkeypatch)
    identity = response["approval"]["publication_snapshot"]
    assert identity["schema_version"] == "approved-publication-snapshot/v4"

    first = store.approved_publication_snapshot(
        offer_id=payload["product_id"], plan_id=payload["plan_id"]
    )
    reopened = ReleaseStore(store.path).approved_publication_snapshot(
        offer_id=payload["product_id"],
        snapshot_digest=identity["snapshot_digest"],
    )
    assert reopened == first
    assert first["snapshot_digest"] == identity["snapshot_digest"]
    assert first["product"]["main_category"]["id"] == "wall-stickers"
    assert first["categories_by_target"]["tiktok:MX"]["category"]["id"] == "600009"
    assert first["categories_by_target"]["miaoshou:COMMON"]["category"] is None
    assert first["skus"][0]["cost"] == {"amount": "8.1", "currency": "CNY"}

    repeated = store.approve_plan(
        payload["plan_id"],
        approved_by="Kyle",
        user_approved=True,
        confirmation_token=store.get_plan(payload["plan_id"])[
            "confirmation_token"
        ],
    )
    assert repeated["created"] is False
    assert repeated["publication_snapshot"] == identity
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM approved_publication_snapshots"
        ).fetchone()[0] == 1


def test_approved_upstream_drift_does_not_change_frozen_snapshot(
    tmp_path, monkeypatch
):
    store, payload, _response = _approved_full_store(tmp_path, monkeypatch)
    before = store.approved_publication_snapshot(
        offer_id=payload["product_id"], plan_id=payload["plan_id"]
    )
    changed = _production_dashboard_with_exact_v4_inputs()
    changed["_approved_publication_snapshot_inputs"]["description"] = (
        "A later unapproved description"
    )
    changed_payload, _ = product_server._release_plan_payload_from_dashboard(
        changed
    )
    assert changed_payload != payload
    after = ReleaseStore(store.path).approved_publication_snapshot(
        offer_id=payload["product_id"], plan_id=payload["plan_id"]
    )
    assert after == before


def test_snapshot_build_failure_rolls_back_approval_and_plan_status(
    tmp_path, monkeypatch
):
    dashboard = _production_dashboard_with_exact_v4_inputs()
    payload, blockers = product_server._release_plan_payload_from_dashboard(
        dashboard
    )
    assert blockers == []
    store = ReleaseStore(tmp_path / "release.db")
    plan = store.create_plan(payload)

    def fail(_plan):
        raise ApprovedPublicationSnapshotError("fault injection")

    monkeypatch.setattr(
        product_operations,
        "build_approved_publication_snapshot",
        fail,
    )
    with pytest.raises(ApprovedPublicationSnapshotError, match="fault injection"):
        store.approve_plan(
            plan["plan_id"],
            approved_by="Kyle",
            user_approved=True,
            confirmation_token=plan["confirmation_token"],
        )
    restored = store.get_plan(plan["plan_id"])
    assert restored["status"] == "PENDING_APPROVAL"
    assert restored["approval"] is None
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM approved_publication_snapshots"
        ).fetchone()[0] == 0


def test_main_category_never_backfills_target_provider_category():
    payload, blockers = product_server._release_plan_payload_from_dashboard(
        _dashboard()
    )
    assert blockers == []
    payload["product_facts"]["category"] = {
        "id": "main-taxonomy-only",
        "name": "Product taxonomy",
    }
    projection = product_server._publication_snapshot_plan_projection(payload)
    assert projection.ready is False
    assert "product_facts.categories_by_target" in projection.missing_fields
    assert "categories_by_target" not in projection.payload["product_facts"]


def test_tampered_snapshot_fails_internal_and_public_read(
    tmp_path, monkeypatch
):
    store, payload, _response = _approved_full_store(tmp_path, monkeypatch)
    with sqlite3.connect(store.path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE approved_publication_snapshots SET snapshot_json = '{}'"
            )
        connection.rollback()
        connection.execute(
            "DROP TRIGGER trg_approved_publication_snapshot_immutable"
        )
        raw = connection.execute(
            "SELECT snapshot_json FROM approved_publication_snapshots"
        ).fetchone()[0]
        document = json.loads(raw)
        document["product"]["title"] = "tampered"
        connection.execute(
            "UPDATE approved_publication_snapshots SET snapshot_json = ?",
            (json.dumps(document, sort_keys=True, separators=(",", ":")),),
        )
        connection.commit()
    with pytest.raises(ImmutableReleaseError):
        store.approved_publication_snapshot(
            offer_id=payload["product_id"], plan_id=payload["plan_id"]
        )
    with pytest.raises(ImmutableReleaseError):
        store.approve_plan(
            payload["plan_id"],
            approved_by="Kyle",
            user_approved=True,
            confirmation_token=store.get_plan(payload["plan_id"])[
                "confirmation_token"
            ],
        )


def test_declared_v4_with_missing_facts_fails_approval_atomically(tmp_path):
    payload, blockers = product_server._release_plan_payload_from_dashboard(
        _dashboard()
    )
    assert blockers == []
    payload["approved_publication_snapshot_schema_version"] = (
        "approved-publication-snapshot/v4"
    )
    store = ReleaseStore(tmp_path / "release.db")
    plan = store.create_plan(payload)

    with pytest.raises(ApprovedPublicationSnapshotError):
        store.approve_plan(
            plan["plan_id"],
            approved_by="Kyle",
            user_approved=True,
            confirmation_token=plan["confirmation_token"],
        )

    assert store.get_plan(plan["plan_id"])["status"] == "PENDING_APPROVAL"
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM release_approvals"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM approved_publication_snapshots"
        ).fetchone()[0] == 0
