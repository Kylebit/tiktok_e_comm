"""Stable, dependency-free contracts exchanged between business domains.

The contracts are immutable snapshots.  They are intentionally smaller than
the current persistence models so legacy tables and API payloads can migrate
behind adapters without changing the public business hand-off.
"""

from __future__ import annotations

from collections.abc import Mapping as MappingABC
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping


@dataclass(frozen=True)
class ProductRecord:
    product_id: str
    seller_sku: str
    title: str
    sku_ids: tuple[str, ...] = ()
    attributes: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ApprovalRecord:
    approval_id: str
    subject_type: str
    subject_id: str
    status: str
    approved_by: str | None = None
    approved_at: datetime | None = None


@dataclass(frozen=True)
class ApprovedProductPackage:
    package_id: str
    product: ProductRecord
    approval: ApprovalRecord
    source_reference: str | None = None


@dataclass(frozen=True)
class ContentPackage:
    package_id: str
    product_id: str
    copy: Mapping[str, str] = field(default_factory=dict)
    image_urls: tuple[str, ...] = ()
    video_urls: tuple[str, ...] = ()
    approval: ApprovalRecord | None = None


@dataclass(frozen=True)
class ChannelListing:
    channel: str
    listing_id: str
    product_package_id: str
    content_package_id: str | None
    status: str
    region: str | None = None


@dataclass(frozen=True)
class InventorySnapshot:
    sku_id: str
    quantity: int
    warehouse: str
    captured_at: datetime
    supplier_id: str | None = None


@dataclass(frozen=True)
class FinancialFact:
    fact_id: str
    fact_type: str
    amount: Decimal
    currency: str
    occurred_at: datetime
    product_id: str | None = None
    channel: str | None = None
    sku_id: str | None = None
    region: str | None = None


def _payload_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _payload_value(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, MappingABC):
        return {str(key): _payload_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_payload_value(item) for item in value]
    return value


def contract_payload(contract: object) -> dict[str, Any]:
    """Return a JSON-ready representation without mutating a contract."""
    payload = _payload_value(contract)
    if not isinstance(payload, dict):
        raise TypeError("contract_payload expects a dataclass contract")
    return payload
