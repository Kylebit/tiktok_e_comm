"""Read-only translations from legacy product facts to domain contracts.

These helpers deliberately accept already-fetched rows or dictionaries.  They
do not open a database connection, mutate a row, or contact a marketplace.
Approval facts must be supplied explicitly; legacy product state alone never
creates an approved package.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Any

from shared_platform.contracts import ApprovalRecord, ApprovedProductPackage, ProductRecord


def _as_dict(row: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Copy a mapping-like legacy row without changing its source."""
    if isinstance(row, Mapping):
        return dict(row)
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    raise TypeError("legacy product rows must be mappings or sqlite-style rows")


def _required(values: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = str(values.get(name) or "").strip()
        if value:
            return value
    raise ValueError(f"missing required field: {names[0]}")


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        candidates: Iterable[Any] = (value,)
    elif isinstance(value, Iterable):
        candidates = value
    else:
        candidates = (value,)
    return tuple(dict.fromkeys(str(item).strip() for item in candidates if str(item).strip()))


def product_record_from_legacy_row(row: Mapping[str, Any] | Any) -> ProductRecord:
    """Translate one existing product/SKU row or product-workbench dictionary.

    The primary catalog spelling is ``product_id``, ``seller_sku``,
    ``product_name``, and ``sku_id``.  The alternative spellings preserve the
    existing workbench dictionary shape (``itemNum`` and ``title``) without
    coupling the contract to it.
    """
    values = _as_dict(row)
    product_id = _required(values, "product_id", "item_id", "id")
    seller_sku = _required(values, "seller_sku", "itemNum", "sku")
    title = _required(values, "product_name", "title", "source_title")
    sku_ids = _string_tuple(values.get("sku_ids")) or _string_tuple(
        values.get("sku_id") or values.get("global_sku_id") or values.get("model_id")
    )

    attributes = {
        key: str(values[key]).strip()
        for key in ("platform", "region", "shop_cipher", "global_product_id", "sku_name", "status")
        if values.get(key) is not None and str(values[key]).strip()
    }
    return ProductRecord(
        product_id=product_id,
        seller_sku=seller_sku,
        title=title,
        sku_ids=sku_ids,
        attributes=MappingProxyType(attributes),
    )


def approval_record_from_fact(
    fact: Mapping[str, Any] | Any, *, product_id: str
) -> ApprovalRecord:
    """Build an approval contract only from an explicit, matching approval fact."""
    values = _as_dict(fact)
    approval_id = _required(values, "approval_id")
    subject_type = _required(values, "subject_type")
    subject_id = _required(values, "subject_id")
    status = _required(values, "status")
    if subject_type.casefold() != "product":
        raise ValueError("approval subject_type must be 'product'")
    if subject_id != product_id:
        raise ValueError("approval subject_id must match product_id")
    if status.casefold() != "approved":
        raise ValueError("approval status must be 'approved'")

    approved_at = values.get("approved_at")
    if isinstance(approved_at, str):
        approved_at = approved_at.strip()
        approved_at = (
            datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
            if approved_at else None
        )
    if approved_at is not None and not isinstance(approved_at, datetime):
        raise ValueError("approval approved_at must be a datetime or ISO-8601 string")
    approved_by = str(values.get("approved_by") or "").strip() or None
    return ApprovalRecord(approval_id, "product", product_id, "approved", approved_by, approved_at)


def approved_product_package_from_facts(
    product_row: Mapping[str, Any] | Any, approval_fact: Mapping[str, Any] | Any
) -> ApprovedProductPackage:
    """Assemble a package when the caller provides complete approval facts.

    ``approval_fact`` must contain ``package_id`` in addition to the explicit
    approval fields checked by :func:`approval_record_from_fact`.
    """
    product = product_record_from_legacy_row(product_row)
    fact = _as_dict(approval_fact)
    approval = approval_record_from_fact(fact, product_id=product.product_id)
    package_id = _required(fact, "package_id")
    source_reference = str(fact.get("source_reference") or "").strip() or None
    return ApprovedProductPackage(package_id, product, approval, source_reference)
