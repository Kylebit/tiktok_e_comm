from __future__ import annotations

import json
from datetime import date
from pathlib import Path
import sqlite3

import pytest

from shared_platform.release_control import (
    build_weekly_profit_rehearsal,
    build_release_dashboard,
    latest_weekly_profit_summary,
    summarize_weekly_profit_payload,
)
from shared_platform.report_store import ReportRunStore


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _release_fixture(tmp_path: Path) -> tuple[Path, Path]:
    offer_id = "3828811808"
    source_url = "https://example.com/source.jpg"
    generated_url = "https://example.com/generated.png"
    state = {
        "offer_id": offer_id,
        "_revision": 7,
        "updated_at": "2026-07-25T10:00:00+08:00",
        "review": {
            "title": "Dog Wall Decal",
            "seller_sku": "",
            "fields_locked": False,
            "cost_cny": 4.4,
            "weight_kg": 0.02,
            "package_cm": [58, 34, 0.02],
            "selected_sites": ["lh_th"],
            "selected_sku_keys": ["size-large"],
            "image_actions": [{"url": source_url, "action": "keep"}],
            "image_order": [source_url, generated_url],
        },
        "content_package": {
            "collect_box_id": offer_id,
            "fact_card_approved": True,
            "planning_scope_approved": True,
            "suite_approved": True,
            "storyboard_reviews": {"sc1": {"decision": "approved"}},
            "current_artifact_ids": {"sc1": "sc1_r1"},
            "asset_decisions": {"sc1_r1": {"decision": "approved"}},
        },
    }
    _write_json(
        tmp_path / "data" / "new_product_workbench" / f"{offer_id}.json",
        state,
    )
    package_dir = tmp_path / "outputs" / "image_suite_from_miaoshou" / offer_id
    _write_json(
        package_dir / "review_package.json",
        {
            "collect_box": {
                "detail_id": int(offer_id),
                "source_item_id": "1688-1",
                "source_title": "Dog Wall Decal",
            },
            "plan": {
                "suite": {
                    "items": [{"id": "sc1", "type": "scene", "selected": True}]
                }
            },
        },
    )
    _write_json(
        package_dir / "generation_audit_sc1_r1.json",
        {
            "shot_id": "sc1",
            "download_verified": True,
            "created_at": "2026-07-25T09:00:00+08:00",
            "final_response": {"result": {"data": [{"url": generated_url}]}},
        },
    )
    generated = package_dir / "generated" / "sc1_r1.png"
    generated.parent.mkdir(parents=True, exist_ok=True)
    generated.write_bytes(b"verified")

    database = tmp_path / "data" / "shop.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE products (seller_sku TEXT)")
        connection.execute("CREATE TABLE shopee_products (seller_sku TEXT)")
        connection.execute("INSERT INTO products VALUES ('0021')")
    return tmp_path, database


def test_release_dashboard_is_a_complete_no_write_rehearsal(tmp_path):
    root, database = _release_fixture(tmp_path)
    before = {
        path.relative_to(root): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }

    result = build_release_dashboard(
        root=root,
        database_path=database,
        report_store_path=root / "data" / "missing-orbit.db",
        offer_id="3828811808",
        seller_sku="0946",
    )

    after = {
        path.relative_to(root): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }
    assert before == after
    assert result["safety"]["external_writes_performed"] == []
    assert result["safety"]["publish_enabled"] is False
    assert result["content"]["approved"] is True
    assert [row["position"] for row in result["content"]["images"]] == [1, 2]
    assert result["approval_rehearsal"]["ready"] is True
    assert result["approval_rehearsal"]["persisted"] is False
    assert result["publication_rehearsal"]["ready"] is True
    assert all(row["status"] == "draft" for row in result["publication_rehearsal"]["drafts"])
    assert result["actual_release_gate"]["ready"] is False
    assert "Product approval has not been persisted." in result["actual_release_gate"]["blockers"]
    omnichannel = result["omnichannel_preview"]
    assert omnichannel["available"] is True
    assert omnichannel["all_preflights_passed"] is False
    assert omnichannel["adapter_calls_performed"] is False
    assert omnichannel["confirmation_token_summary"]["masked"].startswith("PUBLISH-")
    target_status = {
        (row["channel"], row["site"]): (
            row["repository_adapter_audited"],
            row["executable"],
        )
        for row in omnichannel["targets"]
    }
    assert target_status == {
        ("miaoshou", "COMMON"): (True, True),
        ("tiktok", "TH"): (False, False),
        ("shopee", "TH"): (True, True),
        ("ozon", "RU"): (True, True),
    }
    shopee = next(
        row for row in omnichannel["targets"] if row["channel"] == "shopee"
    )
    assert shopee["depends_on"] == ["tiktok:MASTER:verified_readback"]


def test_release_dashboard_normalises_sea_sites_into_shared_channel_matrix(tmp_path):
    root, database = _release_fixture(tmp_path)
    state_path = root / "data" / "new_product_workbench" / "3828811808.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["review"]["selected_sites"] = ["lh_ph", "lh_my", "lh_th", "lh_vn"]
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = build_release_dashboard(
        root=root,
        database_path=database,
        report_store_path=root / "data" / "missing-orbit.db",
    )

    preview = result["omnichannel_preview"]
    assert preview["site_selection"] == {
        "miaoshou": ["COMMON"],
        "tiktok": ["MY", "PH", "TH", "VN"],
        "shopee": ["MY", "PH", "TH", "VN"],
        "ozon": ["RU"],
    }
    status = {
        (row["channel"], row["site"]): (
            row["repository_adapter_audited"],
            row["executable"],
        )
        for row in preview["targets"]
    }
    assert all(status[("tiktok", site)] == (False, False) for site in ("MY", "PH", "TH", "VN"))
    assert all(status[("shopee", site)] == (True, True) for site in ("MY", "PH", "TH", "VN"))
    assert status[("ozon", "RU")] == (True, True)
    assert status[("miaoshou", "COMMON")] == (True, True)
    assert preview["all_preflights_passed"] is False
    assert preview["ready"] is False


def test_release_dashboard_blocks_conflicting_candidate_sku(tmp_path):
    root, database = _release_fixture(tmp_path)

    result = build_release_dashboard(
        root=root,
        database_path=database,
        report_store_path=root / "data" / "missing-orbit.db",
        seller_sku="0021",
    )

    assert result["approval_rehearsal"]["ready"] is False
    assert "seller_sku is already present in the catalog" in result["approval_rehearsal"]["blockers"]
    assert result["publication_rehearsal"]["drafts"] == []
    assert result["omnichannel_preview"]["available"] is False
    assert result["omnichannel_preview"]["targets"] == []
    assert (
        "seller_sku is already present in the catalog"
        in result["omnichannel_preview"]["blockers"]
    )


def test_release_dashboard_treats_other_approved_workbench_as_sku_reservation(
    tmp_path,
):
    root, database = _release_fixture(tmp_path)
    _write_json(
        root / "data" / "new_product_workbench" / "9999999999.json",
        {
            "offer_id": "9999999999",
            "product_approval": {
                "status": "approved",
                "subject_type": "product",
                "subject_id": "9999999999",
                "seller_sku": "990946",
            },
        },
    )

    result = build_release_dashboard(
        root=root,
        database_path=database,
        report_store_path=root / "data" / "missing-orbit.db",
        offer_id="3828811808",
        seller_sku="0946",
    )

    assert result["approval_rehearsal"]["ready"] is False
    assert (
        "seller_sku is already present in the catalog"
        in result["approval_rehearsal"]["blockers"]
    )


def test_release_dashboard_ignores_unapproved_and_self_reservations(tmp_path):
    root, database = _release_fixture(tmp_path)
    state_path = root / "data" / "new_product_workbench" / "3828811808.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["product_approval"] = {
        "status": "approved",
        "subject_type": "product",
        "subject_id": "3828811808",
        "seller_sku": "0946",
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    _write_json(
        root / "data" / "new_product_workbench" / "7777777777.json",
        {
            "offer_id": "7777777777",
            "product_approval": {
                "status": "pending",
                "subject_type": "product",
                "subject_id": "7777777777",
                "seller_sku": "990946",
            },
        },
    )

    result = build_release_dashboard(
        root=root,
        database_path=database,
        report_store_path=root / "data" / "missing-orbit.db",
        offer_id="3828811808",
        seller_sku="0946",
    )

    assert result["approval_rehearsal"]["ready"] is True


def test_release_dashboard_rejects_unlinked_collect_box_identity(tmp_path):
    root, database = _release_fixture(tmp_path)
    state_path = root / "data" / "new_product_workbench" / "3828811808.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["content_package"]["collect_box_id"] = "9999"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(ValueError, match="not explicitly linked"):
        build_release_dashboard(
            root=root,
            database_path=database,
            report_store_path=root / "data" / "missing-orbit.db",
        )


def test_real_gate_requires_matching_approval_and_verified_current_image_order(tmp_path):
    root, database = _release_fixture(tmp_path)
    state_path = root / "data" / "new_product_workbench" / "3828811808.json"
    initial = build_release_dashboard(
        root=root,
        database_path=database,
        report_store_path=root / "data" / "missing-orbit.db",
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["review"]["fields_locked"] = True
    state["review"]["seller_sku"] = "0946"
    state["product_approval"] = dict(
        initial["approval_rehearsal"]["state_patch_preview"]["product_approval"]
    )
    state_path.write_text(json.dumps(state), encoding="utf-8")

    missing_write = build_release_dashboard(
        root=root,
        database_path=database,
        report_store_path=root / "data" / "missing-orbit.db",
    )
    assert missing_write["actual_release_gate"]["ready"] is False
    assert (
        "The current final image set has not been verified as written to Miaoshou."
        in missing_write["actual_release_gate"]["blockers"]
    )

    current_urls = [row["image_url"] for row in missing_write["content"]["images"]]
    state["content_package"]["miaoshou_ordered_images_write"] = {
        "status": "verified",
        "ordered_image_urls": list(reversed(current_urls)),
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    wrong_order = build_release_dashboard(
        root=root,
        database_path=database,
        report_store_path=root / "data" / "missing-orbit.db",
    )
    assert wrong_order["actual_release_gate"]["ready"] is False
    assert "The previous 11-image Miaoshou write is stale." in wrong_order["actual_release_gate"]["blockers"]

    state["content_package"]["miaoshou_ordered_images_write"]["ordered_image_urls"] = current_urls
    state_path.write_text(json.dumps(state), encoding="utf-8")
    ready = build_release_dashboard(
        root=root,
        database_path=database,
        report_store_path=root / "data" / "missing-orbit.db",
    )
    assert ready["content"]["current_image_write_verified"] is True
    assert ready["actual_release_gate"] == {"ready": True, "blockers": []}

    state["review"]["cost_cny"] = 9.9
    state_path.write_text(json.dumps(state), encoding="utf-8")
    commercial_drift = build_release_dashboard(
        root=root,
        database_path=database,
        report_store_path=root / "data" / "missing-orbit.db",
    )
    assert commercial_drift["actual_release_gate"]["ready"] is False
    assert "does not match" in commercial_drift["actual_release_gate"]["blockers"][0]

    state["product_approval"]["seller_sku"] = "9999"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    wrong_approval = build_release_dashboard(
        root=root,
        database_path=database,
        report_store_path=root / "data" / "missing-orbit.db",
    )
    assert wrong_approval["actual_release_gate"]["ready"] is False
    assert "does not match" in wrong_approval["actual_release_gate"]["blockers"][0]


def test_conflicting_sku_can_never_bypass_real_gate_with_empty_fingerprint(tmp_path):
    root, database = _release_fixture(tmp_path)
    state_path = root / "data" / "new_product_workbench" / "3828811808.json"
    baseline = build_release_dashboard(
        root=root,
        database_path=database,
        report_store_path=root / "data" / "missing-orbit.db",
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["review"].update({"fields_locked": True, "seller_sku": "0021"})
    approval = dict(
        baseline["approval_rehearsal"]["state_patch_preview"]["product_approval"]
    )
    approval.update({"seller_sku": "0021", "input_fingerprint": ""})
    state["product_approval"] = approval
    state["content_package"]["miaoshou_ordered_images_write"] = {
        "status": "verified",
        "ordered_image_urls": [
            row["image_url"] for row in baseline["content"]["images"]
        ],
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = build_release_dashboard(
        root=root,
        database_path=database,
        report_store_path=root / "data" / "missing-orbit.db",
        seller_sku="0021",
    )

    assert result["approval_rehearsal"]["ready"] is False
    assert result["actual_release_gate"]["ready"] is False


def test_weekly_summary_keeps_realized_and_quality_evidence_separate():
    summary = summarize_weekly_profit_payload(
        {
            "run_id": "weekly-1",
            "status": "needs_review",
            "period": {"start": "2026-07-13", "end": "2026-07-19"},
            "realized_by_sku": [
                {
                    "settlement_cny": "20.5",
                    "cost_cny": "8",
                    "ad_cost_cny": "0",
                    "profit_cny": "12.5",
                },
                {
                    "settlement_cny": "-2",
                    "cost_cny": "1",
                    "ad_cost_cny": "0",
                    "profit_cny": "-3",
                },
            ],
            "estimate_by_sku": [{"profit_cny": "999"}],
            "negative_profit_skus": [{"sku_id": "0002", "profit_cny": "-3"}],
            "quality_issues": [
                {"code": "upstream:missing_ad_spend"},
                {"code": "upstream:missing_quantity"},
            ],
            "input_snapshot": {
                "snapshot_id": "snapshot-1",
                "source_metadata": {
                    "source_files": [{"name": "one.csv"}],
                    "adapter_row_counts": {"raw": 5, "normalized": 2, "rejected": 3},
                    "adapter_issue_counts": {"missing_quantity": 684},
                },
            },
        }
    )

    assert summary["totals"]["profit_cny"] == "9.5"
    assert summary["realized_bucket_count"] == 2
    assert summary["estimate_bucket_count"] == 1
    assert summary["preliminary"] is True
    assert summary["quality_issue_group_counts"]["upstream:missing_quantity"] == 1
    assert summary["quality_affected_row_counts"]["upstream:missing_quantity"] == 684
    assert summary["decision_usable"] is False


def test_latest_weekly_summary_ignores_newer_non_weekly_report(tmp_path):
    store = ReportRunStore(tmp_path / "orbit.db")

    def payload(run_id, kind):
        return {
            "run_id": run_id,
            "idempotency_key": f"key:{run_id}",
            "calculation_kind": kind,
            "status": "ready",
            "period": {"start": "2026-07-13", "end": "2026-07-19"},
            "realized_by_sku": [],
            "quality_issues": [],
        }

    store.store_report_run(payload("weekly-1", "weekly_profit_digest"), add_to_inbox=False)
    store.store_report_run(payload("monthly-1", "monthly_profit_close"), add_to_inbox=False)

    assert latest_weekly_profit_summary(store.path)["run_id"] == "weekly-1"


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (date(2026, 7, 13), date(2026, 7, 20)),
        (date(2026, 7, 14), date(2026, 7, 20)),
        (date(2026, 7, 13), date(2026, 7, 18)),
    ],
)
def test_weekly_rehearsal_rejects_non_complete_week_before_reading_sources(
    start, end, tmp_path
):
    with pytest.raises(ValueError, match="Monday-through-Sunday"):
        build_weekly_profit_rehearsal(
            period_start=start,
            period_end=end,
            root=tmp_path,
        )
