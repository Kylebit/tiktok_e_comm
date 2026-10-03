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


def test_missing_legacy_shopee_cache_is_an_empty_local_mapping(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(MOD, "REPO_ROOT", tmp_path)

    assert MOD._shopee_rows(("0963",)) == []


def test_missing_shopee_cache_keeps_official_live_discovery(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(MOD, "REPO_ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(MOD, "_live_tiktok_rows", lambda skus: [])
    monkeypatch.setattr(MOD, "_local_tiktok_rows", lambda skus: [])
    monkeypatch.setattr(MOD, "_historical_tiktok_rows", lambda skus: [])
    monkeypatch.setattr(MOD, "_durable_miaoshou_tiktok_rows", lambda skus: [])
    monkeypatch.setattr(MOD, "_ozon_rows", lambda skus: [])

    def live_shopee(skus):
        calls.append(skus)
        return [{
            "target_label": "shopee:PH", "platform": "shopee",
            "store_name": "Shopee PH", "shop_id": "123",
            "product_id": "456", "title": "Exact live item",
            "requested_skus": ["0963"], "all_product_skus": ["0963"],
            "current_status": "NORMAL",
            "identity_source": "shopee_official_active_item_and_models",
            "action": "UNLIST_ITEM",
        }]

    monkeypatch.setattr(MOD, "_live_shopee_rows", live_shopee)

    plan = MOD.build_plan(("0963",), live=True)

    assert calls == [("0963",)]
    row = next(item for item in plan["targets"] if item["target_label"] == "shopee:PH")
    assert row["status"] == "READY"
    assert row["identity_source"] == "shopee_official_active_item_and_models"


def test_offline_shopee_plan_requires_provider_discovery_before_not_found(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(MOD, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(MOD, "_local_tiktok_rows", lambda skus: [])
    monkeypatch.setattr(MOD, "_historical_tiktok_rows", lambda skus: [])
    monkeypatch.setattr(MOD, "_durable_miaoshou_tiktok_rows", lambda skus: [])
    monkeypatch.setattr(MOD, "_ozon_rows", lambda skus: [])

    plan = MOD.build_plan(("0963",), live=False)

    rows = [row for row in plan["targets"] if row["platform"] == "shopee"]
    assert {row["status"] for row in rows} == {"NEEDS_PROVIDER_DISCOVERY"}
    assert all("official provider discovery" in row["reason"] for row in rows)


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


def test_failed_fresh_read_cannot_reuse_frozen_unlisted_status(monkeypatch):
    plan = {'schema_version':'product-delist-plan/v1','requested_skus':['0975'],
            'targets':[{'target_label':'shopee:MY','platform':'shopee','product_id':'123',
                        'requested_skus':['0975'],'all_product_skus':['0975'],
                        'current_status':'UNLIST','status':'READY'}]}
    plan['plan_digest'] = MOD._digest(plan)
    monkeypatch.setattr(MOD, '_live_verify', lambda row: {**row, 'live_read_error':'ReadTimeout'})
    result = MOD.readback(plan)
    assert result['verified_count'] == 0
    assert result['targets'][0]['verified'] is False


def test_live_tiktok_discovery_uses_current_official_modules(monkeypatch) -> None:
    from core import api_client, auth, shops

    monkeypatch.setattr(auth, "access_token", lambda: "token")
    monkeypatch.setattr(shops, "list_shops", lambda token: [{
        "id": "shop-1", "name": "LivelyHive", "region": "PH",
        "cipher": "cipher-1",
    }])
    calls = []

    def official_search(path, token, query, body):
        calls.append((path, dict(query), dict(body)))
        if not query.get("page_token"):
            return {"code": 0, "data": {
                "total_count": 2, "next_page_token": "next", "products": [{
                    "id": "other", "title": "Other",
                    "skus": [{"seller_sku": "other-9999"}],
                }],
            }}
        return {"code": 0, "data": {
            "total_count": 2, "next_page_token": "", "products": [{
                "id": "wanted", "title": "Exact",
                "skus": [{"seller_sku": "seller-0227"}],
            }],
        }}

    monkeypatch.setattr(api_client, "post", official_search)

    rows = MOD._live_tiktok_rows(("0227",))

    assert [row["product_id"] for row in rows] == ["wanted"]
    assert rows[0]["target_label"] == "tiktok:LH_PH"
    assert rows[0]["all_product_skus"] == ["0227"]
    assert calls[1][1]["page_token"] == "next"


def test_live_shopee_discovery_uses_current_official_modules(monkeypatch) -> None:
    from modules.shopee import auth, shops, sync

    monkeypatch.setattr(shops, "sync_shop_ids", lambda: {"PH": 123})
    monkeypatch.setattr(auth, "ensure_shop_token", lambda shop_id: "token")
    monkeypatch.setattr(sync, "_fetch_item_ids", lambda shop_id, token: [456])
    monkeypatch.setattr(sync, "_fetch_items_base", lambda shop_id, token, item_ids: [{
        "item_id": 456, "item_name": "Exact", "has_model": True,
    }])
    calls = []

    def official_models(shop_id, region, token, item, *, use_cache=True, force_refresh=False):
        calls.append((shop_id, region, use_cache))
        return ([{"seller_sku": "seller-0227"}], False)

    monkeypatch.setattr(sync, "_rows_from_item", official_models)

    rows = MOD._live_shopee_rows(("0227",))

    assert [row["product_id"] for row in rows] == ["456"]
    assert rows[0]["target_label"] == "shopee:PH"
    assert rows[0]["all_product_skus"] == ["0227"]
    assert calls == [(123, "PH", False)]


def test_live_discovery_has_no_dangling_audit_script_imports() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "scripts.audit_uncovered_discounts" not in text
    assert "scripts.audit_uncovered_shopee_discounts" not in text


def test_execute_completes_guard_from_only_executable_target_scope(monkeypatch) -> None:
    import shared_platform.operations_domain_guard as guard_module

    plan = {
        "schema_version": "product-delist-plan/v1",
        "requested_skus": ["0227"],
        "targets": [
            {"target_label": "tiktok:LH_MY", "status": "READY", "executable": True},
            {"target_label": "tiktok:HB_MY", "status": "NEEDS_PROVIDER_DISCOVERY", "executable": False},
            {"target_label": "shopee:MY", "status": "NOT_FOUND", "executable": False},
        ],
    }
    plan["plan_digest"] = MOD._digest(plan)
    monkeypatch.setattr(guard_module, "begin_delisting", lambda *args, **kwargs: (object(), "operation", {"acquired": True}))
    captured = {}
    monkeypatch.setattr(
        guard_module,
        "finish_delisting",
        lambda guard, result, *, target_labels=None: captured.update(
            {"guard": guard, "result": result, "target_labels": target_labels}
        ),
    )
    monkeypatch.setattr(MOD, "_execute_one", lambda row: {
        "target_label": row["target_label"],
        "verified": row["target_label"] == "tiktok:LH_MY",
        "outcome": "VERIFIED_DELISTED" if row["target_label"] == "tiktok:LH_MY" else row["status"],
        "external_write_count": 1 if row["target_label"] == "tiktok:LH_MY" else 0,
    })

    result = MOD.execute(plan)

    assert result["verified_count"] == 1
    assert captured["target_labels"] == {"tiktok:LH_MY"}


def test_finish_delisting_ignores_non_executable_plan_rows() -> None:
    import shared_platform.operations_domain_guard as guard_module

    class Engine:
        def __init__(self):
            self.calls = []

        def complete_domain_operation(self, operation, *, provider_readback_ref):
            self.calls.append((operation, provider_readback_ref))

    engine = Engine()
    result = {"targets": [
        {"target_label": "tiktok:LH_MY", "verified": True},
        {"target_label": "tiktok:HB_MY", "verified": False},
        {"target_label": "shopee:MY", "verified": False},
    ]}

    guard_module.finish_delisting(
        (engine, "delisting:plan", {"acquired": True}),
        result,
        target_labels={"tiktok:LH_MY"},
    )

    assert len(engine.calls) == 1
    assert engine.calls[0][0] == "delisting:plan"
    assert engine.calls[0][1].startswith("delisting-result:")
