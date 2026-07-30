"""Pure, fail-closed approval contract for a Shopee global product plan.

This module deliberately has no persistence, HTTP, credential, or marketplace
client dependency.  A channel-owned observer may propose a candidate, but only
an observation from the audited official Open API authority can become
approvable.  Public projections are redacted; raw execution facts are available
only through the explicit server-owned execution seam after drift validation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import hashlib
import itertools
import json
import re
import unicodedata
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from shared_platform.target_scoped_release_contracts import (
    approved_shopee_copy_digest,
    approved_source_image_manifest_digest,
)


CANDIDATE_SCHEMA_VERSION = "shopee-global-plan-candidate/v1"
APPROVED_PLAN_SCHEMA_VERSION = "approved-shopee-global-plan/v1"
APPROVED_EXISTING_PLAN_SCHEMA_VERSION = "approved-shopee-global-plan/v2"
APPROVED_PLAN_RECORD_SCHEMA_VERSION = (
    "approved-shopee-global-plan-record/v1"
)
EXISTING_CURRENT_SNAPSHOT_SCHEMA_VERSION = (
    "approved-shopee-existing-current-snapshot/v1"
)
OFFICIAL_OBSERVATION_SCHEMA_VERSION = (
    "shopee-official-global-plan-observation/v1"
)
OFFICIAL_AUTHORITY = "shopee_official_open_api"
GENERATED_SDK_AUTHORITY = "generated_sdk"
COMMUNITY_AUTHORITY = "community"
INJECTED_UNVERIFIED_AUTHORITY = "injected_unverified"
OBSERVATION_AUTHORITIES = frozenset(
    {
        OFFICIAL_AUTHORITY,
        GENERATED_SDK_AUTHORITY,
        COMMUNITY_AUTHORITY,
        INJECTED_UNVERIFIED_AUTHORITY,
    }
)

READY = "READY"
BLOCKED_CAPABILITY = "BLOCKED_CAPABILITY"
NEW_GLOBAL = "NEW_GLOBAL"
EXISTING_GLOBAL = "EXISTING_GLOBAL"
GLOBAL_PLAN_MODES = frozenset({NEW_GLOBAL, EXISTING_GLOBAL})

SOURCE_IDENTITY_SCHEMA_VERSION = "source-product-identity/v1"
SKU_LINEAGE_SCHEMA_VERSIONS = frozenset(
    {
        "sku-lineage-reservation/v1",
        "new-source-sku-reservation/v1",
    }
)
OFFICIAL_EXISTING_GLOBAL_SELLER_STOCK_SOURCE = (
    "shopee-official-existing-global-seller-stock/v1"
)
SELLER_STOCK_SOURCES = frozenset(
    {
        "approved-sellable-inventory-decision/v1",
        "kyle-explicit-seller-stock/v1",
        OFFICIAL_EXISTING_GLOBAL_SELLER_STOCK_SOURCE,
    }
)
CONDITIONS = frozenset({"NEW", "USED"})
EXISTING_GLOBAL_PERMISSIONS = {
    "reuse_existing_global": True,
    "regional_publish": True,
    "global_create": False,
    "global_update": False,
    "global_model_init": False,
    "global_stock_update": False,
}

_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_CODE_RE = re.compile(r"[a-z0-9][a-z0-9._:/-]{0,127}")
_MODEL_SKU_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class ShopeeGlobalPlanContractError(ValueError):
    """The candidate, approval, or current lineage is not safe to use."""


class ShopeeGlobalPlanApprovalError(ShopeeGlobalPlanContractError):
    """The requested approval is not explicit, current, or approvable."""


class ShopeeGlobalPlanDriftError(ShopeeGlobalPlanContractError):
    """An approved decision no longer matches the current candidate."""


class ShopeeGlobalPlanObservationError(ShopeeGlobalPlanContractError):
    """Redacted failure from the channel-owned read-only observation seam."""

    def __init__(self, *, category: str, code: str) -> None:
        if category not in {"AUTH", "CAPABILITY"}:
            raise ShopeeGlobalPlanContractError(
                "observation failure category is invalid"
            )
        if not _CODE_RE.fullmatch(code):
            raise ShopeeGlobalPlanContractError(
                "observation failure code is invalid"
            )
        super().__init__(code)
        self.category = category
        self.code = code

    def public_projection(self) -> dict[str, Any]:
        return {
            "schema_version": CANDIDATE_SCHEMA_VERSION,
            "status": (
                "BLOCKED_AUTH"
                if self.category == "AUTH"
                else BLOCKED_CAPABILITY
            ),
            "planning_allowed": False,
            "reason_category": self.category,
            "reason_code": self.code,
            "blocker_codes": [self.code],
        }


class _Violation(ShopeeGlobalPlanContractError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class _CategoryNode:
    category_id: int
    name: str

    def payload(self) -> dict[str, Any]:
        return {"category_id": self.category_id, "name": self.name}


@dataclass(frozen=True)
class _Category:
    category_id: int
    path: tuple[_CategoryNode, ...]
    evidence_digest: str

    def payload(self) -> dict[str, Any]:
        return {
            "category_id": self.category_id,
            "path": [row.payload() for row in self.path],
            "path_complete": True,
            "evidence_digest": self.evidence_digest,
        }


@dataclass(frozen=True)
class _AttributeValue:
    value_id: int
    original_value_name: str
    value_unit: str | None

    def payload(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "value_id": self.value_id,
            "original_value_name": self.original_value_name,
        }
        if self.value_unit is not None:
            value["value_unit"] = self.value_unit
        return value


@dataclass(frozen=True)
class _Attribute:
    attribute_id: int
    values: tuple[_AttributeValue, ...]

    def payload(self) -> dict[str, Any]:
        return {
            "attribute_id": self.attribute_id,
            "attribute_value_list": [value.payload() for value in self.values],
        }


@dataclass(frozen=True)
class _Brand:
    brand_id: int
    name: str
    evidence_digest: str

    def payload(self) -> dict[str, Any]:
        return {
            "brand_id": self.brand_id,
            "original_brand_name": self.name,
            "evidence_digest": self.evidence_digest,
        }


@dataclass(frozen=True)
class _ApprovedImage:
    source_url: str
    source_image_digest: str

    def payload(self) -> dict[str, Any]:
        return {
            "source_url": self.source_url,
            "source_image_digest": self.source_image_digest,
        }


@dataclass(frozen=True)
class _Parcel:
    weight_kg: str
    length_cm: str
    width_cm: str
    height_cm: str
    contract_digest: str

    def payload(self) -> dict[str, Any]:
        return {
            "weight_kg": self.weight_kg,
            "package_cm": {
                "length": self.length_cm,
                "width": self.width_cm,
                "height": self.height_cm,
            },
            "contract_digest": self.contract_digest,
        }


@dataclass(frozen=True)
class _SellerStock:
    source: str
    source_digest: str
    quantity: int
    approval_reference: str

    def payload(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_digest": self.source_digest,
            "quantity": self.quantity,
            "approval_reference": self.approval_reference,
        }


@dataclass(frozen=True)
class _Location:
    location_id: str
    evidence_digest: str

    def payload(self) -> dict[str, Any]:
        return {
            "location_id": self.location_id,
            "evidence_digest": self.evidence_digest,
        }


@dataclass(frozen=True)
class _PreOrder:
    is_pre_order: bool
    days_to_ship: int

    def payload(self) -> dict[str, Any]:
        return {
            "is_pre_order": self.is_pre_order,
            "days_to_ship": self.days_to_ship,
        }


@dataclass(frozen=True)
class _VariationOption:
    name: str
    image_position: int | None

    def payload(self) -> dict[str, Any]:
        value: dict[str, Any] = {"option": self.name}
        if self.image_position is not None:
            value["approved_image_position"] = self.image_position
        return value


@dataclass(frozen=True)
class _VariationTier:
    name: str
    options: tuple[_VariationOption, ...]

    def payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "option_list": [option.payload() for option in self.options],
        }


@dataclass(frozen=True)
class _Model:
    model_sku: str
    tier_index: tuple[int, ...]
    original_price_cny: str
    seller_stock_quantity: int

    def payload(self) -> dict[str, Any]:
        return {
            "global_model_sku": self.model_sku,
            "tier_index": list(self.tier_index),
            "original_price_cny": self.original_price_cny,
            "seller_stock_quantity": self.seller_stock_quantity,
        }


@dataclass(frozen=True)
class _ExistingGlobalModel:
    global_model_id: int
    model_sku: str
    tier_index: tuple[int, ...]

    def payload(self) -> dict[str, Any]:
        return {
            "global_model_id": self.global_model_id,
            "global_model_sku": self.model_sku,
            "tier_index": list(self.tier_index),
        }


@dataclass(frozen=True)
class _ExistingSnapshotPlan:
    """Approved current facts for preserve-only reuse of one global item."""

    observation_evidence_digest: str
    source_identity_schema_version: str
    source_identity_digest: str
    sku_lineage_schema_version: str
    sku_lineage_digest: str
    content_package_digest: str
    title: str
    description: str
    approved_copy_digest: str
    approved_images: tuple[_ApprovedImage, ...]
    approved_source_image_manifest_digest: str
    selected_image_positions: tuple[int, ...]
    selected_source_image_manifest_digest: str
    parcel: _Parcel
    target_pricing_digest: str
    global_original_price_cny: str
    policy_digest: str
    existing_global_item_id: int
    existing_global_identity_evidence_digest: str
    category_id: int
    attributes_json: str
    brand_json: str
    seller_stock: _SellerStock
    location: _Location
    condition: str
    preorder: _PreOrder
    tier_variation_json: str
    official_image_ids: tuple[str, ...]
    official_image_url_count: int
    models: tuple[_ExistingGlobalModel, ...]
    current_snapshot_digest: str
    permissions_digest: str

    @property
    def mode(self) -> str:
        return EXISTING_GLOBAL

    def _snapshot_payload(self) -> dict[str, Any]:
        return {
            "schema_version": EXISTING_CURRENT_SNAPSHOT_SCHEMA_VERSION,
            "authority": OFFICIAL_AUTHORITY,
            "observation_schema_version": (
                OFFICIAL_OBSERVATION_SCHEMA_VERSION
            ),
            "observation_evidence_digest": (
                self.observation_evidence_digest
            ),
            "global_item_id": self.existing_global_item_id,
            "existing_global_identity_evidence_digest": (
                self.existing_global_identity_evidence_digest
            ),
            "category_id": self.category_id,
            "attribute_list": json.loads(self.attributes_json),
            "brand": json.loads(self.brand_json),
            "copy": {
                "title": self.title,
                "description": self.description,
                "approved_copy_digest": self.approved_copy_digest,
            },
            "image": {
                "image_id_list": list(self.official_image_ids),
                "image_id_snapshot_digest": _digest(
                    {"ordered_image_ids": self.official_image_ids}
                ),
                "image_url_count": self.official_image_url_count,
                "count_aligned": True,
            },
            "seller_stock": self.seller_stock.payload(),
            "location": self.location.payload(),
            "condition": self.condition,
            "preorder": self.preorder.payload(),
            "tier_variation": json.loads(self.tier_variation_json),
            "global_model": [row.payload() for row in self.models],
        }

    def payload(self) -> dict[str, Any]:
        selected_urls = [
            self.approved_images[position - 1].source_url
            for position in self.selected_image_positions
        ]
        return {
            "mode": EXISTING_GLOBAL,
            "observation_evidence_digest": (
                self.observation_evidence_digest
            ),
            "bindings": {
                "source_identity_schema_version": (
                    self.source_identity_schema_version
                ),
                "source_identity_digest": self.source_identity_digest,
                "sku_lineage_schema_version": (
                    self.sku_lineage_schema_version
                ),
                "sku_lineage_digest": self.sku_lineage_digest,
                "content_package_digest": self.content_package_digest,
                "approved_copy_digest": self.approved_copy_digest,
                "approved_source_image_manifest_digest": (
                    self.approved_source_image_manifest_digest
                ),
                "parcel_contract_digest": self.parcel.contract_digest,
                "target_pricing_digest": self.target_pricing_digest,
                "policy_digest": self.policy_digest,
                "model_sku_set_digest": _digest(
                    sorted(row.model_sku for row in self.models)
                ),
                "current_snapshot_digest": self.current_snapshot_digest,
                "permissions_digest": self.permissions_digest,
            },
            "copy": {
                "title": self.title,
                "description": self.description,
                "approved_copy_digest": self.approved_copy_digest,
            },
            "approved_images": [
                row.payload() for row in self.approved_images
            ],
            "approved_source_image_manifest_digest": (
                self.approved_source_image_manifest_digest
            ),
            "selected_image_positions": list(
                self.selected_image_positions
            ),
            "selected_image_urls": selected_urls,
            "selected_source_image_manifest_digest": (
                self.selected_source_image_manifest_digest
            ),
            "parcel": self.parcel.payload(),
            "pricing": {
                "currency": "CNY",
                "global_original_price": self.global_original_price_cny,
                "target_pricing_digest": self.target_pricing_digest,
            },
            "policy_digest": self.policy_digest,
            "current_snapshot": self._snapshot_payload(),
            "current_snapshot_digest": self.current_snapshot_digest,
            "permissions": dict(EXISTING_GLOBAL_PERMISSIONS),
            "permissions_digest": self.permissions_digest,
        }

    def public_counts(self) -> dict[str, int]:
        return {
            "category_path_depth": 0,
            "attribute_count": len(json.loads(self.attributes_json)),
            "approved_image_count": len(self.approved_images),
            "selected_image_count": len(self.selected_image_positions),
            "variation_tier_count": len(
                json.loads(self.tier_variation_json)
            ),
            "model_count": len(self.models),
        }

    def public_digests(self) -> dict[str, str | None]:
        return {
            "observation_evidence_digest": (
                self.observation_evidence_digest
            ),
            "source_identity_digest": self.source_identity_digest,
            "sku_lineage_digest": self.sku_lineage_digest,
            "content_package_digest": self.content_package_digest,
            "approved_copy_digest": self.approved_copy_digest,
            "approved_source_image_manifest_digest": (
                self.approved_source_image_manifest_digest
            ),
            "selected_source_image_manifest_digest": (
                self.selected_source_image_manifest_digest
            ),
            "parcel_contract_digest": self.parcel.contract_digest,
            "target_pricing_digest": self.target_pricing_digest,
            "policy_digest": self.policy_digest,
            "category_evidence_digest": _digest(
                {"category_id": self.category_id}
            ),
            "attribute_tree_digest": _digest(
                json.loads(self.attributes_json)
            ),
            "brand_evidence_digest": _digest(
                json.loads(self.brand_json)
            ),
            "seller_stock_source_digest": (
                self.seller_stock.source_digest
            ),
            "location_evidence_digest": self.location.evidence_digest,
            "existing_global_identity_digest": _digest(
                {
                    "global_item_id": self.existing_global_item_id,
                    "evidence_digest": (
                        self.existing_global_identity_evidence_digest
                    ),
                }
            ),
        }


@dataclass(frozen=True)
class _Plan:
    mode: str
    observation_evidence_digest: str
    source_identity_schema_version: str
    source_identity_digest: str
    sku_lineage_schema_version: str
    sku_lineage_digest: str
    content_package_digest: str
    title: str
    description: str
    approved_copy_digest: str
    approved_images: tuple[_ApprovedImage, ...]
    approved_source_image_manifest_digest: str
    selected_image_positions: tuple[int, ...]
    selected_source_image_manifest_digest: str
    parcel: _Parcel
    target_pricing_digest: str
    global_original_price_cny: str
    policy_digest: str
    category: _Category
    attributes: tuple[_Attribute, ...]
    attribute_tree_digest: str
    brand: _Brand
    seller_stock: _SellerStock
    location: _Location
    condition: str
    preorder: _PreOrder
    variations: tuple[_VariationTier, ...]
    models: tuple[_Model, ...]
    existing_global_item_id: int | None
    existing_global_identity_evidence_digest: str | None

    def payload(self) -> dict[str, Any]:
        selected_urls = [
            self.approved_images[position - 1].source_url
            for position in self.selected_image_positions
        ]
        return {
            "mode": self.mode,
            "observation_evidence_digest": self.observation_evidence_digest,
            "bindings": {
                "source_identity_schema_version": (
                    self.source_identity_schema_version
                ),
                "source_identity_digest": self.source_identity_digest,
                "sku_lineage_schema_version": self.sku_lineage_schema_version,
                "sku_lineage_digest": self.sku_lineage_digest,
                "content_package_digest": self.content_package_digest,
                "approved_copy_digest": self.approved_copy_digest,
                "approved_source_image_manifest_digest": (
                    self.approved_source_image_manifest_digest
                ),
                "parcel_contract_digest": self.parcel.contract_digest,
                "target_pricing_digest": self.target_pricing_digest,
                "policy_digest": self.policy_digest,
                "attribute_tree_digest": self.attribute_tree_digest,
            },
            "copy": {
                "title": self.title,
                "description": self.description,
                "approved_copy_digest": self.approved_copy_digest,
            },
            "approved_images": [row.payload() for row in self.approved_images],
            "approved_source_image_manifest_digest": (
                self.approved_source_image_manifest_digest
            ),
            "selected_image_positions": list(self.selected_image_positions),
            "selected_image_urls": selected_urls,
            "selected_source_image_manifest_digest": (
                self.selected_source_image_manifest_digest
            ),
            "parcel": self.parcel.payload(),
            "pricing": {
                "currency": "CNY",
                "global_original_price": self.global_original_price_cny,
                "target_pricing_digest": self.target_pricing_digest,
            },
            "policy_digest": self.policy_digest,
            "category": self.category.payload(),
            "attribute_list": [row.payload() for row in self.attributes],
            "attribute_tree_digest": self.attribute_tree_digest,
            "attributes_complete": True,
            "brand": self.brand.payload(),
            "seller_stock": self.seller_stock.payload(),
            "location": self.location.payload(),
            "condition": self.condition,
            "preorder": self.preorder.payload(),
            "tier_variation": [row.payload() for row in self.variations],
            "global_model": [row.payload() for row in self.models],
            "variations_complete": True,
            "existing_global_item_id": self.existing_global_item_id,
            "existing_global_identity_evidence_digest": (
                self.existing_global_identity_evidence_digest
            ),
        }

    def public_counts(self) -> dict[str, int]:
        return {
            "category_path_depth": len(self.category.path),
            "attribute_count": len(self.attributes),
            "approved_image_count": len(self.approved_images),
            "selected_image_count": len(self.selected_image_positions),
            "variation_tier_count": len(self.variations),
            "model_count": len(self.models),
        }

    def public_digests(self) -> dict[str, str | None]:
        existing_identity_digest = None
        if self.existing_global_item_id is not None:
            existing_identity_digest = _digest(
                {
                    "global_item_id": self.existing_global_item_id,
                    "evidence_digest": (
                        self.existing_global_identity_evidence_digest
                    ),
                }
            )
        return {
            "observation_evidence_digest": self.observation_evidence_digest,
            "source_identity_digest": self.source_identity_digest,
            "sku_lineage_digest": self.sku_lineage_digest,
            "content_package_digest": self.content_package_digest,
            "approved_copy_digest": self.approved_copy_digest,
            "approved_source_image_manifest_digest": (
                self.approved_source_image_manifest_digest
            ),
            "selected_source_image_manifest_digest": (
                self.selected_source_image_manifest_digest
            ),
            "parcel_contract_digest": self.parcel.contract_digest,
            "target_pricing_digest": self.target_pricing_digest,
            "policy_digest": self.policy_digest,
            "category_evidence_digest": self.category.evidence_digest,
            "attribute_tree_digest": self.attribute_tree_digest,
            "brand_evidence_digest": self.brand.evidence_digest,
            "seller_stock_source_digest": self.seller_stock.source_digest,
            "location_evidence_digest": self.location.evidence_digest,
            "existing_global_identity_digest": existing_identity_digest,
        }


@dataclass(frozen=True)
class ShopeeGlobalPlanCandidate:
    """Redacted public candidate plus a private immutable execution plan."""

    schema_version: str
    status: str
    planning_allowed: bool
    mode: str | None
    observation_authority: str
    observation_schema_version: str
    observation_evidence_digest: str | None
    blocker_codes: tuple[str, ...]
    candidate_digest: str
    _plan: _Plan | _ExistingSnapshotPlan | None = field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if self.schema_version != CANDIDATE_SCHEMA_VERSION:
            raise ShopeeGlobalPlanContractError("candidate schema is invalid")
        if self.observation_authority not in OBSERVATION_AUTHORITIES:
            raise ShopeeGlobalPlanContractError(
                "candidate observation authority is invalid"
            )
        if (
            not isinstance(self.blocker_codes, tuple)
            or self.blocker_codes != tuple(sorted(set(self.blocker_codes)))
            or any(not _is_code(code) for code in self.blocker_codes)
        ):
            raise ShopeeGlobalPlanContractError("candidate blockers are invalid")
        if self.mode is not None and self.mode not in GLOBAL_PLAN_MODES:
            raise ShopeeGlobalPlanContractError("candidate mode is invalid")
        if self.observation_schema_version not in {
            OFFICIAL_OBSERVATION_SCHEMA_VERSION,
            "unavailable",
        }:
            raise ShopeeGlobalPlanContractError(
                "candidate observation schema projection is invalid"
            )
        if self.status == READY:
            if (
                self.planning_allowed is not True
                or self.mode not in GLOBAL_PLAN_MODES
                or self.observation_authority != OFFICIAL_AUTHORITY
                or self.observation_schema_version
                != OFFICIAL_OBSERVATION_SCHEMA_VERSION
                or not _is_digest(self.observation_evidence_digest)
                or self.blocker_codes
                or type(self._plan)
                not in {_Plan, _ExistingSnapshotPlan}
            ):
                raise ShopeeGlobalPlanContractError(
                    "ready candidate is not authoritative"
                )
            if self._plan.mode != self.mode:
                raise ShopeeGlobalPlanContractError(
                    "candidate mode does not match the private plan"
                )
        elif self.status == BLOCKED_CAPABILITY:
            if (
                self.planning_allowed is not False
                or self._plan is not None
                or not self.blocker_codes
            ):
                raise ShopeeGlobalPlanContractError(
                    "blocked candidate shape is invalid"
                )
        else:
            raise ShopeeGlobalPlanContractError("candidate status is invalid")
        if not _is_digest(self.candidate_digest):
            raise ShopeeGlobalPlanContractError("candidate digest is invalid")
        if self.candidate_digest != _candidate_digest(self):
            raise ShopeeGlobalPlanContractError("candidate digest was forged")

    def public_projection(self) -> dict[str, Any]:
        """Return the complete public candidate without raw copy, URLs, or IDs."""

        counts: dict[str, int] = {}
        digests: dict[str, str | None] = {}
        if self._plan is not None:
            counts = self._plan.public_counts()
            digests = self._plan.public_digests()
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "planning_allowed": self.planning_allowed,
            "mode": self.mode,
            "observation_authority": self.observation_authority,
            "observation_schema_version": self.observation_schema_version,
            "checks": {
                "official_authority_exact": (
                    self.observation_authority == OFFICIAL_AUTHORITY
                ),
                "audited_schema_exact": (
                    self.observation_schema_version
                    == OFFICIAL_OBSERVATION_SCHEMA_VERSION
                ),
                "attributes_complete": self._plan is not None,
                "variations_complete": self._plan is not None,
                "no_default_execution_fact": self._plan is not None,
            },
            "counts": counts,
            "digests": {
                **digests,
                "observation_evidence_digest": (
                    self.observation_evidence_digest
                ),
                "candidate_digest": self.candidate_digest,
            },
            "blocker_codes": list(self.blocker_codes),
        }


@dataclass(frozen=True)
class ApprovedShopeeGlobalPlan:
    """Kyle-approved immutable plan; raw fields remain server-internal."""

    schema_version: str
    approved_by: str
    confirm_approved_shopee_global_plan: bool
    candidate_digest: str
    mode: str
    approved_plan_digest: str
    _plan: _Plan | _ExistingSnapshotPlan = field(
        repr=False, compare=False
    )

    def __post_init__(self) -> None:
        expected_schema = (
            APPROVED_EXISTING_PLAN_SCHEMA_VERSION
            if type(self._plan) is _ExistingSnapshotPlan
            else APPROVED_PLAN_SCHEMA_VERSION
        )
        expected_plan_type = (
            _ExistingSnapshotPlan
            if self.schema_version
            == APPROVED_EXISTING_PLAN_SCHEMA_VERSION
            else _Plan
        )
        if self.schema_version != expected_schema:
            raise ShopeeGlobalPlanContractError("approved plan schema is invalid")
        if type(self.approved_by) is not str or self.approved_by != "Kyle":
            raise ShopeeGlobalPlanContractError("approved actor is invalid")
        if self.confirm_approved_shopee_global_plan is not True:
            raise ShopeeGlobalPlanContractError(
                "literal global-plan consent is required"
            )
        if not _is_digest(self.candidate_digest):
            raise ShopeeGlobalPlanContractError("candidate digest is invalid")
        if (
            self.mode not in GLOBAL_PLAN_MODES
            or type(self._plan) is not expected_plan_type
        ):
            raise ShopeeGlobalPlanContractError("approved plan payload is invalid")
        if self._plan.mode != self.mode:
            raise ShopeeGlobalPlanContractError("approved mode drifted")
        if (
            self.candidate_digest
            != _ready_candidate_from_plan(self._plan).candidate_digest
        ):
            raise ShopeeGlobalPlanContractError(
                "approved candidate digest does not match the raw plan"
            )
        if not _is_digest(self.approved_plan_digest):
            raise ShopeeGlobalPlanContractError(
                "approved plan digest is invalid"
            )
        if self.approved_plan_digest != _approved_plan_digest(self):
            raise ShopeeGlobalPlanContractError(
                "approved plan digest was forged"
            )

    def public_projection(self) -> dict[str, Any]:
        """Return only redacted approval facts and stable digests."""

        return {
            "schema_version": self.schema_version,
            "approved_by": self.approved_by,
            "literal_consent_recorded": True,
            "mode": self.mode,
            "status": "APPROVED",
            "counts": self._plan.public_counts(),
            "digests": {
                **self._plan.public_digests(),
                "candidate_digest": self.candidate_digest,
                "approved_plan_digest": self.approved_plan_digest,
            },
        }

    def server_owned_execution_payload(
        self, current_candidate: ShopeeGlobalPlanCandidate
    ) -> dict[str, Any]:
        """Return raw approved facts only after exact current-candidate proof."""

        validate_approved_shopee_global_plan(self, current_candidate)
        return {
            "schema_version": self.schema_version,
            "approved_by": self.approved_by,
            "candidate_digest": self.candidate_digest,
            "approved_plan_digest": self.approved_plan_digest,
            "plan": self._plan.payload(),
        }


def serialize_approved_shopee_global_plan(
    approved: ApprovedShopeeGlobalPlan,
) -> str:
    """Return the one canonical, server-internal JSON persistence record."""

    if type(approved) is not ApprovedShopeeGlobalPlan:
        raise ShopeeGlobalPlanContractError(
            "approved Shopee global plan contract is invalid"
        )
    # Reconstructing is deliberately part of serialization.  A caller cannot
    # persist an object manufactured with __new__ or mutated outside dataclass
    # construction.
    record = {
        "record_schema_version": APPROVED_PLAN_RECORD_SCHEMA_VERSION,
        "approved_plan": {
            "schema_version": approved.schema_version,
            "approved_by": approved.approved_by,
            "confirm_approved_shopee_global_plan": (
                approved.confirm_approved_shopee_global_plan
            ),
            "candidate_digest": approved.candidate_digest,
            "mode": approved.mode,
            "approved_plan_digest": approved.approved_plan_digest,
            "plan": approved._plan.payload(),
        },
    }
    serialized = _canonical_json(record)
    rehydrated = rehydrate_approved_shopee_global_plan(serialized)
    if (
        rehydrated.candidate_digest != approved.candidate_digest
        or rehydrated.approved_plan_digest != approved.approved_plan_digest
        or rehydrated._plan != approved._plan
    ):
        raise ShopeeGlobalPlanContractError(
            "approved Shopee global plan cannot be persisted safely"
        )
    return serialized


def rehydrate_approved_shopee_global_plan(
    serialized: object,
) -> ApprovedShopeeGlobalPlan:
    """Restore and fully revalidate one canonical internal approval record."""

    if type(serialized) is not str or not serialized:
        raise ShopeeGlobalPlanContractError(
            "approved Shopee global plan record must be canonical JSON"
        )
    try:
        record = json.loads(
            serialized,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ShopeeGlobalPlanContractError(
            "approved Shopee global plan record is invalid JSON"
        ) from error
    if _canonical_json(record) != serialized:
        raise ShopeeGlobalPlanContractError(
            "approved Shopee global plan record is not canonical JSON"
        )
    record = _exact_mapping(
        record,
        required={"record_schema_version", "approved_plan"},
        optional=set(),
        code="approved_plan_record_invalid",
    )
    if record["record_schema_version"] != APPROVED_PLAN_RECORD_SCHEMA_VERSION:
        raise ShopeeGlobalPlanContractError(
            "approved Shopee global plan record schema is invalid"
        )
    stored = _exact_mapping(
        record["approved_plan"],
        required={
            "schema_version",
            "approved_by",
            "confirm_approved_shopee_global_plan",
            "candidate_digest",
            "mode",
            "approved_plan_digest",
            "plan",
        },
        optional=set(),
        code="approved_plan_record_invalid",
    )
    try:
        if (
            stored["schema_version"]
            == APPROVED_EXISTING_PLAN_SCHEMA_VERSION
            and stored["mode"] == EXISTING_GLOBAL
        ):
            plan = _existing_snapshot_plan_from_payload(stored["plan"])
        elif stored["schema_version"] == APPROVED_PLAN_SCHEMA_VERSION:
            plan = _plan_from_payload(stored["plan"])
        else:
            raise _Violation("approved_plan_schema_mode_invalid")
    except _Violation as error:
        raise ShopeeGlobalPlanContractError(
            f"approved Shopee global plan record failed {error.code}"
        ) from error
    candidate = _ready_candidate_from_plan(plan)
    if stored["candidate_digest"] != candidate.candidate_digest:
        raise ShopeeGlobalPlanContractError(
            "persisted candidate digest does not match the raw plan"
        )
    try:
        return ApprovedShopeeGlobalPlan(
            schema_version=stored["schema_version"],
            approved_by=stored["approved_by"],
            confirm_approved_shopee_global_plan=stored[
                "confirm_approved_shopee_global_plan"
            ],
            candidate_digest=stored["candidate_digest"],
            mode=stored["mode"],
            approved_plan_digest=stored["approved_plan_digest"],
            _plan=plan,
        )
    except ShopeeGlobalPlanContractError:
        raise
    except (TypeError, ValueError) as error:
        raise ShopeeGlobalPlanContractError(
            "approved Shopee global plan record identity is invalid"
        ) from error


def build_shopee_global_plan_candidate(
    *,
    mode: object,
    observation_authority: object,
    observation_schema_version: object,
    observation_evidence_digest: object,
    source_identity_schema_version: object,
    source_identity_digest: object,
    sku_lineage_schema_version: object,
    sku_lineage_digest: object,
    content_package_digest: object,
    title: object,
    description: object,
    approved_copy_digest: object,
    ordered_approved_images: object,
    approved_source_image_manifest_digest: object,
    selected_image_positions: object,
    parcel: object,
    target_pricing: object,
    policy_digest: object,
    category: object,
    attributes: object,
    attributes_complete: object,
    attribute_tree_digest: object,
    brand: object,
    seller_stock: object,
    location: object,
    condition: object,
    preorder: object,
    variations: object,
    variations_complete: object,
    models: object,
    existing_global_item_id: object = None,
    existing_global_identity_evidence_digest: object = None,
) -> ShopeeGlobalPlanCandidate:
    """Build a redacted candidate; unsafe authority or shape is non-approvable."""

    normalized_authority = (
        observation_authority
        if type(observation_authority) is str
        and observation_authority in OBSERVATION_AUTHORITIES
        else INJECTED_UNVERIFIED_AUTHORITY
    )
    normalized_schema = (
        OFFICIAL_OBSERVATION_SCHEMA_VERSION
        if observation_schema_version == OFFICIAL_OBSERVATION_SCHEMA_VERSION
        else "unavailable"
    )
    normalized_evidence = (
        observation_evidence_digest
        if _is_digest(observation_evidence_digest)
        else None
    )
    normalized_mode = (
        mode if type(mode) is str and mode in GLOBAL_PLAN_MODES else None
    )

    if normalized_authority != OFFICIAL_AUTHORITY:
        return _blocked_candidate(
            mode=normalized_mode,
            authority=normalized_authority,
            observation_schema_version=normalized_schema,
            observation_evidence_digest=normalized_evidence,
            code=(
                "official_authority_unavailable"
                if normalized_authority in OBSERVATION_AUTHORITIES
                else "observation_authority_invalid"
            ),
        )
    if normalized_schema != OFFICIAL_OBSERVATION_SCHEMA_VERSION:
        return _blocked_candidate(
            mode=normalized_mode,
            authority=normalized_authority,
            observation_schema_version=normalized_schema,
            observation_evidence_digest=normalized_evidence,
            code="audited_schema_unavailable",
        )
    if normalized_evidence is None:
        return _blocked_candidate(
            mode=normalized_mode,
            authority=normalized_authority,
            observation_schema_version=normalized_schema,
            observation_evidence_digest=None,
            code="audited_evidence_unavailable",
        )
    try:
        plan = _normalize_plan(
            mode=mode,
            observation_evidence_digest=observation_evidence_digest,
            source_identity_schema_version=source_identity_schema_version,
            source_identity_digest=source_identity_digest,
            sku_lineage_schema_version=sku_lineage_schema_version,
            sku_lineage_digest=sku_lineage_digest,
            content_package_digest=content_package_digest,
            title=title,
            description=description,
            approved_copy_digest_value=approved_copy_digest,
            ordered_approved_images=ordered_approved_images,
            approved_source_image_manifest_digest_value=(
                approved_source_image_manifest_digest
            ),
            selected_image_positions=selected_image_positions,
            parcel=parcel,
            target_pricing=target_pricing,
            policy_digest=policy_digest,
            category=category,
            attributes=attributes,
            attributes_complete=attributes_complete,
            attribute_tree_digest=attribute_tree_digest,
            brand=brand,
            seller_stock=seller_stock,
            location=location,
            condition=condition,
            preorder=preorder,
            variations=variations,
            variations_complete=variations_complete,
            models=models,
            existing_global_item_id=existing_global_item_id,
            existing_global_identity_evidence_digest=(
                existing_global_identity_evidence_digest
            ),
        )
    except _Violation as error:
        return _blocked_candidate(
            mode=normalized_mode,
            authority=normalized_authority,
            observation_schema_version=normalized_schema,
            observation_evidence_digest=normalized_evidence,
            code=error.code,
        )
    except (InvalidOperation, KeyError, TypeError, ValueError, OverflowError):
        return _blocked_candidate(
            mode=normalized_mode,
            authority=normalized_authority,
            observation_schema_version=normalized_schema,
            observation_evidence_digest=normalized_evidence,
            code="candidate_shape_invalid",
        )

    return _ready_candidate_from_plan(plan)


def build_shopee_official_existing_global_seller_stock(
    *,
    observation_evidence_digest: object,
    existing_global_item_id: object,
    existing_global_identity_evidence_digest: object,
    seller_stock_rows: object,
) -> dict[str, Any]:
    """Build the official-current stock/location binding accepted by the plan.

    This is a preservation fact for an already-existing Shopee global item.
    It is not a physical inventory decision and it never authorizes a stock
    mutation.  The returned digest binds the official observation, item
    identity, and item-level seller-stock row so a caller cannot relabel an
    arbitrary quantity as an official-current fact.  The v1 plan can represent
    exactly one official seller location; multiple official rows fail closed
    rather than being merged or selected implicitly.
    """

    try:
        return _official_existing_global_seller_stock_binding(
            observation_evidence_digest=observation_evidence_digest,
            existing_global_item_id=existing_global_item_id,
            existing_global_identity_evidence_digest=(
                existing_global_identity_evidence_digest
            ),
            seller_stock_rows=seller_stock_rows,
        )
    except _Violation as error:
        raise ShopeeGlobalPlanContractError(
            f"official existing-global seller stock failed {error.code}"
        ) from error


def build_shopee_existing_current_snapshot_candidate(
    *,
    observation_authority: object,
    observation_schema_version: object,
    observation_evidence_digest: object,
    source_identity_schema_version: object,
    source_identity_digest: object,
    sku_lineage_schema_version: object,
    sku_lineage_digest: object,
    content_package_digest: object,
    title: object,
    description: object,
    approved_copy_digest: object,
    ordered_approved_images: object,
    approved_source_image_manifest_digest: object,
    selected_image_positions: object,
    parcel: object,
    target_pricing: object,
    policy_digest: object,
    expected_model_skus: object,
    existing_global_item: object,
    existing_global_models: object,
    existing_global_identity_evidence_digest: object,
) -> ShopeeGlobalPlanCandidate:
    """Build the preserve-only v2 candidate for one official existing item.

    Unlike NEW_GLOBAL, this contract does not require or synthesize an
    approved category path, attribute tree, brand decision, variation design,
    stock decision, or global-create body.  It binds the current official item
    snapshot and explicitly forbids every global mutation.  Only reuse of the
    exact global identity and later regional publication are authorized.
    """

    authority = (
        observation_authority
        if type(observation_authority) is str
        and observation_authority in OBSERVATION_AUTHORITIES
        else INJECTED_UNVERIFIED_AUTHORITY
    )
    schema = (
        OFFICIAL_OBSERVATION_SCHEMA_VERSION
        if observation_schema_version == OFFICIAL_OBSERVATION_SCHEMA_VERSION
        else "unavailable"
    )
    evidence = (
        observation_evidence_digest
        if _is_digest(observation_evidence_digest)
        else None
    )
    if authority != OFFICIAL_AUTHORITY:
        return _blocked_candidate(
            mode=EXISTING_GLOBAL,
            authority=authority,
            observation_schema_version=schema,
            observation_evidence_digest=evidence,
            code="official_authority_unavailable",
        )
    if schema != OFFICIAL_OBSERVATION_SCHEMA_VERSION:
        return _blocked_candidate(
            mode=EXISTING_GLOBAL,
            authority=authority,
            observation_schema_version=schema,
            observation_evidence_digest=evidence,
            code="audited_schema_unavailable",
        )
    if evidence is None:
        return _blocked_candidate(
            mode=EXISTING_GLOBAL,
            authority=authority,
            observation_schema_version=schema,
            observation_evidence_digest=None,
            code="audited_evidence_unavailable",
        )
    try:
        plan = _normalize_existing_snapshot_plan(
            observation_evidence_digest=evidence,
            source_identity_schema_version=(
                source_identity_schema_version
            ),
            source_identity_digest=source_identity_digest,
            sku_lineage_schema_version=sku_lineage_schema_version,
            sku_lineage_digest=sku_lineage_digest,
            content_package_digest=content_package_digest,
            title=title,
            description=description,
            approved_copy_digest_value=approved_copy_digest,
            ordered_approved_images=ordered_approved_images,
            approved_source_image_manifest_digest_value=(
                approved_source_image_manifest_digest
            ),
            selected_image_positions=selected_image_positions,
            parcel=parcel,
            target_pricing=target_pricing,
            policy_digest=policy_digest,
            expected_model_skus=expected_model_skus,
            existing_global_item=existing_global_item,
            existing_global_models=existing_global_models,
            existing_global_identity_evidence_digest=(
                existing_global_identity_evidence_digest
            ),
        )
    except _Violation as error:
        return _blocked_candidate(
            mode=EXISTING_GLOBAL,
            authority=authority,
            observation_schema_version=schema,
            observation_evidence_digest=evidence,
            code=error.code,
        )
    except (InvalidOperation, KeyError, TypeError, ValueError, OverflowError):
        return _blocked_candidate(
            mode=EXISTING_GLOBAL,
            authority=authority,
            observation_schema_version=schema,
            observation_evidence_digest=evidence,
            code="candidate_shape_invalid",
        )
    return _ready_candidate_from_plan(plan)


def approve_shopee_global_plan(
    candidate: ShopeeGlobalPlanCandidate,
    *,
    approved_by: object,
    confirm_approved_shopee_global_plan: object,
    expected_candidate_digest: object,
) -> ApprovedShopeeGlobalPlan:
    """Record Kyle's exact approval of one current, authoritative candidate."""

    if type(candidate) is not ShopeeGlobalPlanCandidate:
        raise ShopeeGlobalPlanApprovalError("candidate contract is invalid")
    if candidate.status != READY or candidate.planning_allowed is not True:
        raise ShopeeGlobalPlanApprovalError("candidate is not approvable")
    if type(approved_by) is not str or approved_by != "Kyle":
        raise ShopeeGlobalPlanApprovalError("approved_by must be exactly Kyle")
    if confirm_approved_shopee_global_plan is not True:
        raise ShopeeGlobalPlanApprovalError(
            "literal confirm_approved_shopee_global_plan=true is required"
        )
    if (
        not _is_digest(expected_candidate_digest)
        or expected_candidate_digest != candidate.candidate_digest
    ):
        raise ShopeeGlobalPlanApprovalError("candidate digest is stale")
    if type(candidate._plan) not in {_Plan, _ExistingSnapshotPlan}:
        raise ShopeeGlobalPlanApprovalError("candidate plan is unavailable")

    provisional = ApprovedShopeeGlobalPlan.__new__(ApprovedShopeeGlobalPlan)
    values = {
        "schema_version": (
            APPROVED_EXISTING_PLAN_SCHEMA_VERSION
            if type(candidate._plan) is _ExistingSnapshotPlan
            else APPROVED_PLAN_SCHEMA_VERSION
        ),
        "approved_by": "Kyle",
        "confirm_approved_shopee_global_plan": True,
        "candidate_digest": candidate.candidate_digest,
        "mode": candidate.mode,
        "approved_plan_digest": "0" * 64,
        "_plan": candidate._plan,
    }
    for name, value in values.items():
        object.__setattr__(provisional, name, value)
    values["approved_plan_digest"] = _approved_plan_digest(provisional)
    return ApprovedShopeeGlobalPlan(**values)


def validate_approved_shopee_global_plan(
    approved: ApprovedShopeeGlobalPlan,
    current_candidate: ShopeeGlobalPlanCandidate,
) -> None:
    """Reject any candidate, policy, content, or lineage drift before use."""

    if type(approved) is not ApprovedShopeeGlobalPlan:
        raise ShopeeGlobalPlanDriftError("approved plan contract is invalid")
    if type(current_candidate) is not ShopeeGlobalPlanCandidate:
        raise ShopeeGlobalPlanDriftError("current candidate contract is invalid")
    if (
        current_candidate.status != READY
        or current_candidate.planning_allowed is not True
        or current_candidate.candidate_digest != approved.candidate_digest
        or current_candidate.mode != approved.mode
        or current_candidate._plan != approved._plan
    ):
        raise ShopeeGlobalPlanDriftError(
            "approved Shopee global plan no longer matches current facts"
        )


def _ready_candidate_from_plan(
    plan: _Plan | _ExistingSnapshotPlan,
) -> ShopeeGlobalPlanCandidate:
    if type(plan) not in {_Plan, _ExistingSnapshotPlan}:
        raise ShopeeGlobalPlanContractError(
            "Shopee global plan payload is invalid"
        )
    provisional = ShopeeGlobalPlanCandidate.__new__(ShopeeGlobalPlanCandidate)
    values = {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "status": READY,
        "planning_allowed": True,
        "mode": plan.mode,
        "observation_authority": OFFICIAL_AUTHORITY,
        "observation_schema_version": OFFICIAL_OBSERVATION_SCHEMA_VERSION,
        "observation_evidence_digest": plan.observation_evidence_digest,
        "blocker_codes": (),
        "candidate_digest": "0" * 64,
        "_plan": plan,
    }
    for name, value in values.items():
        object.__setattr__(provisional, name, value)
    values["candidate_digest"] = _candidate_digest(provisional)
    return ShopeeGlobalPlanCandidate(**values)


def _plan_from_payload(value: object) -> _Plan:
    payload = _exact_mapping(
        value,
        required={
            "mode",
            "observation_evidence_digest",
            "bindings",
            "copy",
            "approved_images",
            "approved_source_image_manifest_digest",
            "selected_image_positions",
            "selected_image_urls",
            "selected_source_image_manifest_digest",
            "parcel",
            "pricing",
            "policy_digest",
            "category",
            "attribute_list",
            "attribute_tree_digest",
            "attributes_complete",
            "brand",
            "seller_stock",
            "location",
            "condition",
            "preorder",
            "tier_variation",
            "global_model",
            "variations_complete",
            "existing_global_item_id",
            "existing_global_identity_evidence_digest",
        },
        optional=set(),
        code="serialized_plan_shape_invalid",
    )
    bindings = _exact_mapping(
        payload["bindings"],
        required={
            "source_identity_schema_version",
            "source_identity_digest",
            "sku_lineage_schema_version",
            "sku_lineage_digest",
            "content_package_digest",
            "approved_copy_digest",
            "approved_source_image_manifest_digest",
            "parcel_contract_digest",
            "target_pricing_digest",
            "policy_digest",
            "attribute_tree_digest",
        },
        optional=set(),
        code="serialized_plan_bindings_invalid",
    )
    copy = _exact_mapping(
        payload["copy"],
        required={"title", "description", "approved_copy_digest"},
        optional=set(),
        code="serialized_plan_copy_invalid",
    )
    parcel = _exact_mapping(
        payload["parcel"],
        required={"weight_kg", "package_cm", "contract_digest"},
        optional=set(),
        code="serialized_plan_parcel_invalid",
    )
    package_cm = _exact_mapping(
        parcel["package_cm"],
        required={"length", "width", "height"},
        optional=set(),
        code="serialized_plan_parcel_invalid",
    )
    pricing = _exact_mapping(
        payload["pricing"],
        required={
            "currency",
            "global_original_price",
            "target_pricing_digest",
        },
        optional=set(),
        code="serialized_plan_pricing_invalid",
    )
    plan = _normalize_plan(
        mode=payload["mode"],
        observation_evidence_digest=payload["observation_evidence_digest"],
        source_identity_schema_version=bindings[
            "source_identity_schema_version"
        ],
        source_identity_digest=bindings["source_identity_digest"],
        sku_lineage_schema_version=bindings["sku_lineage_schema_version"],
        sku_lineage_digest=bindings["sku_lineage_digest"],
        content_package_digest=bindings["content_package_digest"],
        title=copy["title"],
        description=copy["description"],
        approved_copy_digest_value=copy["approved_copy_digest"],
        ordered_approved_images=payload["approved_images"],
        approved_source_image_manifest_digest_value=payload[
            "approved_source_image_manifest_digest"
        ],
        selected_image_positions=payload["selected_image_positions"],
        parcel={
            "weight_kg": parcel["weight_kg"],
            "length_cm": package_cm["length"],
            "width_cm": package_cm["width"],
            "height_cm": package_cm["height"],
            "contract_digest": parcel["contract_digest"],
        },
        target_pricing={
            "currency": pricing["currency"],
            "global_original_price": pricing["global_original_price"],
            "contract_digest": pricing["target_pricing_digest"],
        },
        policy_digest=payload["policy_digest"],
        category=payload["category"],
        attributes=payload["attribute_list"],
        attributes_complete=payload["attributes_complete"],
        attribute_tree_digest=payload["attribute_tree_digest"],
        brand=payload["brand"],
        seller_stock=payload["seller_stock"],
        location=payload["location"],
        condition=payload["condition"],
        preorder=payload["preorder"],
        variations=payload["tier_variation"],
        variations_complete=payload["variations_complete"],
        models=payload["global_model"],
        existing_global_item_id=payload["existing_global_item_id"],
        existing_global_identity_evidence_digest=payload[
            "existing_global_identity_evidence_digest"
        ],
    )
    # Every duplicated binding/derived field in the record must agree exactly,
    # including JSON types.  Python's ``True == 1`` must never validate a
    # persisted identity.
    if _canonical_json(plan.payload()) != _canonical_json(payload):
        raise _Violation("serialized_plan_derived_field_mismatch")
    return plan


def _normalize_existing_snapshot_plan(**raw: Any) -> _ExistingSnapshotPlan:
    observation_digest = _required_digest(
        raw["observation_evidence_digest"],
        "audited_evidence_unavailable",
    )
    source_schema = _required_string(
        raw["source_identity_schema_version"], "source_identity_invalid"
    )
    if source_schema != SOURCE_IDENTITY_SCHEMA_VERSION:
        raise _Violation("source_identity_invalid")
    source_digest = _required_digest(
        raw["source_identity_digest"], "source_identity_invalid"
    )
    lineage_schema = _required_string(
        raw["sku_lineage_schema_version"], "sku_lineage_invalid"
    )
    if lineage_schema not in SKU_LINEAGE_SCHEMA_VERSIONS:
        raise _Violation("sku_lineage_invalid")
    lineage_digest = _required_digest(
        raw["sku_lineage_digest"], "sku_lineage_invalid"
    )
    content_digest = _required_digest(
        raw["content_package_digest"], "content_binding_invalid"
    )
    if type(raw["title"]) is not str or type(raw["description"]) is not str:
        raise _Violation("approved_copy_invalid")
    title = unicodedata.normalize("NFC", raw["title"].strip())
    description = raw["description"]
    if (
        not title
        or len(title) > 120
        or not description.strip()
        or len(description) > 3000
    ):
        raise _Violation("approved_copy_invalid")
    copy_digest = _required_digest(
        raw["approved_copy_digest_value"], "approved_copy_invalid"
    )
    if copy_digest != approved_shopee_copy_digest(title, description):
        raise _Violation("approved_copy_digest_mismatch")

    images = _normalize_images(raw["ordered_approved_images"])
    source_manifest_digest = _required_digest(
        raw["approved_source_image_manifest_digest_value"],
        "approved_image_manifest_invalid",
    )
    if source_manifest_digest != approved_source_image_manifest_digest(
        [image.source_url for image in images]
    ):
        raise _Violation("approved_image_manifest_digest_mismatch")
    positions = _normalize_selected_positions(
        raw["selected_image_positions"], len(images)
    )
    selected_manifest_digest = approved_source_image_manifest_digest(
        [images[position - 1].source_url for position in positions]
    )
    parcel = _normalize_parcel(raw["parcel"])
    pricing_digest, global_price = _normalize_target_pricing(
        raw["target_pricing"]
    )
    policy_digest = _required_digest(
        raw["policy_digest"], "policy_digest_invalid"
    )

    expected_rows = _required_list(
        raw["expected_model_skus"], "existing_global_models_invalid"
    )
    if (
        not expected_rows
        or any(
            type(value) is not str
            or not _MODEL_SKU_RE.fullmatch(value)
            for value in expected_rows
        )
        or len(expected_rows) != len(set(expected_rows))
    ):
        raise _Violation("existing_global_models_invalid")

    item = _exact_mapping(
        raw["existing_global_item"],
        required={
            "global_item_id",
            "global_item_name",
            "description",
            "image",
            "category_id",
            "attribute_list",
            "brand",
            "seller_stock",
            "condition",
            "pre_order",
            "tier_variation",
        },
        optional=set(),
        code="existing_current_snapshot_invalid",
    )
    item_id = _positive_int(
        item["global_item_id"], "existing_global_identity_invalid"
    )
    identity_digest = _required_digest(
        raw["existing_global_identity_evidence_digest"],
        "existing_global_identity_invalid",
    )
    official_title = item["global_item_name"]
    official_description = item["description"]
    if (
        type(official_title) is not str
        or type(official_description) is not str
        or approved_shopee_copy_digest(
            official_title, official_description
        )
        != copy_digest
    ):
        raise _Violation("existing_global_copy_drift")

    image = _exact_mapping(
        item["image"],
        required={"image_url_list", "image_id_list"},
        optional=set(),
        code="existing_global_images_invalid",
    )
    image_urls = _required_list(
        image["image_url_list"], "existing_global_images_invalid"
    )
    image_ids = _required_list(
        image["image_id_list"], "existing_global_images_invalid"
    )
    if (
        not image_urls
        or len(image_urls) != len(image_ids)
        or len(image_ids) != len(positions)
        or any(
            type(value) is not str or not value.strip()
            for value in image_ids
        )
        or len(image_ids) != len(set(image_ids))
        or any(
            type(value) is not str
            or not value.startswith("https://")
            for value in image_urls
        )
        or len(image_urls) != len(set(image_urls))
    ):
        raise _Violation("existing_global_images_invalid")

    category_id = _positive_int(
        item["category_id"], "existing_global_category_invalid"
    )
    attributes = _normalize_attributes(item["attribute_list"])
    attributes_json = _canonical_json(
        [attribute.payload() for attribute in attributes]
    )
    brand_value = item["brand"]
    if brand_value is None:
        brand_json = "null"
    else:
        brand = _exact_mapping(
            brand_value,
            required={"brand_id", "original_brand_name"},
            optional=set(),
            code="existing_global_brand_invalid",
        )
        brand_json = _canonical_json(
            {
                "brand_id": _nonnegative_int(
                    brand["brand_id"], "existing_global_brand_invalid"
                ),
                "original_brand_name": _required_string(
                    brand["original_brand_name"],
                    "existing_global_brand_invalid",
                ),
            }
        )
    condition = _required_string(
        item["condition"], "condition_invalid"
    )
    if condition not in CONDITIONS:
        raise _Violation("condition_invalid")
    preorder = _normalize_preorder(item["pre_order"])
    tier_variation_json = _normalize_existing_tier_variation(
        item["tier_variation"]
    )

    model_rows = _required_list(
        raw["existing_global_models"], "existing_global_models_invalid"
    )
    if not model_rows:
        raise _Violation("existing_global_models_invalid")
    models: list[_ExistingGlobalModel] = []
    seen_ids: set[int] = set()
    seen_skus: set[str] = set()
    for raw_model in model_rows:
        model = _exact_mapping(
            raw_model,
            required={
                "global_model_id",
                "global_model_sku",
                "tier_index",
            },
            optional=set(),
            code="existing_global_models_invalid",
        )
        model_id = _positive_int(
            model["global_model_id"], "existing_global_models_invalid"
        )
        model_sku = _required_string(
            model["global_model_sku"], "existing_global_models_invalid"
        )
        tier = _required_list(
            model["tier_index"], "existing_global_models_invalid"
        )
        if (
            not _MODEL_SKU_RE.fullmatch(model_sku)
            or model_id in seen_ids
            or model_sku in seen_skus
            or not tier
            or any(type(value) is not int or value < 0 for value in tier)
        ):
            raise _Violation("existing_global_models_invalid")
        seen_ids.add(model_id)
        seen_skus.add(model_sku)
        models.append(
            _ExistingGlobalModel(model_id, model_sku, tuple(tier))
        )
    if sorted(seen_skus) != sorted(expected_rows):
        raise _Violation("existing_global_model_set_drift")

    stock_binding = _official_existing_global_seller_stock_binding(
        observation_evidence_digest=observation_digest,
        existing_global_item_id=item_id,
        existing_global_identity_evidence_digest=identity_digest,
        seller_stock_rows=item["seller_stock"],
    )
    stock = _normalize_stock(stock_binding["seller_stock"])
    location = _normalize_location(stock_binding["location"])
    permissions_digest = _digest(EXISTING_GLOBAL_PERMISSIONS)
    provisional = _ExistingSnapshotPlan(
        observation_evidence_digest=observation_digest,
        source_identity_schema_version=source_schema,
        source_identity_digest=source_digest,
        sku_lineage_schema_version=lineage_schema,
        sku_lineage_digest=lineage_digest,
        content_package_digest=content_digest,
        title=title,
        description=description,
        approved_copy_digest=copy_digest,
        approved_images=images,
        approved_source_image_manifest_digest=source_manifest_digest,
        selected_image_positions=positions,
        selected_source_image_manifest_digest=selected_manifest_digest,
        parcel=parcel,
        target_pricing_digest=pricing_digest,
        global_original_price_cny=global_price,
        policy_digest=policy_digest,
        existing_global_item_id=item_id,
        existing_global_identity_evidence_digest=identity_digest,
        category_id=category_id,
        attributes_json=attributes_json,
        brand_json=brand_json,
        seller_stock=stock,
        location=location,
        condition=condition,
        preorder=preorder,
        tier_variation_json=tier_variation_json,
        official_image_ids=tuple(image_ids),
        official_image_url_count=len(image_urls),
        models=tuple(models),
        current_snapshot_digest="0" * 64,
        permissions_digest=permissions_digest,
    )
    snapshot_digest = _digest(provisional._snapshot_payload())
    return _ExistingSnapshotPlan(
        **{
            **provisional.__dict__,
            "current_snapshot_digest": snapshot_digest,
        }
    )


def _normalize_existing_tier_variation(value: object) -> str:
    rows = _required_list(value, "existing_global_variation_invalid")
    if not rows:
        raise _Violation("existing_global_variation_invalid")
    normalized: list[dict[str, Any]] = []
    for raw_row in rows:
        row = _exact_mapping(
            raw_row,
            required={"name", "option_list"},
            optional=set(),
            code="existing_global_variation_invalid",
        )
        options = _required_list(
            row["option_list"], "existing_global_variation_invalid"
        )
        if not options:
            raise _Violation("existing_global_variation_invalid")
        normalized_options: list[dict[str, str]] = []
        seen: set[str] = set()
        for raw_option in options:
            option = _exact_mapping(
                raw_option,
                required={"option"},
                optional=set(),
                code="existing_global_variation_invalid",
            )
            name = _required_string(
                option["option"], "existing_global_variation_invalid"
            )
            if name in seen:
                raise _Violation("existing_global_variation_invalid")
            seen.add(name)
            normalized_options.append({"option": name})
        normalized.append(
            {
                "name": _required_string(
                    row["name"], "existing_global_variation_invalid"
                ),
                "option_list": normalized_options,
            }
        )
    return _canonical_json(normalized)


def _existing_snapshot_plan_from_payload(
    value: object,
) -> _ExistingSnapshotPlan:
    payload = _exact_mapping(
        value,
        required={
            "mode",
            "observation_evidence_digest",
            "bindings",
            "copy",
            "approved_images",
            "approved_source_image_manifest_digest",
            "selected_image_positions",
            "selected_image_urls",
            "selected_source_image_manifest_digest",
            "parcel",
            "pricing",
            "policy_digest",
            "current_snapshot",
            "current_snapshot_digest",
            "permissions",
            "permissions_digest",
        },
        optional=set(),
        code="serialized_existing_plan_shape_invalid",
    )
    if payload["mode"] != EXISTING_GLOBAL:
        raise _Violation("serialized_existing_plan_shape_invalid")
    permissions = _exact_mapping(
        payload["permissions"],
        required=set(EXISTING_GLOBAL_PERMISSIONS),
        optional=set(),
        code="existing_global_permissions_invalid",
    )
    if (
        dict(permissions) != EXISTING_GLOBAL_PERMISSIONS
        or payload["permissions_digest"] != _digest(
            EXISTING_GLOBAL_PERMISSIONS
        )
    ):
        raise _Violation("existing_global_permissions_invalid")
    bindings = _exact_mapping(
        payload["bindings"],
        required={
            "source_identity_schema_version",
            "source_identity_digest",
            "sku_lineage_schema_version",
            "sku_lineage_digest",
            "content_package_digest",
            "approved_copy_digest",
            "approved_source_image_manifest_digest",
            "parcel_contract_digest",
            "target_pricing_digest",
            "policy_digest",
            "model_sku_set_digest",
            "current_snapshot_digest",
            "permissions_digest",
        },
        optional=set(),
        code="serialized_existing_plan_bindings_invalid",
    )
    snapshot = _exact_mapping(
        payload["current_snapshot"],
        required={
            "schema_version",
            "authority",
            "observation_schema_version",
            "observation_evidence_digest",
            "global_item_id",
            "existing_global_identity_evidence_digest",
            "category_id",
            "attribute_list",
            "brand",
            "copy",
            "image",
            "seller_stock",
            "location",
            "condition",
            "preorder",
            "tier_variation",
            "global_model",
        },
        optional=set(),
        code="serialized_existing_snapshot_invalid",
    )
    if (
        snapshot["schema_version"]
        != EXISTING_CURRENT_SNAPSHOT_SCHEMA_VERSION
        or snapshot["authority"] != OFFICIAL_AUTHORITY
        or snapshot["observation_schema_version"]
        != OFFICIAL_OBSERVATION_SCHEMA_VERSION
    ):
        raise _Violation("serialized_existing_snapshot_invalid")
    copy = _exact_mapping(
        payload["copy"],
        required={"title", "description", "approved_copy_digest"},
        optional=set(),
        code="serialized_existing_plan_copy_invalid",
    )
    snapshot_copy = _exact_mapping(
        snapshot["copy"],
        required={"title", "description", "approved_copy_digest"},
        optional=set(),
        code="serialized_existing_snapshot_invalid",
    )
    if dict(copy) != dict(snapshot_copy):
        raise _Violation("serialized_existing_snapshot_invalid")
    parcel = _exact_mapping(
        payload["parcel"],
        required={"weight_kg", "package_cm", "contract_digest"},
        optional=set(),
        code="serialized_existing_plan_parcel_invalid",
    )
    package = _exact_mapping(
        parcel["package_cm"],
        required={"length", "width", "height"},
        optional=set(),
        code="serialized_existing_plan_parcel_invalid",
    )
    pricing = _exact_mapping(
        payload["pricing"],
        required={
            "currency",
            "global_original_price",
            "target_pricing_digest",
        },
        optional=set(),
        code="serialized_existing_plan_pricing_invalid",
    )
    image = _exact_mapping(
        snapshot["image"],
        required={
            "image_id_list",
            "image_id_snapshot_digest",
            "image_url_count",
            "count_aligned",
        },
        optional=set(),
        code="serialized_existing_snapshot_invalid",
    )
    # Rebuild through the one authoritative normalizer.  Persisted official
    # URLs are intentionally absent, so use deterministic private sentinels
    # only for count/shape reconstruction; they are never returned or stored.
    selected_positions = payload["selected_image_positions"]
    if not isinstance(selected_positions, list):
        raise _Violation("serialized_existing_plan_shape_invalid")
    synthetic_urls = [
        f"https://redacted.invalid/{index}"
        for index in range(1, len(selected_positions) + 1)
    ]
    plan = _normalize_existing_snapshot_plan(
        observation_evidence_digest=payload[
            "observation_evidence_digest"
        ],
        source_identity_schema_version=bindings[
            "source_identity_schema_version"
        ],
        source_identity_digest=bindings["source_identity_digest"],
        sku_lineage_schema_version=bindings[
            "sku_lineage_schema_version"
        ],
        sku_lineage_digest=bindings["sku_lineage_digest"],
        content_package_digest=bindings["content_package_digest"],
        title=copy["title"],
        description=copy["description"],
        approved_copy_digest_value=copy["approved_copy_digest"],
        ordered_approved_images=payload["approved_images"],
        approved_source_image_manifest_digest_value=payload[
            "approved_source_image_manifest_digest"
        ],
        selected_image_positions=selected_positions,
        parcel={
            "weight_kg": parcel["weight_kg"],
            "length_cm": package["length"],
            "width_cm": package["width"],
            "height_cm": package["height"],
            "contract_digest": parcel["contract_digest"],
        },
        target_pricing={
            "currency": pricing["currency"],
            "global_original_price": pricing["global_original_price"],
            "contract_digest": pricing["target_pricing_digest"],
        },
        policy_digest=payload["policy_digest"],
        expected_model_skus=[
            row.get("global_model_sku")
            for row in snapshot["global_model"]
            if isinstance(row, Mapping)
        ],
        existing_global_item={
            "global_item_id": snapshot["global_item_id"],
            "global_item_name": snapshot_copy["title"],
            "description": snapshot_copy["description"],
            "image": {
                "image_url_list": synthetic_urls,
                "image_id_list": image["image_id_list"],
            },
            "category_id": snapshot["category_id"],
            "attribute_list": snapshot["attribute_list"],
            "brand": snapshot["brand"],
            "seller_stock": [
                {
                    "location_id": _exact_mapping(
                        snapshot["location"],
                        required={"location_id", "evidence_digest"},
                        optional=set(),
                        code="serialized_existing_snapshot_invalid",
                    )["location_id"],
                    "stock": _exact_mapping(
                        snapshot["seller_stock"],
                        required={
                            "source",
                            "source_digest",
                            "quantity",
                            "approval_reference",
                        },
                        optional=set(),
                        code="serialized_existing_snapshot_invalid",
                    )["quantity"],
                }
            ],
            "condition": snapshot["condition"],
            "pre_order": snapshot["preorder"],
            "tier_variation": snapshot["tier_variation"],
        },
        existing_global_models=snapshot["global_model"],
        existing_global_identity_evidence_digest=snapshot[
            "existing_global_identity_evidence_digest"
        ],
    )
    # Every derived field and duplicate binding must match byte-for-byte.
    if _canonical_json(plan.payload()) != _canonical_json(payload):
        raise _Violation("serialized_existing_plan_derived_field_mismatch")
    return plan


def _normalize_plan(**raw: Any) -> _Plan:
    mode = raw["mode"]
    if type(mode) is not str or mode not in GLOBAL_PLAN_MODES:
        raise _Violation("mode_invalid")
    observation_evidence_digest = _required_digest(
        raw["observation_evidence_digest"], "audited_evidence_unavailable"
    )
    source_identity_schema = _required_string(
        raw["source_identity_schema_version"], "source_identity_invalid"
    )
    if source_identity_schema != SOURCE_IDENTITY_SCHEMA_VERSION:
        raise _Violation("source_identity_invalid")
    source_identity_digest = _required_digest(
        raw["source_identity_digest"], "source_identity_invalid"
    )
    sku_lineage_schema = _required_string(
        raw["sku_lineage_schema_version"], "sku_lineage_invalid"
    )
    if sku_lineage_schema not in SKU_LINEAGE_SCHEMA_VERSIONS:
        raise _Violation("sku_lineage_invalid")
    sku_lineage_digest = _required_digest(
        raw["sku_lineage_digest"], "sku_lineage_invalid"
    )
    content_package_digest = _required_digest(
        raw["content_package_digest"], "content_binding_invalid"
    )

    if type(raw["title"]) is not str or type(raw["description"]) is not str:
        raise _Violation("approved_copy_invalid")
    title = unicodedata.normalize("NFC", raw["title"].strip())
    description = raw["description"]
    if (
        not title
        or len(title) > 120
        or not description.strip()
        or len(description) > 3000
    ):
        raise _Violation("approved_copy_invalid")
    supplied_copy_digest = _required_digest(
        raw["approved_copy_digest_value"], "approved_copy_invalid"
    )
    if supplied_copy_digest != approved_shopee_copy_digest(title, description):
        raise _Violation("approved_copy_digest_mismatch")

    images = _normalize_images(raw["ordered_approved_images"])
    supplied_manifest_digest = _required_digest(
        raw["approved_source_image_manifest_digest_value"],
        "approved_image_manifest_invalid",
    )
    image_urls = [image.source_url for image in images]
    if supplied_manifest_digest != approved_source_image_manifest_digest(
        image_urls
    ):
        raise _Violation("approved_image_manifest_digest_mismatch")
    positions = _normalize_selected_positions(
        raw["selected_image_positions"], len(images)
    )
    selected_manifest_digest = approved_source_image_manifest_digest(
        [images[position - 1].source_url for position in positions]
    )

    parcel = _normalize_parcel(raw["parcel"])
    target_pricing_digest, global_price = _normalize_target_pricing(
        raw["target_pricing"]
    )
    policy_digest = _required_digest(
        raw["policy_digest"], "policy_digest_invalid"
    )
    category = _normalize_category(raw["category"])
    attributes = _normalize_attributes(raw["attributes"])
    if raw["attributes_complete"] is not True:
        raise _Violation("attributes_incomplete")
    attribute_tree_digest = _required_digest(
        raw["attribute_tree_digest"], "attribute_tree_invalid"
    )
    brand = _normalize_brand(raw["brand"])
    stock = _normalize_stock(raw["seller_stock"])
    location = _normalize_location(raw["location"])
    condition = _required_string(raw["condition"], "condition_invalid")
    if condition not in CONDITIONS:
        raise _Violation("condition_invalid")
    preorder = _normalize_preorder(raw["preorder"])
    variations = _normalize_variations(raw["variations"], positions)
    if raw["variations_complete"] is not True:
        raise _Violation("variations_incomplete")
    models = _normalize_models(
        raw["models"],
        variations=variations,
        global_price=global_price,
        stock_quantity=stock.quantity,
    )

    existing_id = raw["existing_global_item_id"]
    existing_evidence = raw["existing_global_identity_evidence_digest"]
    if mode == NEW_GLOBAL:
        if existing_id is not None or existing_evidence is not None:
            raise _Violation("new_global_existing_identity_forbidden")
        normalized_existing_id = None
        normalized_existing_evidence = None
    else:
        normalized_existing_id = _positive_int(
            existing_id, "existing_global_identity_invalid"
        )
        normalized_existing_evidence = _required_digest(
            existing_evidence, "existing_global_identity_invalid"
        )
    _validate_seller_stock_source(
        mode=mode,
        observation_evidence_digest=observation_evidence_digest,
        stock=stock,
        location=location,
        existing_global_item_id=normalized_existing_id,
        existing_global_identity_evidence_digest=(
            normalized_existing_evidence
        ),
    )

    return _Plan(
        mode=mode,
        observation_evidence_digest=observation_evidence_digest,
        source_identity_schema_version=source_identity_schema,
        source_identity_digest=source_identity_digest,
        sku_lineage_schema_version=sku_lineage_schema,
        sku_lineage_digest=sku_lineage_digest,
        content_package_digest=content_package_digest,
        title=title,
        description=description,
        approved_copy_digest=supplied_copy_digest,
        approved_images=images,
        approved_source_image_manifest_digest=supplied_manifest_digest,
        selected_image_positions=positions,
        selected_source_image_manifest_digest=selected_manifest_digest,
        parcel=parcel,
        target_pricing_digest=target_pricing_digest,
        global_original_price_cny=global_price,
        policy_digest=policy_digest,
        category=category,
        attributes=attributes,
        attribute_tree_digest=attribute_tree_digest,
        brand=brand,
        seller_stock=stock,
        location=location,
        condition=condition,
        preorder=preorder,
        variations=variations,
        models=models,
        existing_global_item_id=normalized_existing_id,
        existing_global_identity_evidence_digest=(
            normalized_existing_evidence
        ),
    )


def _normalize_images(value: object) -> tuple[_ApprovedImage, ...]:
    rows = _required_list(value, "approved_images_invalid")
    if not rows:
        raise _Violation("approved_images_invalid")
    images: list[_ApprovedImage] = []
    seen_urls: set[str] = set()
    seen_digests: set[str] = set()
    for row in rows:
        _exact_mapping(
            row,
            required={"source_url", "source_image_digest"},
            optional=set(),
            code="approved_images_invalid",
        )
        url = _https_url(row["source_url"], "approved_images_invalid")
        digest = _required_digest(
            row["source_image_digest"], "approved_images_invalid"
        )
        if url in seen_urls or digest in seen_digests:
            raise _Violation("approved_images_invalid")
        seen_urls.add(url)
        seen_digests.add(digest)
        images.append(_ApprovedImage(url, digest))
    return tuple(images)


def _normalize_selected_positions(
    value: object, approved_count: int
) -> tuple[int, ...]:
    positions = _required_list(value, "selected_images_invalid")
    if not positions or len(positions) > 9:
        raise _Violation("selected_images_invalid")
    normalized = tuple(
        _positive_int(position, "selected_images_invalid")
        for position in positions
    )
    if normalized != tuple(sorted(set(normalized))):
        raise _Violation("selected_images_invalid")
    if normalized[-1] > approved_count:
        raise _Violation("selected_images_invalid")
    return normalized


def _normalize_parcel(value: object) -> _Parcel:
    row = _exact_mapping(
        value,
        required={
            "weight_kg",
            "length_cm",
            "width_cm",
            "height_cm",
            "contract_digest",
        },
        optional=set(),
        code="parcel_invalid",
    )
    return _Parcel(
        weight_kg=_positive_decimal(row["weight_kg"], "parcel_invalid"),
        length_cm=_positive_decimal(row["length_cm"], "parcel_invalid"),
        width_cm=_positive_decimal(row["width_cm"], "parcel_invalid"),
        height_cm=_positive_decimal(row["height_cm"], "parcel_invalid"),
        contract_digest=_required_digest(
            row["contract_digest"], "parcel_invalid"
        ),
    )


def _normalize_target_pricing(value: object) -> tuple[str, str]:
    row = _exact_mapping(
        value,
        required={"currency", "global_original_price", "contract_digest"},
        optional=set(),
        code="target_pricing_invalid",
    )
    if type(row["currency"]) is not str or row["currency"] != "CNY":
        raise _Violation("target_pricing_invalid")
    return (
        _required_digest(row["contract_digest"], "target_pricing_invalid"),
        _positive_decimal(
            row["global_original_price"], "target_pricing_invalid"
        ),
    )


def _normalize_category(value: object) -> _Category:
    row = _exact_mapping(
        value,
        required={
            "category_id",
            "path",
            "path_complete",
            "evidence_digest",
        },
        optional=set(),
        code="category_invalid",
    )
    category_id = _positive_int(row["category_id"], "category_invalid")
    if row["path_complete"] is not True:
        raise _Violation("category_path_incomplete")
    path_rows = _required_list(row["path"], "category_invalid")
    if not path_rows:
        raise _Violation("category_invalid")
    path: list[_CategoryNode] = []
    seen: set[int] = set()
    for node in path_rows:
        node = _exact_mapping(
            node,
            required={"category_id", "name"},
            optional=set(),
            code="category_invalid",
        )
        node_id = _positive_int(node["category_id"], "category_invalid")
        if node_id in seen:
            raise _Violation("category_invalid")
        seen.add(node_id)
        path.append(
            _CategoryNode(
                node_id, _required_string(node["name"], "category_invalid")
            )
        )
    if path[-1].category_id != category_id:
        raise _Violation("category_invalid")
    return _Category(
        category_id,
        tuple(path),
        _required_digest(row["evidence_digest"], "category_invalid"),
    )


def _normalize_attributes(value: object) -> tuple[_Attribute, ...]:
    rows = _required_list(value, "attributes_invalid")
    if not rows:
        raise _Violation("attributes_invalid")
    attributes: list[_Attribute] = []
    seen_ids: set[int] = set()
    for row in rows:
        row = _exact_mapping(
            row,
            required={"attribute_id", "attribute_value_list"},
            optional=set(),
            code="attributes_invalid",
        )
        attribute_id = _positive_int(
            row["attribute_id"], "attributes_invalid"
        )
        if attribute_id in seen_ids:
            raise _Violation("attributes_invalid")
        seen_ids.add(attribute_id)
        values_raw = _required_list(
            row["attribute_value_list"], "attributes_invalid"
        )
        if not values_raw:
            raise _Violation("attributes_invalid")
        values: list[_AttributeValue] = []
        seen_values: set[tuple[int, str, str | None]] = set()
        for item in values_raw:
            item = _exact_mapping(
                item,
                required={"value_id", "original_value_name"},
                optional={"value_unit"},
                code="attributes_invalid",
            )
            value_id = _nonnegative_int(
                item["value_id"], "attributes_invalid"
            )
            value_name = _required_string(
                item["original_value_name"], "attributes_invalid"
            )
            value_unit = None
            if "value_unit" in item:
                value_unit = _required_string(
                    item["value_unit"], "attributes_invalid"
                )
            key = (value_id, value_name, value_unit)
            if key in seen_values:
                raise _Violation("attributes_invalid")
            seen_values.add(key)
            values.append(_AttributeValue(*key))
        attributes.append(_Attribute(attribute_id, tuple(values)))
    return tuple(attributes)


def _normalize_brand(value: object) -> _Brand:
    row = _exact_mapping(
        value,
        required={"brand_id", "original_brand_name", "evidence_digest"},
        optional=set(),
        code="brand_invalid",
    )
    return _Brand(
        _nonnegative_int(row["brand_id"], "brand_invalid"),
        _required_string(row["original_brand_name"], "brand_invalid"),
        _required_digest(row["evidence_digest"], "brand_invalid"),
    )


def _normalize_stock(value: object) -> _SellerStock:
    row = _exact_mapping(
        value,
        required={
            "source",
            "source_digest",
            "quantity",
            "approval_reference",
        },
        optional=set(),
        code="seller_stock_invalid",
    )
    source = _required_string(row["source"], "seller_stock_invalid")
    if source not in SELLER_STOCK_SOURCES:
        raise _Violation("seller_stock_invalid")
    return _SellerStock(
        source,
        _required_digest(row["source_digest"], "seller_stock_invalid"),
        _positive_int(row["quantity"], "seller_stock_invalid"),
        _required_string(row["approval_reference"], "seller_stock_invalid"),
    )


def _official_existing_global_seller_stock_binding(
    *,
    observation_evidence_digest: object,
    existing_global_item_id: object,
    existing_global_identity_evidence_digest: object,
    seller_stock_rows: object,
) -> dict[str, Any]:
    observation_digest = _required_digest(
        observation_evidence_digest, "seller_stock_invalid"
    )
    item_id = _positive_int(
        existing_global_item_id, "seller_stock_invalid"
    )
    identity_digest = _required_digest(
        existing_global_identity_evidence_digest, "seller_stock_invalid"
    )
    rows = _required_list(seller_stock_rows, "seller_stock_invalid")
    if len(rows) != 1:
        raise _Violation("seller_stock_invalid")
    row = _exact_mapping(
        rows[0],
        required={"location_id", "stock"},
        optional=set(),
        code="seller_stock_invalid",
    )
    location_id = _required_string(
        row["location_id"], "seller_stock_invalid"
    )
    normalized_quantity = _positive_int(
        row["stock"], "seller_stock_invalid"
    )
    normalized_stock_rows = [
        {"location_id": location_id, "stock": normalized_quantity}
    ]
    source_digest = _digest(
        {
            "schema_version": (
                OFFICIAL_EXISTING_GLOBAL_SELLER_STOCK_SOURCE
            ),
            "authority": OFFICIAL_AUTHORITY,
            "observation_schema_version": (
                OFFICIAL_OBSERVATION_SCHEMA_VERSION
            ),
            "observation_evidence_digest": observation_digest,
            "existing_global_item_id": item_id,
            "existing_global_identity_evidence_digest": identity_digest,
            "seller_stock": normalized_stock_rows,
        }
    )
    location_evidence_digest = _digest(
        {
            "schema_version": "shopee-official-existing-location/v1",
            "authority": OFFICIAL_AUTHORITY,
            "observation_evidence_digest": observation_digest,
            "existing_global_item_id": item_id,
            "existing_global_identity_evidence_digest": identity_digest,
            "location_id": location_id,
        }
    )
    return {
        "seller_stock": {
            "source": OFFICIAL_EXISTING_GLOBAL_SELLER_STOCK_SOURCE,
            "source_digest": source_digest,
            "quantity": normalized_quantity,
            "approval_reference": observation_digest,
        },
        "location": {
            "location_id": location_id,
            "evidence_digest": location_evidence_digest,
        },
    }


def _validate_seller_stock_source(
    *,
    mode: str,
    observation_evidence_digest: str,
    stock: _SellerStock,
    location: _Location,
    existing_global_item_id: int | None,
    existing_global_identity_evidence_digest: str | None,
) -> None:
    if stock.source != OFFICIAL_EXISTING_GLOBAL_SELLER_STOCK_SOURCE:
        return
    if (
        mode != EXISTING_GLOBAL
        or existing_global_item_id is None
        or existing_global_identity_evidence_digest is None
    ):
        raise _Violation("official_existing_seller_stock_mode_invalid")
    expected = _official_existing_global_seller_stock_binding(
        observation_evidence_digest=observation_evidence_digest,
        existing_global_item_id=existing_global_item_id,
        existing_global_identity_evidence_digest=(
            existing_global_identity_evidence_digest
        ),
        seller_stock_rows=[
            {
                "location_id": location.location_id,
                "stock": stock.quantity,
            }
        ],
    )
    if (
        stock.payload() != expected["seller_stock"]
        or location.payload() != expected["location"]
    ):
        raise _Violation("official_existing_seller_stock_digest_mismatch")


def _normalize_location(value: object) -> _Location:
    row = _exact_mapping(
        value,
        required={"location_id", "evidence_digest"},
        optional=set(),
        code="location_invalid",
    )
    return _Location(
        _required_string(row["location_id"], "location_invalid"),
        _required_digest(row["evidence_digest"], "location_invalid"),
    )


def _normalize_preorder(value: object) -> _PreOrder:
    row = _exact_mapping(
        value,
        required={"is_pre_order", "days_to_ship"},
        optional=set(),
        code="preorder_invalid",
    )
    if type(row["is_pre_order"]) is not bool:
        raise _Violation("preorder_invalid")
    days = _nonnegative_int(row["days_to_ship"], "preorder_invalid")
    if row["is_pre_order"] is True and days <= 0:
        raise _Violation("preorder_invalid")
    if row["is_pre_order"] is False and days != 0:
        raise _Violation("preorder_invalid")
    return _PreOrder(row["is_pre_order"], days)


def _normalize_variations(
    value: object, selected_image_positions: tuple[int, ...]
) -> tuple[_VariationTier, ...]:
    rows = _required_list(value, "variations_invalid")
    if not 1 <= len(rows) <= 2:
        raise _Violation("variations_invalid")
    tiers: list[_VariationTier] = []
    seen_names: set[str] = set()
    selected_set = set(selected_image_positions)
    for row in rows:
        row = _exact_mapping(
            row,
            required={"name", "option_list"},
            optional=set(),
            code="variations_invalid",
        )
        name = _required_string(row["name"], "variations_invalid")
        if name in seen_names:
            raise _Violation("variations_invalid")
        seen_names.add(name)
        options_raw = _required_list(row["option_list"], "variations_invalid")
        if not options_raw:
            raise _Violation("variations_invalid")
        options: list[_VariationOption] = []
        seen_options: set[str] = set()
        for option in options_raw:
            option = _exact_mapping(
                option,
                required={"option"},
                optional={"approved_image_position"},
                code="variations_invalid",
            )
            option_name = _required_string(
                option["option"], "variations_invalid"
            )
            if option_name in seen_options:
                raise _Violation("variations_invalid")
            seen_options.add(option_name)
            image_position = None
            if "approved_image_position" in option:
                image_position = _positive_int(
                    option["approved_image_position"], "variations_invalid"
                )
                if image_position not in selected_set:
                    raise _Violation("variations_invalid")
            options.append(_VariationOption(option_name, image_position))
        tiers.append(_VariationTier(name, tuple(options)))
    return tuple(tiers)


def _normalize_models(
    value: object,
    *,
    variations: tuple[_VariationTier, ...],
    global_price: str,
    stock_quantity: int,
) -> tuple[_Model, ...]:
    rows = _required_list(value, "models_invalid")
    if not rows:
        raise _Violation("models_invalid")
    models: list[_Model] = []
    seen_skus: set[str] = set()
    seen_indices: set[tuple[int, ...]] = set()
    for row in rows:
        row = _exact_mapping(
            row,
            required={
                "global_model_sku",
                "tier_index",
                "original_price_cny",
                "seller_stock_quantity",
            },
            optional=set(),
            code="models_invalid",
        )
        sku = _required_string(row["global_model_sku"], "models_invalid")
        if _MODEL_SKU_RE.fullmatch(sku) is None:
            raise _Violation("models_invalid")
        if sku in seen_skus:
            raise _Violation("models_invalid")
        seen_skus.add(sku)
        indices_raw = _required_list(row["tier_index"], "models_invalid")
        if len(indices_raw) != len(variations):
            raise _Violation("models_invalid")
        indices = tuple(
            _nonnegative_int(index, "models_invalid")
            for index in indices_raw
        )
        if any(
            index >= len(variations[position].options)
            for position, index in enumerate(indices)
        ):
            raise _Violation("models_invalid")
        if indices in seen_indices:
            raise _Violation("models_invalid")
        seen_indices.add(indices)
        model_price = _positive_decimal(
            row["original_price_cny"], "models_invalid"
        )
        model_stock = _positive_int(
            row["seller_stock_quantity"], "models_invalid"
        )
        if model_price != global_price or model_stock != stock_quantity:
            raise _Violation("models_invalid")
        models.append(_Model(sku, indices, model_price, model_stock))
    expected_indices = set(
        itertools.product(
            *(range(len(variation.options)) for variation in variations)
        )
    )
    if seen_indices != expected_indices:
        raise _Violation("models_incomplete")
    return tuple(models)


def _blocked_candidate(
    *,
    mode: str | None,
    authority: str,
    observation_schema_version: str,
    observation_evidence_digest: str | None,
    code: str,
) -> ShopeeGlobalPlanCandidate:
    blocker_codes = (code,)
    provisional = ShopeeGlobalPlanCandidate.__new__(ShopeeGlobalPlanCandidate)
    values = {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "status": BLOCKED_CAPABILITY,
        "planning_allowed": False,
        "mode": mode,
        "observation_authority": authority,
        "observation_schema_version": observation_schema_version,
        "observation_evidence_digest": observation_evidence_digest,
        "blocker_codes": blocker_codes,
        "candidate_digest": "0" * 64,
        "_plan": None,
    }
    for name, value in values.items():
        object.__setattr__(provisional, name, value)
    values["candidate_digest"] = _candidate_digest(provisional)
    return ShopeeGlobalPlanCandidate(**values)


def _candidate_digest(candidate: ShopeeGlobalPlanCandidate) -> str:
    return _digest(
        {
            "schema_version": candidate.schema_version,
            "status": candidate.status,
            "planning_allowed": candidate.planning_allowed,
            "mode": candidate.mode,
            "observation_authority": candidate.observation_authority,
            "observation_schema_version": candidate.observation_schema_version,
            "observation_evidence_digest": (
                candidate.observation_evidence_digest
            ),
            "blocker_codes": list(candidate.blocker_codes),
            "plan": candidate._plan.payload()
            if candidate._plan is not None
            else None,
        }
    )


def _approved_plan_digest(approved: ApprovedShopeeGlobalPlan) -> str:
    return _digest(
        {
            "schema_version": approved.schema_version,
            "approved_by": approved.approved_by,
            "confirm_approved_shopee_global_plan": (
                approved.confirm_approved_shopee_global_plan
            ),
            "candidate_digest": approved.candidate_digest,
            "mode": approved.mode,
            "plan": approved._plan.payload(),
        }
    )


def _exact_mapping(
    value: object,
    *,
    required: set[str],
    optional: set[str],
    code: str,
) -> dict[str, Any]:
    if type(value) is not dict:
        raise _Violation(code)
    if not required.issubset(value) or set(value) - required - optional:
        raise _Violation(code)
    return value


def _required_list(value: object, code: str) -> list[Any]:
    if type(value) is not list:
        raise _Violation(code)
    return value


def _required_string(value: object, code: str) -> str:
    if type(value) is not str:
        raise _Violation(code)
    normalized = unicodedata.normalize("NFC", value.strip())
    if not normalized or any(ord(char) < 32 for char in normalized):
        raise _Violation(code)
    return normalized


def _positive_int(value: object, code: str) -> int:
    if type(value) is not int or value <= 0:
        raise _Violation(code)
    return value


def _nonnegative_int(value: object, code: str) -> int:
    if type(value) is not int or value < 0:
        raise _Violation(code)
    return value


def _positive_decimal(value: object, code: str) -> str:
    if type(value) not in {str, int, Decimal}:
        raise _Violation(code)
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise _Violation(code)
    if not number.is_finite() or number <= 0:
        raise _Violation(code)
    normalized = format(number.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized


def _https_url(value: object, code: str) -> str:
    if type(value) is not str or value != value.strip():
        raise _Violation(code)
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise _Violation(code)
    return value


def _required_digest(value: object, code: str) -> str:
    if not _is_digest(value):
        raise _Violation(code)
    return value


def _is_digest(value: object) -> bool:
    return type(value) is str and _DIGEST_RE.fullmatch(value) is not None


def _is_code(value: object) -> bool:
    return type(value) is str and _CODE_RE.fullmatch(value) is not None


def _reject_duplicate_json_keys(
    pairs: Sequence[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


__all__ = [
    "APPROVED_EXISTING_PLAN_SCHEMA_VERSION",
    "APPROVED_PLAN_SCHEMA_VERSION",
    "APPROVED_PLAN_RECORD_SCHEMA_VERSION",
    "ApprovedShopeeGlobalPlan",
    "BLOCKED_CAPABILITY",
    "CANDIDATE_SCHEMA_VERSION",
    "COMMUNITY_AUTHORITY",
    "EXISTING_GLOBAL",
    "EXISTING_CURRENT_SNAPSHOT_SCHEMA_VERSION",
    "EXISTING_GLOBAL_PERMISSIONS",
    "GENERATED_SDK_AUTHORITY",
    "GLOBAL_PLAN_MODES",
    "INJECTED_UNVERIFIED_AUTHORITY",
    "NEW_GLOBAL",
    "OBSERVATION_AUTHORITIES",
    "OFFICIAL_AUTHORITY",
    "OFFICIAL_EXISTING_GLOBAL_SELLER_STOCK_SOURCE",
    "OFFICIAL_OBSERVATION_SCHEMA_VERSION",
    "READY",
    "ShopeeGlobalPlanApprovalError",
    "ShopeeGlobalPlanCandidate",
    "ShopeeGlobalPlanContractError",
    "ShopeeGlobalPlanDriftError",
    "ShopeeGlobalPlanObservationError",
    "approve_shopee_global_plan",
    "build_shopee_official_existing_global_seller_stock",
    "build_shopee_existing_current_snapshot_candidate",
    "build_shopee_global_plan_candidate",
    "rehydrate_approved_shopee_global_plan",
    "serialize_approved_shopee_global_plan",
    "validate_approved_shopee_global_plan",
]
