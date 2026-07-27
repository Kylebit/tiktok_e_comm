from datetime import datetime, timezone

import pytest

from domains.channel_operations import target_scoped_retry_adapters as adapters
from shared_platform.target_scoped_release_contracts import (
    OfficialTargetProof,
    TargetScopedOperationRequest,
    target_failure_digest,
    target_preflight_digest,
)


def request(target="shopee:MY"):
    kind = "shopee_safe_pre_submit_retry_v1" if target.startswith("shopee") else "ozon_existing_product_stock_reconciliation_v1"
    command = ({"target_label": target, "operation_kind": kind, "region": target[-2:], "model_sku": "0954", "approved_master_digest": "master", "regional_title": "title", "regional_description": "description", "regional_image_urls": ["image"], "parcel": {"weight_kg": .12, "dimensions_cm": [40, 3, 3]}, "excluded_logistics_ids": [50052] if target.endswith("VN") else [], "local_original_price": 10, "item_status": "NORMAL"} if target.startswith("shopee") else {"target_label": target, "operation_kind": kind})
    command_digest = adapters.canonical_digest(command)
    failure = target_failure_digest(target_label=target, attempts=1, error="failed", failure_event_digests=[])
    return TargetScopedOperationRequest(plan_id="plan", confirmation_token="token", approval_scope_digest="scope", product_id="3838616043", seller_sku="0954", product_package_id="product", content_package_id="content", run_id="run", target_label=target, operation_kind=kind, product_revision=31, payload_digest="payload", planned_command=command, planned_command_digest=command_digest, failure_attempt=1, failure_digest=failure, target_idempotency_key="idempotency", preflight_digest=target_preflight_digest(plan_id="plan", run_id="run", target_label=target, operation_kind=kind, product_revision=31, payload_digest="payload", planned_command_digest=command_digest, failure_attempt=1, failure_digest=failure, target_idempotency_key="idempotency"))


def test_shopee_proof_is_full_scan_no_refresh_and_vn_filters_50052(monkeypatch):
    req = request("shopee:VN")
    monkeypatch.setattr(adapters, "_prepared_shopee_credentials", lambda region: (12, "prepared"))
    monkeypatch.setattr("modules.shopee.target_scoped.scan_prepared_shop_sku", lambda **_kw: {"matches": [], "complete": True, "statuses": {"NORMAL": {"pages": 1, "complete": True}, "UNLIST": {"pages": 1, "complete": True}, "BANNED": {"pages": 1, "complete": True}}})
    monkeypatch.setattr("modules.shopee.target_scoped.compatible_prepared_logistics", lambda **_kw: [1, 2, 3])
    monkeypatch.setattr("modules.shopee.target_scoped.inspect_existing_global", lambda **_kw: {"global_model_id": "1", "tier_index": [0]})
    monkeypatch.setattr("modules.shopee.global_sku_map.global_item_id_for_match_key", lambda _sku: "40283034166")
    proof = adapters.build_official_target_proof(req)
    parsed = OfficialTargetProof.from_value(proof, request=req)
    assert parsed.allow_refresh is False
    assert parsed.checks["full_pagination_exact_zero"] is True
    assert parsed.checks["vn_50052_absent"] is True
    assert parsed.semantic_evidence["compatible_logistics_count"] == 3
    with pytest.raises(adapters.TargetScopedRetryError, match="forbids token refresh"):
        adapters.build_official_target_proof(req, allow_refresh=True)


def test_existing_or_ambiguous_shop_listing_fails_before_dispatch(monkeypatch):
    monkeypatch.setattr(adapters, "_prepared_shopee_credentials", lambda _region: (12, "prepared"))
    monkeypatch.setattr("modules.shopee.target_scoped.scan_prepared_shop_sku", lambda **_kw: {"matches": [{"item_id": "one"}], "complete": True, "statuses": {}})
    with pytest.raises(adapters.TargetScopedRetryError, match="exact-zero"):
        adapters.build_official_target_proof(request())


def test_scan_never_returns_after_normal_terminal_page(monkeypatch):
    from modules.shopee import target_scoped
    calls = []
    def get(path, _shop, _token, query):
        calls.append((path, query.get("item_status")))
        if path.endswith("get_item_list"):
            return {"response": {"item": [], "has_next_page": False}}
        return {"response": {"item_list": []}}
    monkeypatch.setattr(target_scoped, "shop_get", get)
    result = target_scoped.scan_prepared_shop_sku(shop_id=1, access_token="x", seller_sku="0954")
    assert result["complete"] is True
    assert [status for path, status in calls if path.endswith("get_item_list")] == ["NORMAL", "UNLIST", "BANNED"]


def test_ozon_proof_requires_exact_existing_product_and_no_stock(monkeypatch):
    req = request("ozon:RU")
    monkeypatch.setattr("modules.ozon.target_scoped.read_existing_product", lambda **_kw: {"product_id": "5687436857", "checks": {key: True for key in ("created", "approved", "title", "price", "images", "stock_false")}})
    proof = adapters.build_official_target_proof(req)
    assert OfficialTargetProof.from_value(proof, request=req).checks["no_import_or_create"]


def test_execute_never_uses_legacy_publish_or_ozon_import(monkeypatch):
    req = request()
    monkeypatch.setattr(adapters, "_prepared_shopee_credentials", lambda _region: (12, "prepared"))
    monkeypatch.setattr("modules.shopee.target_scoped.scan_prepared_shop_sku", lambda **_kw: {"matches": [], "complete": True, "statuses": {"NORMAL": {"pages": 1, "complete": True}, "UNLIST": {"pages": 1, "complete": True}, "BANNED": {"pages": 1, "complete": True}}})
    monkeypatch.setattr("modules.shopee.global_sku_map.global_item_id_for_match_key", lambda _sku: "40283034166")
    monkeypatch.setattr("modules.shopee.target_scoped.inspect_existing_global", lambda **_kw: {"global_model_id": "1", "tier_index": [0]})
    monkeypatch.setattr("modules.shopee.target_scoped.compatible_prepared_logistics", lambda **_kw: [1])
    proof = OfficialTargetProof.from_value(adapters.build_official_target_proof(req), request=req)
    calls = []
    monkeypatch.setattr("modules.shopee.target_scoped.publish_existing_global_site", lambda **_kw: calls.append(_kw) or {"item_id": "123", "verified": True})
    result = adapters.execute_target_scoped_operation(req, proof)
    assert result.succeeded and result.readback_verified
    assert result.readback_evidence["external_writes_performed"] == ["shopee:regional_publish"]
    assert len(calls) == 1
