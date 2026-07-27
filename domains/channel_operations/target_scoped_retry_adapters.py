"""Proof-bound recovery adapters for the three remaining channel targets.

This module intentionally does not use the generic release executor.  It is
called only by the platform's target-scoped claim seam and therefore never
loops over failed targets, refreshes credentials, creates global products, or
persists marketplace discovery state.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from domains.channel_operations.release_executor import AdapterExecutionResult
from shared_platform.target_scoped_release_contracts import (
    TargetScopedOperationRequest,
    canonical_digest,
)


_SHOPEE_TARGETS = frozenset(("shopee:MY", "shopee:VN"))
_OZON_TARGET = "ozon:RU"


class TargetScopedRetryError(RuntimeError):
    """The official proof is insufficient for a bounded recovery action."""


def _proof_payload(request: TargetScopedOperationRequest, *, checks: Mapping[str, bool], semantic_evidence: Mapping[str, Any], summary: Mapping[str, Any]) -> dict[str, Any]:
    observed = datetime.now(timezone.utc)
    payload = {
        "schema_version": "official-target-proof/v1",
        "operation_kind": request.operation_kind,
        "plan_id": request.plan_id,
        "run_id": request.run_id,
        "target_label": request.target_label,
        "product_revision": request.product_revision,
        "payload_digest": request.payload_digest,
        "planned_command_digest": request.planned_command_digest,
        "preflight_digest": request.preflight_digest,
        "failure_attempt": request.failure_attempt,
        "failure_digest": request.failure_digest,
        "provided_by": "03",
        "allow_refresh": False,
        "checks": dict(checks),
        "semantic_evidence": dict(semantic_evidence),
        "external_writes_performed": [],
    }
    return {
        **payload,
        "observed_at": observed.isoformat(),
        "expires_at": (observed + timedelta(minutes=5)).isoformat(),
        "redacted_summary": dict(summary),
        "proof_digest": canonical_digest(payload),
    }


def _prepared_shopee_credentials(region: str) -> tuple[int, str]:
    """Read a still-valid token without calling any refresh/sync routine."""
    from modules.products.release_adapters import _shopee_readback_credentials

    return _shopee_readback_credentials(region, allow_token_refresh=False)


def _shopee_proof(request: TargetScopedOperationRequest) -> tuple[dict[str, Any], dict[str, Any]]:
    """Full-paginate to prove exact zero regional listings and known global ID."""
    from modules.shopee.global_sku_map import global_item_id_for_match_key
    from modules.shopee.target_scoped import scan_prepared_shop_sku

    region = request.target_label.rsplit(":", 1)[1]
    shop_id, token = _prepared_shopee_credentials(region)
    seller_sku = request.seller_sku[-4:].zfill(4)
    scan = scan_prepared_shop_sku(
        shop_id=shop_id, access_token=token, seller_sku=seller_sku
    )
    matches = scan.get("matches") if isinstance(scan, Mapping) else scan
    if not isinstance(matches, list) or not isinstance(scan, Mapping) or scan.get("complete") is not True:
        raise TargetScopedRetryError("regional full-status scan is incomplete")
    if matches:
        raise TargetScopedRetryError("regional SKU is not exact-zero; reconciliation is required")
    global_item_id = str(global_item_id_for_match_key(seller_sku) or "")
    if not global_item_id:
        raise TargetScopedRetryError("existing global item identity is required")
    from modules.shopee.target_scoped import inspect_existing_global, compatible_prepared_logistics
    global_facts = inspect_existing_global(
        shop_id=shop_id, access_token=token, global_item_id=global_item_id,
        model_sku=request.planned_command["model_sku"],
        approved_master_digest=request.planned_command["approved_master_digest"],
    )
    evidence: dict[str, Any] = {
        "source": "shopee:official_get_only",
        "region": region,
        "shop_id": int(shop_id),
        "seller_sku_suffix": seller_sku,
        "global_item_id": global_item_id,
        "full_pagination": True,
        "regional_match_count": 0,
        "status_scan": dict(scan.get("statuses") or {}),
        "authentication": "prepared_token_only",
        "global_model_id": global_facts["global_model_id"],
        "global_tier_index": global_facts["tier_index"],
        "approved_master_digest": request.planned_command_digest,
    }
    checks: dict[str, bool] = {
        "prepared_token": True,
        "full_pagination_exact_zero": True,
        "existing_global_identity": True,
        "no_refresh": True,
    }
    logistics = compatible_prepared_logistics(
        shop_id=shop_id, access_token=token, parcel=request.planned_command["parcel"],
        excluded_ids=request.planned_command.get("excluded_logistics_ids") or (),
    )
    if region == "VN":
        ids = tuple(sorted(int(value) for value in logistics))
        if not ids or 50052 in ids:
            raise TargetScopedRetryError("VN compatible logistics proof is not exact")
        evidence.update({"compatible_logistics_digest": canonical_digest({"ids": ids}), "compatible_logistics_count": len(ids), "unsupported_50052_absent": True})
        checks.update({"vn_logistics_nonempty": True, "vn_50052_absent": True})
    evidence["selected_logistics_ids"] = tuple(sorted(int(value) for value in logistics))
    evidence["selected_logistics_digest"] = canonical_digest({"ids": evidence["selected_logistics_ids"]})
    checks.update({"global_model_unique": True, "master_digest_exact": True, "enabled_logistics_nonempty": bool(logistics)})
    if not logistics:
        raise TargetScopedRetryError("no enabled logistics satisfies the immutable parcel policy")
    return checks, evidence


def _ozon_proof(request: TargetScopedOperationRequest) -> tuple[dict[str, Any], dict[str, Any]]:
    from modules.ozon.target_scoped import read_existing_product

    product = read_existing_product(offer_id=request.seller_sku[-4:].zfill(4))
    if str(product.get("product_id") or "") != "5687436857":
        raise TargetScopedRetryError("Ozon product identity is not the approved existing product")
    required = ("created", "approved", "title", "price", "images", "stock_false")
    if any(product.get("checks", {}).get(key) is not True for key in required):
        raise TargetScopedRetryError("Ozon product is not exact created-and-unstocked")
    return ({key: True for key in required} | {"existing_product_id": True, "no_import_or_create": True}, {
        "source": "ozon:official_semantic_read",
        "offer_id": request.seller_sku[-4:].zfill(4),
        "product_id": "5687436857",
        "state": "exact_created_unstocked",
        "checks": {key: True for key in required},
    })


def build_official_target_proof(request: TargetScopedOperationRequest, allow_refresh: bool = False) -> dict:
    """Build a zero-write proof. Token refresh is deliberately forbidden."""
    if allow_refresh:
        raise TargetScopedRetryError("target-scoped proof forbids token refresh")
    if request.target_label in _SHOPEE_TARGETS:
        checks, evidence = _shopee_proof(request)
        return _proof_payload(request, checks=checks, semantic_evidence=evidence, summary={"target": request.target_label, "state": "exact-zero regional listing", "refresh": "not-used"})
    if request.target_label == _OZON_TARGET:
        checks, evidence = _ozon_proof(request)
        return _proof_payload(request, checks=checks, semantic_evidence=evidence, summary={"target": request.target_label, "state": "existing product stock-only"})
    raise TargetScopedRetryError("only shopee:MY, shopee:VN and ozon:RU are supported")


def _assert_proof(request: TargetScopedOperationRequest, proof: Any) -> Mapping[str, Any]:
    raw = proof.durable_payload() if hasattr(proof, "durable_payload") else proof
    if not isinstance(raw, Mapping) or raw.get("target_label") != request.target_label:
        raise TargetScopedRetryError("proof target identity does not match request")
    if raw.get("planned_command_digest") != request.planned_command_digest or raw.get("proof_digest") != canonical_digest({key: raw[key] for key in ("schema_version", "operation_kind", "plan_id", "run_id", "target_label", "product_revision", "payload_digest", "planned_command_digest", "preflight_digest", "failure_attempt", "failure_digest", "provided_by", "allow_refresh", "checks", "semantic_evidence", "external_writes_performed")}):
        raise TargetScopedRetryError("proof digest is stale or invalid")
    return raw


def execute_target_scoped_operation(request: TargetScopedOperationRequest, proof: Any) -> AdapterExecutionResult:
    """Perform one exact existing-object action then bounded official readback."""
    raw = _assert_proof(request, proof)
    if request.target_label in _SHOPEE_TARGETS:
        from modules.shopee.target_scoped import publish_existing_global_site
        receipt = publish_existing_global_site(request=request, evidence=raw["semantic_evidence"])
        if receipt.get("verified") is not True:
            return AdapterExecutionResult(False, False, "Shopee dispatch/readback requires reconciliation", str(receipt.get("item_id") or "") or None, {"external_writes_performed": ["shopee:regional_publish"], "reconciliation_required": True})
        return AdapterExecutionResult(True, True, "Shopee one-site publish readback verified", str(receipt["item_id"]), {"verified": True, "external_writes_performed": ["shopee:regional_publish"], "readback": receipt})
    if request.target_label == _OZON_TARGET:
        if not request.planned_command:
            raise TargetScopedRetryError("Ozon successor stock decision is required")
        from modules.ozon.target_scoped import stock_existing_product
        receipt = stock_existing_product(product_id="5687436857", offer_id=request.seller_sku[-4:].zfill(4))
        if receipt.get("verified") is not True:
            return AdapterExecutionResult(False, False, "Ozon stock/readback requires reconciliation", "5687436857", {"external_writes_performed": ["ozon:stock:update"], "reconciliation_required": True})
        return AdapterExecutionResult(True, True, "Ozon existing-product stock readback verified", "5687436857", {"verified": True, "external_writes_performed": ["ozon:stock:update"], "readback": receipt})
    raise TargetScopedRetryError("unsupported target")
