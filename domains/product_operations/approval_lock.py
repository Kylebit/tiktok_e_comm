"""Pure approval and seller-SKU lock gate for product-operation state.

The gate is intentionally a preview: it never reads or writes a database,
workbench file, marketplace, or channel.  A caller may persist its returned
``state_patch`` into the existing workbench state only after an explicit human
approval has been recorded by the owning UI/workflow.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from shared_platform.contracts import ApprovedProductPackage, ContentPackage, contract_payload

from .adapters import approval_record_from_fact, product_record_from_legacy_row


@dataclass(frozen=True)
class ProductApprovalLockPreview:
    """Result of evaluating a proposed product approval without persisting it."""

    approved_package: ApprovedProductPackage | None
    state_patch: Mapping[str, Any]
    blockers: tuple[str, ...]
    idempotent: bool
    supersedes_approval_id: str | None


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(product: object, content: ContentPackage, seller_sku: str) -> str:
    payload = {
        "product": contract_payload(product),
        "content_package": contract_payload(content),
        "seller_sku": seller_sku,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _content_blockers(content: ContentPackage | None, product_id: str) -> list[str]:
    if content is None:
        return ["approved content package is required"]
    if content.product_id != product_id:
        return ["content package product_id must match product_id"]
    approval = content.approval
    if approval is None:
        return ["content package approval is required"]
    if approval.subject_type != "content_package" or approval.subject_id != content.package_id:
        return ["content package approval subject does not match package"]
    if approval.status != "approved":
        return ["content package approval is not approved"]
    return []


def preview_product_approval_lock(
    *,
    state: Mapping[str, Any],
    product_row: Mapping[str, Any] | Any,
    content_package: ContentPackage | None,
    seller_sku: str,
    known_seller_skus: tuple[str, ...] | list[str] | set[str],
    user_approved: bool,
    approval_fact: Mapping[str, Any] | Any,
    expected_revision: int | None = None,
) -> ProductApprovalLockPreview:
    """Validate an explicit approval and return the state patch it would make.

    ``known_seller_skus`` must come from a read-only catalog query.  The
    proposed SKU is unique only when it is non-empty and absent from that set.
    An active prior lock with a different input fingerprint is reported as
    superseded, never silently reused.
    """
    product = product_record_from_legacy_row(product_row)
    fact = dict(approval_fact)
    clean_sku = _clean(seller_sku)
    blockers: list[str] = []
    if user_approved is not True:
        blockers.append("explicit user_approved=True is required")
    if clean_sku != product.seller_sku:
        blockers.append("seller_sku must match the product record")
    if not clean_sku:
        blockers.append("seller_sku is required")
    if clean_sku in {_clean(value) for value in known_seller_skus}:
        blockers.append("seller_sku is already present in the catalog")

    current_revision = int(state.get("_revision") or 0)
    if expected_revision is not None and expected_revision != current_revision:
        blockers.append("state revision is stale")
    state_offer_id = _clean(state.get("offer_id"))
    if state_offer_id and state_offer_id != product.product_id:
        blockers.append("state offer_id must match product_id")
    blockers.extend(_content_blockers(content_package, product.product_id))

    try:
        approval = approval_record_from_fact(fact, product_id=product.product_id)
        package_id = _clean(fact.get("package_id"))
        if not package_id:
            blockers.append("package_id is required")
    except (TypeError, ValueError) as error:
        approval = None
        package_id = ""
        blockers.append(str(error))

    if blockers or approval is None or content_package is None:
        return ProductApprovalLockPreview(None, {}, tuple(blockers), False, None)

    signature = _fingerprint(product, content_package, clean_sku)
    prior = state.get("product_approval")
    if not isinstance(prior, Mapping):
        prior = {}
    prior_id = _clean(prior.get("approval_id")) or None
    prior_active = _clean(prior.get("status")) == "approved"
    if prior_active and _clean(prior.get("input_fingerprint")) == signature:
        return ProductApprovalLockPreview(None, {}, (), True, None)

    source_reference = _clean(fact.get("source_reference")) or None
    package = ApprovedProductPackage(package_id, product, approval, source_reference)
    patch = {
        "review": {"seller_sku": clean_sku, "fields_locked": True},
        "product_approval": {
            "approval_id": approval.approval_id,
            "package_id": package.package_id,
            "status": "approved",
            "subject_type": "product",
            "subject_id": product.product_id,
            "seller_sku": clean_sku,
            "content_package_id": content_package.package_id,
            "content_approval_id": content_package.approval.approval_id,
            "input_fingerprint": signature,
            "approved_by": approval.approved_by,
            "approved_at": approval.approved_at.isoformat() if isinstance(approval.approved_at, datetime) else None,
        },
    }
    return ProductApprovalLockPreview(package, patch, (), False, prior_id if prior_active else None)
