from __future__ import annotations

from pathlib import Path

from shared_platform.publication_image_plan import build_generic_image_plan
from shared_platform.publication_rounds import (
    build_round1_snapshot,
    canonical_targets_to_workbench_sites,
)
from shared_platform.publication_stock_policy import default_publication_stock_policy


def test_generic_image_plan_keeps_livelyhive_and_homebloom_routes_separate() -> None:
    plan = build_generic_image_plan(("tiktok:LH_PH", "tiktok:HB_MY", "shopee:PH"))

    assert plan["schema_version"] == "first-review-image-plan/v1"
    assert [row["id"] for row in plan["brand_plans"]] == [
        "livelyhive-sea",
        "homebloom-sea",
    ]
    assert plan["summary"]["net_new_output_count"] == 12


def test_default_stock_policy_is_a_reviewable_shared_contract() -> None:
    assert default_publication_stock_policy() == {
        "schema_version": "publication-default-stock/v1",
        "quantity_per_sku": 200,
        "scope": "EACH_SELECTED_SKU",
        "source": "SYSTEM_GOVERNED_DEFAULT",
        "review_round": "ROUND1",
    }


def test_supervised_round1_snapshot_stays_local_and_digest_bound(tmp_path: Path) -> None:
    review = {
        "schema": "publication-preparation-decision/v1",
        "status": "FIRST_REVIEW_READY",
        "offer_id": "123456",
        "product_center_revision": 7,
        "target_selection": {"requested": ["tiktok:LH_PH", "ozon:RU"]},
        "product_facts": {"seller_sku": "0980"},
        "shared_review_facts": {"status": "COMPLETE"},
        "targets": [],
        "platform_categories": [],
        "copy_review_sets": [],
        "content_groups": {},
        "publication_stock_policy": default_publication_stock_policy(),
        "image_execution_plan": build_generic_image_plan(("tiktok:LH_PH", "ozon:RU")),
    }
    state = {
        "_revision": 9,
        "review": {"selected_sites": ["lh_ph"]},
        "product_approval": {
            "status": "approved",
            "approved_by": "Kyle",
            "approval_id": "approval-123",
            "input_fingerprint": "sha256:facts",
        },
    }

    snapshot = build_round1_snapshot(
        first_review=review,
        state=state,
        approved_by="Kyle",
        approved_at="2026-09-05T00:00:00+00:00",
        report_directory=tmp_path,
    )

    assert canonical_targets_to_workbench_sites(snapshot["canonical_targets"]) == ["lh_ph"]
    assert snapshot["publication_stock_policy"]["quantity_per_sku"] == 200
    assert snapshot["snapshot_digest"].startswith("sha256:")
    assert snapshot["external_write_count"] == 0
