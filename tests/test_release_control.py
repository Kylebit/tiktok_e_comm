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
    pricing = result["pricing_review"]
    assert pricing["algorithm"]["legacy_api"] == "/api/new-product/preview"
    assert pricing["algorithm"]["legacy_ui"] == "/new-product#renderPricing"
    assert pricing["input"] == {
        "cost_cny": 4.4,
        "weight_kg": 0.02,
        "package_cm": [58.0, 34.0, 0.02],
        "volumetric_kg": 0.0049,
        "billable_kg": 0.02,
    }
    assert pricing["selected_store_prices"][0]["target_key"] == "lh_th"
    assert pricing["target_pricing"]["tiktok:LH_TH"]["store_prices"][0][
        "list_price"
    ] > 0
    assert pricing["target_pricing"]["shopee:TH"]["depends_on"] == (
        "tiktok:MASTER:verified_readback"
    )
    assert pricing["target_pricing"]["ozon:RU"]["write_fields"] == [
        "draft.price",
        "draft.old_price",
    ]
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
        ("tiktok", "LH_TH"): (False, False),
        ("shopee", "TH"): (True, True),
        ("ozon", "RU"): (True, True),
    }
    shopee = next(
        row for row in omnichannel["targets"] if row["channel"] == "shopee"
    )
    assert shopee["depends_on"] == ["tiktok:LH_TH:verified_readback"]
    assert shopee["pricing"]["source"]["target_key"] == "lh_th"


def test_formal_candidate_is_generated_from_catalog_and_all_reservations(tmp_path):
    root, database = _release_fixture(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO products VALUES ('0945')")
        connection.execute("INSERT INTO shopee_products VALUES ('0946')")
    _write_json(
        root / "data" / "new_product_workbench" / "4000000001.json",
        {
            "offer_id": "4000000001",
            "review": {
                "seller_sku": "0947",
                "fields_locked": True,
            },
        },
    )

    result = build_release_dashboard(
        root=root,
        database_path=database,
        report_store_path=root / "data" / "missing-orbit.db",
        offer_id="3828811808",
    )

    governance = result["product"]["seller_sku_governance"]
    assert result["product"]["seller_sku_candidate"] == "0948"
    assert governance["generated_by_system"] is True
    assert governance["allocation_source"] == (
        "automatic_catalog_and_reservation_scan"
    )
    assert governance["available"] is True
    assert governance["suggested_sku_range"] == ["0948"]
    assert governance["reservation_count"] == 1


def test_locked_product_keeps_its_approved_seller_sku(tmp_path):
    root, database = _release_fixture(tmp_path)
    state_path = (
        root / "data" / "new_product_workbench" / "3828811808.json"
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["review"]["seller_sku"] = "0031"
    state["review"]["fields_locked"] = True
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = build_release_dashboard(
        root=root,
        database_path=database,
        report_store_path=root / "data" / "missing-orbit.db",
        offer_id="3828811808",
    )

    governance = result["product"]["seller_sku_governance"]
    assert result["product"]["seller_sku_candidate"] == "0031"
    assert governance["generated_by_system"] is False
    assert governance["allocation_source"] == "approved_workbench_lock"
    assert governance["suggested_sku_range"] == ["0031"]


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
        "tiktok": ["LH_MY", "LH_PH", "LH_TH", "LH_VN"],
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
    assert all(
        status[("tiktok", f"LH_{site}")] == (False, False)
        for site in ("MY", "PH", "TH", "VN")
    )
    assert all(status[("shopee", site)] == (True, True) for site in ("MY", "PH", "TH", "VN"))
    assert status[("ozon", "RU")] == (True, True)
    assert status[("miaoshou", "COMMON")] == (True, True)
    assert preview["all_preflights_passed"] is False
    assert preview["ready"] is False
    assert result["publication_scope"]["default_labels"] == [
        "miaoshou:COMMON",
        "tiktok:LH_PH",
        "tiktok:LH_MY",
        "tiktok:LH_TH",
        "tiktok:LH_VN",
        "shopee:PH",
        "shopee:MY",
        "shopee:TH",
        "shopee:VN",
        "ozon:RU",
    ]
    assert not any(
        "HB_" in label or label in {"tiktok:MX", "tiktok:GB"}
        for label in result["publication_scope"]["default_labels"]
    )


def test_release_dashboard_applies_exact_user_selected_channel_scope_and_prices(
    tmp_path,
):
    root, database = _release_fixture(tmp_path)
    selected = [
        "miaoshou:COMMON",
        "tiktok:LH_PH",
        "tiktok:HB_PH",
        "shopee:PH",
        "ozon:RU",
    ]

    result = build_release_dashboard(
        root=root,
        database_path=database,
        report_store_path=root / "data" / "missing-orbit.db",
        publication_targets=selected,
    )

    scope = result["publication_scope"]
    assert scope["source"] == "user_selection"
    assert scope["selected_labels"] == selected
    assert scope["selected_count"] == 5
    assert len(scope["available_targets"]) == 16
    assert scope["selection_applied_to_plan"] is True
    assert scope["read_only_preflight"] is True
    assert result["omnichannel_preview"]["site_selection"] == {
        "miaoshou": ["COMMON"],
        "tiktok": ["HB_PH", "LH_PH"],
        "shopee": ["PH"],
        "ozon": ["RU"],
    }
    assert {
        (row["channel"], row["site"])
        for row in result["omnichannel_preview"]["targets"]
    } == {
        ("miaoshou", "COMMON"),
        ("tiktok", "LH_PH"),
        ("tiktok", "HB_PH"),
        ("shopee", "PH"),
        ("ozon", "RU"),
    }

    pricing = result["pricing_review"]
    assert len(pricing["all_legacy_store_prices"]) == 10
    assert {
        row["target_key"] for row in pricing["selected_store_prices"]
    } == {"lh_ph", "hb_ph"}
    assert pricing["target_pricing"]["tiktok:LH_PH"]["store_prices"][0][
        "target_key"
    ] == "lh_ph"
    assert pricing["target_pricing"]["tiktok:HB_PH"]["store_prices"][0][
        "target_key"
    ] == "hb_ph"
    assert pricing["target_pricing"]["shopee:PH"]["source"]["region"] == "PH"
    assert pricing["target_pricing"]["shopee:PH"][
        "source_policy"
    ] == "prefer_livelyhive_then_homebloom_within_country"
    assert pricing["target_pricing"]["shopee:PH"][
        "selected_source_target_key"
    ] == "lh_ph"
    assert [
        row["target_key"]
        for row in pricing["target_pricing"]["shopee:PH"]["source_candidates"]
    ] == ["lh_ph", "hb_ph"]
    assert pricing["target_pricing"]["ozon:RU"]["source"]["region"] == "PH"
    assert pricing["target_pricing"]["ozon:RU"][
        "selected_source_target_key"
    ] == "lh_ph"
    assert pricing["publication_target_labels"] == selected
    assert pricing["workbench_selected_store_prices"][0]["target_key"] == "lh_th"


def test_release_dashboard_channel_scope_changes_plan_and_confirmation_token(tmp_path):
    root, database = _release_fixture(tmp_path)
    common = {
        "root": root,
        "database_path": database,
        "report_store_path": root / "data" / "missing-orbit.db",
    }

    thailand = build_release_dashboard(
        **common,
        publication_targets=[
            "miaoshou:COMMON",
            "tiktok:LH_TH",
            "shopee:TH",
        ],
    )
    philippines = build_release_dashboard(
        **common,
        publication_targets=[
            "miaoshou:COMMON",
            "tiktok:LH_PH",
            "shopee:PH",
        ],
    )

    assert (
        thailand["omnichannel_preview"]["plan_id"]
        != philippines["omnichannel_preview"]["plan_id"]
    )
    assert (
        thailand["omnichannel_preview"]["confirmation_token_summary"][
            "scope_fingerprint"
        ]
        != philippines["omnichannel_preview"]["confirmation_token_summary"][
            "scope_fingerprint"
        ]
    )


def test_release_dashboard_exposes_audited_mx_gb_targets_without_defaulting_them(
    tmp_path,
):
    root, database = _release_fixture(tmp_path)

    result = build_release_dashboard(
        root=root,
        database_path=database,
        report_store_path=root / "data" / "missing-orbit.db",
        publication_targets=[
            "miaoshou:COMMON",
            "tiktok:MX",
            "tiktok:GB",
            "ozon:RU",
        ],
    )

    assert len(result["publication_scope"]["available_targets"]) == 16
    status = {
        (row["channel"], row["site"]): (
            row["repository_adapter_audited"],
            row["executable"],
        )
        for row in result["omnichannel_preview"]["targets"]
    }
    assert status[("tiktok", "MX")] == (True, True)
    assert status[("tiktok", "GB")] == (True, True)
    assert {
        row["target_key"] for row in result["pricing_review"]["selected_store_prices"]
    } == {"mx", "gb"}
    assert result["pricing_review"]["target_pricing"]["tiktok:MX"][
        "store_prices"
    ][0]["target_key"] == "mx"
    assert result["pricing_review"]["target_pricing"]["tiktok:GB"][
        "store_prices"
    ][0]["target_key"] == "gb"
    ozon = result["pricing_review"]["target_pricing"]["ozon:RU"]
    assert ozon["selected_source_target_key"] == "mx"
    assert ozon["source_policy"] == (
        "country_priority_PH_MY_TH_VN_MX_GB_then_"
        "prefer_livelyhive_then_homebloom"
    )


def test_release_dashboard_never_defaults_mx_or_gb_from_workbench_site_keys(tmp_path):
    root, database = _release_fixture(tmp_path)
    state_path = root / "data" / "new_product_workbench" / "3828811808.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["review"]["selected_sites"] = ["lh_th", "mx", "gb"]
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = build_release_dashboard(
        root=root,
        database_path=database,
        report_store_path=root / "data" / "missing-orbit.db",
    )

    assert result["publication_scope"]["default_labels"] == [
        "miaoshou:COMMON",
        "tiktok:LH_TH",
        "shopee:TH",
        "ozon:RU",
    ]
    assert "tiktok:MX" not in result["publication_scope"]["selected_labels"]
    assert "tiktok:GB" not in result["publication_scope"]["selected_labels"]


def test_release_dashboard_blocks_shopee_without_selected_same_country_tiktok(
    tmp_path,
):
    root, database = _release_fixture(tmp_path)

    result = build_release_dashboard(
        root=root,
        database_path=database,
        report_store_path=root / "data" / "missing-orbit.db",
        publication_targets=[
            "miaoshou:COMMON",
            "tiktok:LH_TH",
            "shopee:PH",
        ],
    )

    shopee = next(
        row
        for row in result["omnichannel_preview"]["targets"]
        if row["channel"] == "shopee"
    )
    assert shopee["executable"] is False
    assert any(
        check["code"] == "upstream_target_selected" and not check["passed"]
        for check in shopee["preflights"]
    )
    pricing = result["pricing_review"]["target_pricing"]["shopee:PH"]
    assert pricing["status"] == "blocked"
    assert pricing["source_candidates"] == []
    assert "selected TikTok store in PH" in pricing["blocker"]


@pytest.mark.parametrize(
    "targets",
    [
        [],
        [""],
        ["tiktok:US"],
        ["unknown:TH"],
        ["tiktok:LH_TH", "tiktok:lh_th"],
        "tiktok:LH_TH",
    ],
)
def test_release_dashboard_rejects_unallowlisted_or_invalid_channel_scope(
    tmp_path,
    targets,
):
    root, database = _release_fixture(tmp_path)

    with pytest.raises((TypeError, ValueError)):
        build_release_dashboard(
            root=root,
            database_path=database,
            report_store_path=root / "data" / "missing-orbit.db",
            publication_targets=targets,
        )


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


def test_release_dashboard_preserves_legacy_english_title_gate(tmp_path):
    root, database = _release_fixture(tmp_path)
    state_path = root / "data" / "new_product_workbench" / "3828811808.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["review"]["title"] = "可爱小狗墙贴"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = build_release_dashboard(
        root=root,
        database_path=database,
        report_store_path=root / "data" / "missing-orbit.db",
    )

    blocker = "英文标题必须包含英文字母且不能含中文"
    assert result["approval_rehearsal"]["ready"] is False
    assert result["approval_rehearsal"]["state_patch_preview"] == {}
    assert blocker in result["approval_rehearsal"]["blockers"]
    assert result["publication_rehearsal"]["drafts"] == []
    assert result["omnichannel_preview"]["available"] is False
    assert blocker in result["omnichannel_preview"]["blockers"]
    assert result["actual_release_gate"]["ready"] is False
    assert blocker in result["actual_release_gate"]["blockers"]


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
        "seller_sku is reserved by another workbench or verified TikTok claim"
        in result["approval_rehearsal"]["blockers"]
    )


def test_release_dashboard_blocks_legacy_lock_and_claim_and_suggests_next_range(
    tmp_path,
):
    root, database = _release_fixture(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO products VALUES ('990945')")
    for offer_id in (
        "3749982947",
        "3749982951",
        "3749982953",
        "780091850593",
    ):
        _write_json(
            root / "data" / "new_product_workbench" / f"{offer_id}.json",
            {
                "offer_id": offer_id,
                "review": {
                    "seller_sku": "0946",
                    "fields_locked": True,
                },
            },
        )
    _write_json(
        root
        / "data"
        / "new_product_workbench"
        / "3749982951_tiktok_claim.json",
        {
            "claimed": True,
            "sku_numbering": {
                "base_sku": "0946",
                "sku_item_nums": [
                    "0946",
                    "0947",
                    "0948",
                    "0949",
                    "0950",
                    "0951",
                ],
                "verified": True,
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

    governance = result["product"]["seller_sku_governance"]
    assert governance["available"] is False
    assert governance["suggested_base_sku"] == "0952"
    assert governance["suggested_sku_range"] == ["0952"]
    assert {row["offer_id"] for row in governance["reservation_conflicts"]} == {
        "3749982947",
        "3749982951",
        "3749982953",
        "780091850593",
    }
    blocker = (
        "seller_sku is reserved by another workbench or verified TikTok claim"
    )
    assert blocker in result["approval_rehearsal"]["blockers"]
    assert result["approval_rehearsal"]["ready"] is False
    assert blocker in result["actual_release_gate"]["blockers"]


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
    state["review"]["seller_sku"] = initial["product"]["seller_sku_candidate"]
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
