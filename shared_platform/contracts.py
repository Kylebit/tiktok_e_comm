"""Stable, dependency-free contracts exchanged between business domains.

The contracts are immutable snapshots.  They are intentionally smaller than
the current persistence models so legacy tables and API payloads can migrate
behind adapters without changing the public business hand-off.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
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
    amount: float
    currency: str
    occurred_at: datetime
    product_id: str | None = None
    channel: str | None = None


def contract_payload(contract: object) -> dict[str, Any]:
    """Return a serialization-ready representation without mutating a contract."""
    return asdict(contract)
