from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "delist-products-by-sku" / "scripts" / "delist_products_by_sku.py"
SPEC = importlib.util.spec_from_file_location("delist_products_by_sku", SCRIPT)
assert SPEC and SPEC.loader
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def test_all_scope_is_exactly_fifteen_targets() -> None:
    assert len(MOD.ALL_TARGETS) == 15
    assert set(MOD.ALL_TARGETS) == {
        "tiktok:LH_PH", "tiktok:LH_MY", "tiktok:LH_TH", "tiktok:LH_VN",
        "tiktok:HB_PH", "tiktok:HB_MY", "tiktok:HB_TH", "tiktok:HB_VN",
        "tiktok:MX", "tiktok:GB", "shopee:PH", "shopee:MY",
        "shopee:TH", "shopee:VN", "ozon:RU",
    }


def test_digest_detects_plan_mutation() -> None:
    plan = {"schema_version": "product-delist-plan/v1", "targets": []}
    plan["plan_digest"] = MOD._digest(plan)
    MOD._verify_plan(plan)
    plan["targets"].append({"target_label": "shopee:PH"})
    try:
        MOD._verify_plan(plan)
    except ValueError as exc:
        assert "digest mismatch" in str(exc)
    else:
        raise AssertionError("mutated plan must be rejected")


def test_mixed_product_is_never_executable(monkeypatch) -> None:
    monkeypatch.setattr(MOD, "_local_tiktok_rows", lambda skus: [{
        "target_label": "tiktok:LH_PH", "platform": "tiktok",
        "requested_skus": ["0975", "0976"],
        "all_product_skus": ["0975", "0976", "0999"],
        "current_status": "ACTIVATE", "action": "DEACTIVATE_PRODUCT",
    }])
    monkeypatch.setattr(MOD, "_shopee_rows", lambda skus: [])
    monkeypatch.setattr(MOD, "_ozon_rows", lambda skus: [])
    monkeypatch.setattr(MOD, "_historical_tiktok_rows", lambda skus: [])
    plan = MOD.build_plan(("0975", "0976"), live=False)
    row = next(item for item in plan["targets"] if item["target_label"] == "tiktok:LH_PH")
    assert row["status"] == "BLOCKED_MIXED_PRODUCT"
    assert row["executable"] is False


def test_execution_rejects_tampered_plan() -> None:
    plan = {"schema_version": "product-delist-plan/v1", "requested_skus": ["0975"], "targets": []}
    plan["plan_digest"] = MOD._digest(plan)
    plan["requested_skus"] = ["0976"]
    try:
        MOD.execute(plan)
    except ValueError:
        pass
    else:
        raise AssertionError("tampered plan must not execute")


def test_readback_preserves_frozen_plan_digest(monkeypatch) -> None:
    plan = {
        "schema_version": "product-delist-plan/v1",
        "requested_skus": ["0975", "0976"],
        "targets": [{
            "target_label": "tiktok:LH_PH", "platform": "tiktok",
            "product_id": "123", "shop_cipher": "cipher",
            "requested_skus": ["0975", "0976"],
            "all_product_skus": ["0975", "0976"], "status": "READY",
        }],
    }
    plan["plan_digest"] = MOD._digest(plan)
    frozen_digest = plan["plan_digest"]
    monkeypatch.setattr(MOD, "_live_verify", lambda row: {
        **row, "current_status": "SELLER_DEACTIVATED",
        "identity_source": "tiktok_official_product_detail",
    })
    report = MOD.readback(plan)
    assert report["plan_digest"] == frozen_digest
    assert report["verified_count"] == 1
    assert report["targets"][0]["outcome"] == "VERIFIED_DELISTED"


def test_tiktok_readback_fails_closed_on_unknown_status(monkeypatch) -> None:
    plan = {
        "schema_version": "product-delist-plan/v1",
        "requested_skus": ["0975"],
        "targets": [{
            "target_label": "tiktok:LH_PH", "platform": "tiktok",
            "product_id": "123", "shop_cipher": "cipher",
            "requested_skus": ["0975"], "all_product_skus": ["0975"],
            "status": "READY",
        }],
    }
    plan["plan_digest"] = MOD._digest(plan)
    monkeypatch.setattr(MOD, "_live_verify", lambda row: {**row, "current_status": "UNKNOWN_NEW_STATE"})
    report = MOD.readback(plan)
    assert report["verified_count"] == 0
    assert report["targets"][0]["verified"] is False


def test_skill_never_instructs_permanent_delete() -> None:
    text = (ROOT / "skills" / "delist-products-by-sku" / "SKILL.md").read_text(encoding="utf-8")
    assert "永远不要删除商品" in text
    assert "unlist=true" in text
