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


def build_official_target_reconciliation_proof(request, allow_refresh: bool = False) -> dict:
    """GET-only proof for an already durable target-scoped operation."""
    if allow_refresh or request.target_label not in _SHOPEE_TARGETS:
        raise TargetScopedRetryError("GET-only reconciliation forbids refresh or unsupported targets")
    from modules.shopee.target_scoped import reconcile_existing_global_site
    receipt = reconcile_existing_global_site(request=request)
    checks = dict(receipt["checks"])
    payload = {"schema_version":"official-target-reconciliation-proof/v1","reconciliation_mode":"official_get_only","reconciliation_request_digest":request.request_digest(),"plan_id":request.plan_id,"run_id":request.run_id,"target_label":request.target_label,"operation_digest":request.operation_digest,"operation_proof_digest":request.operation_proof_digest,"prior_result_digest":request.prior_result_digest,"external_identity_digest":request.external_identity_digest,"provided_by":"03","allow_refresh":False,"checks":checks,"semantic_evidence":dict(receipt["evidence"]),"redacted_summary":dict(receipt["summary"]),"external_writes_performed":[]}
    return {**payload, "observed_at": receipt["observed_at"], "expires_at": receipt["expires_at"], "proof_digest": canonical_digest(payload)}


def reconcile_target_scoped_operation(request, proof) -> AdapterExecutionResult:
    """Return a server-finalizable result; this function never writes externally."""
    raw = proof.durable_payload() if hasattr(proof, "durable_payload") else proof
    if not isinstance(raw, Mapping) or raw.get("allow_refresh") is not False:
        raise TargetScopedRetryError("GET-only reconciliation proof is invalid")
    evidence = raw.get("semantic_evidence") or {}
    verified = all((raw.get("checks") or {}).values()) and evidence.get("derived_status") in {"observed", "warning"}
    return AdapterExecutionResult(verified, verified, "Shopee official GET-only reconciliation" if verified else "Shopee reconciliation requires review", evidence.get("item_id"), {"external_writes_performed": [], "verified": verified, "manual_review_required": evidence.get("manual_review_required") is True, "profit_status":"unverified", "readback": evidence})


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
    required_policy = ("regional_copy_policy_version", "regional_copy_lint_policy_version", "regional_image_verification_policy_version", "regional_observation_policy_digest", "approved_copy_digest", "approved_source_image_manifest_digest", "global_image_observation_policy_version")
    if any(not request.planned_command.get(field) for field in required_policy):
        raise TargetScopedRetryError("planned_command_incomplete: regional observation policy is required")
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
        command=request.planned_command,
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
        "approved_master_digest": request.planned_command["approved_master_digest"],
        "approved_copy_digest": request.planned_command["approved_copy_digest"],
        "global_image_snapshot_digest": global_facts["summary"]["global_image_snapshot_digest"],
        "global_image_observation": global_facts["image_observation"],
        "global_image_outcome": global_facts["image_outcome"],
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
    checks.update({"global_model_unique": True, "copy_digest_exact": True, "global_image_execution_allowed": True, "enabled_logistics_nonempty": bool(logistics)})
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
        image = evidence["global_image_observation"]
        outcome = evidence["global_image_outcome"]
        return _proof_payload(request, checks=checks, semantic_evidence=evidence, summary={
            "target": request.target_label, "state": "exact-zero regional listing", "refresh": "not-used",
            "global_image_status": image["status"],
            "global_image_verification_scope": outcome.get("global_image_verification_scope"),
            "global_image_count": image.get("official_image_id_count"),
            "manual_review_required": outcome.get("manual_review_required") is True,
            "global_image_rule_ids": list(outcome.get("matched_rule_ids") or ()),
            "global_image_evidence_digest": image.get("evidence_digest"),
        })
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
        from modules.shopee.target_scoped import publish_existing_global_site, ShopeeRegionalPublishReconciliationError, ShopeeRegionalPreSubmitError
        try:
            receipt = publish_existing_global_site(request=request, evidence=raw["semantic_evidence"])
        except ShopeeRegionalPreSubmitError as error:
            return AdapterExecutionResult(False, False, "Shopee pre-submit validation failed", None, {"external_writes_performed": [], "pre_submit_failure": True, "error_type": type(error).__name__}, submission_accepted=False)
        except ShopeeRegionalPublishReconciliationError as error:
            safe = dict(error.external_write_evidence)
            allowed = {key: safe[key] for key in ("external_writes_performed", "durable_state_uncertain", "reconciliation_required", "task_id", "item_id", "error_type", "checks") if key in safe}
            return AdapterExecutionResult(False, False, "Shopee accepted publish requires reconciliation", error.external_reference, allowed, submission_accepted=True)
        if receipt.get("verified") is not True:
            return AdapterExecutionResult(False, False, "Shopee dispatch/readback requires reconciliation", str(receipt.get("item_id") or "") or None, {"external_writes_performed": ["shopee:regional_publish"], "reconciliation_required": True})
        return AdapterExecutionResult(True, True, "Shopee one-site publish readback verified", str(receipt["item_id"]), {"verified": True, "source_copy_verified": receipt.get("source_copy_verified") is True, "external_writes_performed": ["shopee:regional_publish"], "manual_review_required": receipt.get("manual_review_required") is True, "profit_status": "unverified", "derived_translation_status": receipt.get("derived_translation_status"), "derived_image_status": receipt.get("derived_image_status"), "global_image_status": receipt.get("global_image_status"), "global_image_verification_scope": receipt.get("global_image_verification_scope"), "global_image_url_identity_exact": False, "global_image_approved_order_exact": receipt.get("global_image_approved_order_exact") is True, "semantic_equivalence": "unverified", "matched_rule_ids": receipt.get("matched_rule_ids") or [], "observation_evidence_digest": receipt.get("observation_evidence_digest"), "global_image_observation_digest": receipt.get("global_image_observation_digest"), "global_image_outcome_digest": receipt.get("global_image_outcome_digest"), "readback": receipt})
    if request.target_label == _OZON_TARGET:
        if not request.planned_command:
            raise TargetScopedRetryError("Ozon successor stock decision is required")
        from modules.ozon.target_scoped import stock_existing_product
        receipt = stock_existing_product(product_id="5687436857", offer_id=request.seller_sku[-4:].zfill(4))
        if receipt.get("verified") is not True:
            return AdapterExecutionResult(False, False, "Ozon stock/readback requires reconciliation", "5687436857", {"external_writes_performed": ["ozon:stock:update"], "reconciliation_required": True})
        return AdapterExecutionResult(True, True, "Ozon existing-product stock readback verified", "5687436857", {"verified": True, "external_writes_performed": ["ozon:stock:update"], "readback": receipt})
    raise TargetScopedRetryError("unsupported target")
