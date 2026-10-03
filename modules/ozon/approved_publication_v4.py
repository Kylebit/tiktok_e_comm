"""Deterministic Ozon boundary for one frozen publication snapshot.

The caller owns provider transport.  This module owns only the exact v4 fact
projection, one independent dispatch per approved model SKU, authoritative
readback classification, and the narrow result returned to the publication
runner.  It never reads a dashboard, catalogue cache, or another channel.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
from typing import Any

from domains.product_operations.approved_publication_snapshot import (
    publication_content_for_target,
    publication_images_for_target,
)


SNAPSHOT_SCHEMA_VERSION = "approved-publication-snapshot/v4"
PLATFORM_RESULT_SCHEMA_VERSION = "product-publication-platform-result/v1"
OZON_TARGET = "ozon:RU"
PROCESSING_STATES = frozenset({"IMPORTED", "OFFER_VALIDATED", "PROCESSING"})
FAILED_STATES = frozenset(
    {"ARCHIVED", "BLOCKED", "CANCELLED", "DECLINED", "ERROR", "FAILED", "REJECTED"}
)


class OzonApprovedPublicationError(ValueError):
    """The frozen facts cannot produce an exact Ozon request."""


@dataclass(frozen=True)
class OzonDispatchFact:
    """Credential-free fact returned by the thin official import transport."""

    outcome: str
    task_id: str | None = None
    provider_code: str | None = None
    provider_reason: str | None = None

    def __post_init__(self) -> None:
        if self.outcome not in {"ACCEPTED", "REJECTED", "UNKNOWN", "PRE_SUBMIT_FAILED"}:
            raise ValueError("Ozon dispatch outcome is invalid")
        if self.task_id is not None and (
            type(self.task_id) is not str
            or not self.task_id
            or self.task_id != self.task_id.strip()
        ):
            raise ValueError("Ozon dispatch task identity is invalid")
        if self.outcome == "ACCEPTED" and self.task_id is None:
            raise ValueError("accepted Ozon dispatch requires a task identity")
        for value, name in (
            (self.provider_code, "Ozon provider code"),
            (self.provider_reason, "Ozon provider reason"),
        ):
            if value is not None and (
                type(value) is not str
                or not value
                or value != value.strip()
                or len(value) > 160
            ):
                raise ValueError(f"{name} is invalid")


@dataclass(frozen=True)
class OzonStockDispatchFact:
    """Credential-free result of one batch stock mutation."""

    outcome: str
    provider_code: str | None = None
    provider_reason: str | None = None

    def __post_init__(self) -> None:
        if self.outcome not in {"ACCEPTED", "REJECTED", "UNKNOWN", "PRE_SUBMIT_FAILED"}:
            raise ValueError("Ozon stock dispatch outcome is invalid")
        for value, name in (
            (self.provider_code, "Ozon stock provider code"),
            (self.provider_reason, "Ozon stock provider reason"),
        ):
            if value is not None and (
                type(value) is not str
                or not value
                or value != value.strip()
                or len(value) > 160
            ):
                raise ValueError(f"{name} is invalid")


DispatchVariant = Callable[[dict[str, Any]], OzonDispatchFact]
ReadbackVariants = Callable[[tuple[str, ...]], Sequence[Mapping[str, Any]]]
UpdateStocks = Callable[[tuple[dict[str, Any], ...]], OzonStockDispatchFact]
ReadbackStocks = Callable[[tuple[str, ...]], Sequence[Mapping[str, Any]]]
@dataclass(frozen=True)
class PreparedStockUpdate:
    warehouse_id: int
    submit: Callable[[], OzonStockDispatchFact]

    def __post_init__(self):
        if type(self.warehouse_id) is not int or self.warehouse_id <= 0 or not callable(self.submit):
            raise ValueError("prepared Ozon stock submission identity is invalid")


PrepareStockUpdate = Callable[[tuple[dict[str, Any], ...]], PreparedStockUpdate]
OfficialProfileResolver = Callable[[Mapping[str, Any]], Mapping[str, Any]]
LocalizedCopyResolver = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise OzonApprovedPublicationError(f"{name} is invalid")
    return value


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise OzonApprovedPublicationError(f"{name} is invalid")
    return value


def _sequence(value: object, name: str) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise OzonApprovedPublicationError(f"{name} is invalid")
    return list(value)


def _positive_decimal(value: object, name: str) -> str:
    if type(value) not in {str, int, float} or isinstance(value, bool):
        raise OzonApprovedPublicationError(f"{name} is invalid")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise OzonApprovedPublicationError(f"{name} is invalid") from None
    if not number.is_finite() or number <= 0:
        raise OzonApprovedPublicationError(f"{name} is invalid")
    normalized = format(number.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized


def _https_urls(value: object, name: str) -> list[str]:
    rows = _sequence(value, name)
    if not rows:
        raise OzonApprovedPublicationError(f"{name} is empty")
    urls = [_text(row, name) for row in rows]
    if any(not url.startswith("https://") for url in urls) or len(urls) != len(set(urls)):
        raise OzonApprovedPublicationError(f"{name} is invalid")
    return urls


def _exact_ozon_target(snapshot: Mapping[str, Any], target_labels: tuple[str, ...]) -> None:
    if target_labels != (OZON_TARGET,):
        raise OzonApprovedPublicationError("Ozon target scope is not exact")
    raw_targets = _sequence(snapshot.get("publication_targets"), "publication targets")
    matches = [
        row
        for row in raw_targets
        if isinstance(row, Mapping) and row.get("target_label") == OZON_TARGET
    ]
    if len(matches) != 1:
        raise OzonApprovedPublicationError("approved Ozon target is missing or ambiguous")
    target = matches[0]
    if (
        target.get("platform") != "ozon"
        or target.get("site") != "RU"
        or target.get("store") != "RU"
    ):
        raise OzonApprovedPublicationError("approved Ozon target identity conflicts")


def _approved_category(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    categories = _mapping(snapshot.get("categories_by_target"), "target categories")
    row = _mapping(categories.get(OZON_TARGET), "approved Ozon category")
    if (
        row.get("target_label") != OZON_TARGET
        or row.get("platform") != "ozon"
        or row.get("site") != "RU"
        or row.get("store") != "RU"
    ):
        raise OzonApprovedPublicationError("approved Ozon category identity conflicts")
    decision = _mapping(row.get("decision"), "approved Ozon category decision")
    if decision.get("status") != "APPROVED":
        raise OzonApprovedPublicationError("approved Ozon category is unavailable")
    category = _mapping(row.get("category"), "approved Ozon category")
    category_id = _text(category.get("id"), "approved Ozon category id")
    category_name = _text(category.get("name"), "approved Ozon category name")
    raw_path = _sequence(category.get("path"), "approved Ozon category path")
    path: list[dict[str, str]] = []
    for raw in raw_path:
        node = _mapping(raw, "approved Ozon category path node")
        path.append(
            {
                "id": _text(node.get("id"), "approved Ozon category path id"),
                "name": _text(node.get("name"), "approved Ozon category path name"),
            }
        )
    if not path or path[-1] != {"id": category_id, "name": category_name}:
        raise OzonApprovedPublicationError("approved Ozon category path conflicts")
    return {"id": category_id, "name": category_name, "path": path}


def _official_profile(value: object) -> dict[str, Any]:
    profile = _mapping(value, "Ozon official profile")
    if (
        profile.get("schema_version") != "ozon-official-profile-resolution/v1"
        or profile.get("resolution") != "EXACT"
    ):
        raise OzonApprovedPublicationError("Ozon official profile is not exact")
    category_id = int(
        _positive_decimal(
            profile.get("description_category_id"),
            "Ozon official description category id",
        )
    )
    category_name = _text(profile.get("category_name"), "Ozon category name")
    type_id = int(_positive_decimal(profile.get("type_id"), "Ozon official type id"))
    type_name = _text(profile.get("type_name"), "Ozon official type name")
    raw_path = _sequence(profile.get("category_path"), "Ozon category path")
    path: list[dict[str, str]] = []
    for raw in raw_path:
        node = _mapping(raw, "Ozon category path node")
        path.append(
            {
                "id": _text(node.get("id"), "Ozon category path id"),
                "name": _text(node.get("name"), "Ozon category path name"),
            }
        )
    if not path or path[-1] != {"id": str(category_id), "name": category_name}:
        raise OzonApprovedPublicationError("Ozon official category path conflicts")
    raw_attributes = _mapping(
        profile.get("required_attributes"), "Ozon required attributes"
    )
    if set(raw_attributes) != {"brand", "model_name", "product_type"}:
        raise OzonApprovedPublicationError("Ozon required attribute coverage conflicts")

    def dictionary_attribute(name: str, expected_id: int) -> dict[str, Any]:
        row = _mapping(raw_attributes.get(name), f"Ozon {name} attribute")
        if row.get("attribute_id") != expected_id:
            raise OzonApprovedPublicationError(f"Ozon {name} attribute conflicts")
        return {
            "attribute_id": expected_id,
            "dictionary_value_id": int(
                _positive_decimal(
                    row.get("dictionary_value_id"),
                    f"Ozon {name} dictionary value id",
                )
            ),
            "value": _text(row.get("value"), f"Ozon {name} value"),
        }

    model_name = _mapping(raw_attributes.get("model_name"), "Ozon model attribute")
    if model_name != {"attribute_id": 9048}:
        raise OzonApprovedPublicationError("Ozon model attribute conflicts")
    normalized = {
        "schema_version": "ozon-official-profile-resolution/v1",
        "resolution": "EXACT",
        "description_category_id": category_id,
        "category_name": category_name,
        "category_path": path,
        "type_id": type_id,
        "type_name": type_name,
        "required_attributes": {
            "brand": dictionary_attribute("brand", 85),
            "model_name": {"attribute_id": 9048},
            "product_type": dictionary_attribute("product_type", 8229),
        },
    }
    if normalized["required_attributes"]["product_type"]["dictionary_value_id"] != type_id:
        raise OzonApprovedPublicationError("Ozon product type dictionary conflicts")
    return normalized


def _category_and_profile(
    snapshot: Mapping[str, Any],
    resolver: OfficialProfileResolver | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    categories = _mapping(snapshot.get("categories_by_target"), "target categories")
    row = _mapping(categories.get(OZON_TARGET), "approved Ozon category")
    decision = _mapping(row.get("decision"), "approved Ozon category decision")
    status = decision.get("status")
    approved = _approved_category(snapshot) if status == "APPROVED" else None
    if resolver is None:
        if approved is None:
            raise OzonApprovedPublicationError("approved Ozon category is unavailable")
        return approved, None
    profile = _official_profile(resolver(deepcopy(dict(snapshot))))
    resolved = {
        "id": str(profile["description_category_id"]),
        "name": profile["category_name"],
        "path": deepcopy(profile["category_path"]),
    }
    if approved is not None and approved != resolved:
        # Older frozen snapshots can contain only the approved leaf path while
        # the current official tree includes its ancestors.  The category is
        # still exact when the immutable id/name match and the approved path is
        # an exact suffix of the official path.  Enrich it with the official
        # hierarchy; never accept a different leaf or a conflicting ancestor.
        approved_path = approved["path"]
        resolved_path = resolved["path"]
        # Display names are localized snapshot evidence, not immutable Ozon
        # identities.  Ozon may also rename the current official tree.  Bind
        # the frozen decision to category ids and allow the official names to
        # enrich the execution payload.  A conflicting ancestor id still
        # fails closed.
        approved_path_ids = [str(node["id"]) for node in approved_path]
        resolved_path_ids = [str(node["id"]) for node in resolved_path]
        exact_official_suffix = (
            approved["id"] == resolved["id"]
            and len(approved_path_ids) <= len(resolved_path_ids)
            and approved_path_ids
            == resolved_path_ids[-len(approved_path_ids) :]
        )
        if not exact_official_suffix:
            raise OzonApprovedPublicationError(
                "approved and official Ozon categories conflict"
            )
        approved = resolved
    if approved is None and status != "DEFERRED_TO_SKILL":
        raise OzonApprovedPublicationError("Ozon category decision is invalid")
    return approved or resolved, profile


def _localized_copy(
    snapshot: Mapping[str, Any],
    resolver: LocalizedCopyResolver | None,
) -> tuple[str, str]:
    if resolver is None:
        raise OzonApprovedPublicationError(
            "Ozon Russian copy is required for this official profile"
        )
    receipt = _mapping(resolver(deepcopy(dict(snapshot))), "Ozon localized copy")
    if (
        receipt.get("schema_version") != "ozon-localized-copy/v1"
        or receipt.get("source_snapshot_digest") != snapshot.get("snapshot_digest")
        or receipt.get("language") != "ru"
    ):
        raise OzonApprovedPublicationError("Ozon localized copy identity conflicts")
    return _validated_russian_copy(
        receipt.get("title"), receipt.get("description")
    )


def _validated_russian_copy(
    raw_title: object, raw_description: object
) -> tuple[str, str]:
    title = _text(raw_title, "Ozon localized title")
    description = _text(raw_description, "Ozon localized description")

    def has_cyrillic(value: str) -> bool:
        return sum("\u0400" <= character <= "\u04ff" for character in value) >= 5

    if not has_cyrillic(title) or not has_cyrillic(description):
        raise OzonApprovedPublicationError("Ozon localized copy is not Russian")
    # Ozon accepts the multiplication sign in the import request but removes
    # it from the stored title (for example ``7 × 7`` becomes ``7 7``).  That
    # makes an exact authoritative readback impossible, so reject the lossy
    # spelling before any provider write.  Russian ``7 на 7`` is stable.
    if "×" in title:
        raise OzonApprovedPublicationError(
            "Ozon localized title contains a provider-stripped multiplication sign"
        )
    return title, description


def project_ozon_v4_variants(
    snapshot: Mapping[str, Any], *, target_labels: tuple[str, ...],
    official_profile_resolver: OfficialProfileResolver | None = None,
    localized_copy_resolver: LocalizedCopyResolver | None = None,
) -> tuple[dict[str, Any], ...]:
    """Project exact per-model import inputs from a runner-validated v4 body.

    ``old_price_cny`` is an explicit schema seam.  It must be frozen next to
    the Ozon amount for every model; deriving it here from cost or a percentage
    would silently change approved commercial facts.
    """

    if not isinstance(snapshot, Mapping):
        raise OzonApprovedPublicationError("approved publication snapshot is missing")
    if snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise OzonApprovedPublicationError("approved publication snapshot schema is invalid")
    _text(snapshot.get("snapshot_digest"), "snapshot digest")
    _exact_ozon_target(snapshot, target_labels)
    product = _mapping(snapshot.get("product"), "approved product")
    raw_stock_policy = product.get("stock_policy")
    if raw_stock_policy is None:
        stock_quantity = 200
        stock_policy_source = "LEGACY_COMPATIBILITY_DEFAULT"
    else:
        stock_policy = _mapping(raw_stock_policy, "approved stock policy")
        if (
            stock_policy.get("schema_version") != "publication-default-stock/v1"
            or stock_policy.get("quantity_per_sku") != 200
            or stock_policy.get("scope") != "EACH_SELECTED_SKU"
        ):
            raise OzonApprovedPublicationError("approved stock policy is invalid")
        stock_quantity = 200
        stock_policy_source = str(stock_policy.get("source") or "")
    raw_stock_decision = product.get("ozon_stock_decision")
    stock_warehouse_id = None
    if raw_stock_decision is not None:
        stock_decision = _mapping(
            raw_stock_decision, "approved Ozon stock warehouse decision"
        )
        stock_warehouse_id = stock_decision.get("warehouse_id")
        if (
            stock_decision.get("schema_version")
            != "ozon-stock-warehouse-decision/v1"
            or stock_decision.get("selection_policy")
            != "EXACT_UNIQUE_ACTIVE_OR_CREATED_NON_KGT"
            or stock_decision.get("source") != "OFFICIAL_PROVIDER_READBACK"
            or type(stock_warehouse_id) is not int
            or stock_warehouse_id <= 0
        ):
            raise OzonApprovedPublicationError(
                "approved Ozon stock warehouse decision is invalid"
            )
    title = _text(product.get("title"), "approved Ozon title")
    description = _text(product.get("description"), "approved Ozon description")
    target_content = publication_content_for_target(snapshot, OZON_TARGET)
    if target_content["locale"].lower().startswith("ru"):
        title, description = _validated_russian_copy(
            target_content["title"], target_content["description"]
        )
    base_images = _https_urls(product.get("images"), "approved product images")
    target_images = publication_images_for_target(snapshot, OZON_TARGET)
    if len(base_images) != len(target_images):
        raise OzonApprovedPublicationError(
            "Ozon target image route coverage is invalid"
        )
    routed_by_base = dict(zip(base_images, target_images))
    category, official_profile = _category_and_profile(
        snapshot, official_profile_resolver
    )
    if (
        official_profile is not None
        and official_profile["type_id"] == 93785
        and not target_content["locale"].lower().startswith("ru")
    ):
        title, description = _localized_copy(snapshot, localized_copy_resolver)
    raw_skus = _sequence(snapshot.get("skus"), "approved SKUs")
    if not raw_skus:
        raise OzonApprovedPublicationError("approved Ozon SKU coverage is empty")

    variants: list[dict[str, Any]] = []
    seen_models: set[str] = set()
    seen_variants: set[str] = set()
    for raw in raw_skus:
        sku = _mapping(raw, "approved Ozon SKU")
        variant_key = _text(sku.get("variant_key"), "approved variant key")
        seller_sku = _text(sku.get("seller_sku"), "approved seller SKU")
        model_sku = _text(sku.get("model_sku"), "approved model SKU")
        if variant_key in seen_variants or model_sku in seen_models:
            raise OzonApprovedPublicationError("approved Ozon SKU identity is ambiguous")
        seen_variants.add(variant_key)
        seen_models.add(model_sku)
        specification = dict(_mapping(sku.get("specification"), "approved specification"))
        if not specification or any(
            type(key) is not str
            or type(value) is not str
            or not key.strip()
            or not value.strip()
            for key, value in specification.items()
        ):
            raise OzonApprovedPublicationError("approved Ozon specification is invalid")
        parcel = _mapping(sku.get("parcel"), "approved Ozon parcel")
        package = _sequence(parcel.get("package_cm"), "approved Ozon package")
        if len(package) != 3:
            raise OzonApprovedPublicationError("approved Ozon package is invalid")
        normalized_parcel = {
            "weight_kg": _positive_decimal(parcel.get("weight_kg"), "approved Ozon weight"),
            "package_cm": [
                _positive_decimal(value, "approved Ozon package dimension")
                for value in package
            ],
        }
        prices = _mapping(sku.get("prices"), "approved Ozon prices")
        price = _mapping(prices.get(OZON_TARGET), "approved Ozon price")
        if price.get("currency") != "CNY":
            raise OzonApprovedPublicationError("approved Ozon price currency must be CNY")
        amount = _positive_decimal(price.get("amount"), "approved Ozon price")
        if "old_price_cny" not in price:
            raise OzonApprovedPublicationError(
                "approved Ozon old_price_cny lineage is missing"
            )
        old_price = _positive_decimal(
            price.get("old_price_cny"), "approved Ozon old price"
        )
        if Decimal(old_price) <= Decimal(amount):
            raise OzonApprovedPublicationError(
                "approved Ozon old price must exceed price"
            )
        images = [
            routed_by_base.get(url, url)
            for url in _https_urls(sku.get("variant_images"), "approved Ozon variant images")
        ]
        variants.append(
            {
                "schema_version": "ozon-approved-import-variant/v1",
                "target_label": OZON_TARGET,
                "offer_id": model_sku,
                "approved_seller_sku": seller_sku,
                "variant_key": variant_key,
                "specification": specification,
                "title": title,
                "description": description,
                "price": amount,
                "old_price": old_price,
                "currency": "CNY",
                "stock_quantity": stock_quantity,
                "stock_policy_schema_version": "publication-default-stock/v1",
                "stock_policy_source": stock_policy_source,
                **(
                    {"stock_warehouse_id": stock_warehouse_id}
                    if stock_warehouse_id is not None
                    else {}
                ),
                "parcel": normalized_parcel,
                "images": images,
                "image_count": len(images),
                "category": deepcopy(category),
                **(
                    {"official_profile": deepcopy(official_profile)}
                    if official_profile is not None
                    else {}
                ),
            }
        )
    return tuple(variants)


def _same_decimal(left: object, right: object) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, TypeError, ValueError):
        return False


def _same_parcel(observed: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    observed_package = observed.get("package_cm")
    expected_package = expected.get("package_cm")
    if (
        not isinstance(observed_package, Sequence)
        or isinstance(observed_package, (str, bytes, bytearray))
        or len(observed_package) != 3
        or not isinstance(expected_package, Sequence)
        or len(expected_package) != 3
    ):
        return False
    return _same_decimal(observed.get("weight_kg"), expected.get("weight_kg")) and all(
        _same_decimal(observed_package[index], expected_package[index])
        for index in range(3)
    )


def _has_authoritative_item_id(observed: Mapping[str, Any]) -> bool:
    item_id = observed.get("id")
    if type(item_id) is int:
        return item_id > 0
    return type(item_id) is str and bool(item_id.strip())


def _classify_variant(
    expected: Mapping[str, Any],
    observed: Mapping[str, Any] | None,
    dispatch: OzonDispatchFact,
) -> str:
    if observed is None:
        return "FAILED" if dispatch.outcome == "REJECTED" else "PROCESSING"
    statuses = observed.get("statuses")
    if not isinstance(statuses, Mapping):
        return "FAILED"
    state = str(statuses.get("status") or "").strip().upper()
    failed = str(statuses.get("status_failed") or "").strip().upper()
    created = statuses.get("is_created") is True
    if failed or state in FAILED_STATES:
        return "FAILED"
    if not created and state in PROCESSING_STATES:
        return "PROCESSING"
    if not _has_authoritative_item_id(observed):
        return "FAILED" if created else "PROCESSING"
    if not created:
        return "PROCESSING"
    images = observed.get("images")
    profile = expected.get("official_profile")
    expected_type_id = (
        str(profile.get("type_id") or "") if isinstance(profile, Mapping) else ""
    )
    checks = (
        observed.get("name") == expected["title"],
        observed.get("description") == expected["description"],
        _same_decimal(observed.get("price"), expected["price"]),
        _same_decimal(observed.get("old_price"), expected["old_price"]),
        isinstance(images, Sequence)
        and not isinstance(images, (str, bytes, bytearray))
        and len(images) == expected["image_count"],
        str(observed.get("category_id") or "") == expected["category"]["id"],
        not expected_type_id
        or str(observed.get("type_id") or "") == expected_type_id,
        _same_parcel(observed, expected["parcel"]),
    )
    if all(checks):
        return "PUBLISHED"
    # A successful import acknowledgement is asynchronous.  Ozon can expose
    # the previous stored copy for a short period even though the new update
    # is accepted.  Only a provider rejection/terminal failed status is a
    # failure; an accepted write with stale facts remains PROCESSING until a
    # later authoritative readback converges.
    return "FAILED" if dispatch.outcome == "REJECTED" else "PROCESSING"


def _result(
    status: str,
    *,
    dispatch_attempted: bool,
    readback_completed: bool,
    external_write_count: int | None,
    requires_human_action: bool,
    stage: str | None = None,
    provider_code: str | None = None,
    provider_reason: str | None = None,
) -> dict[str, Any]:
    status = str(status)
    if status not in {"PUBLISHED", "PROCESSING", "FAILED"}:
        raise OzonApprovedPublicationError("Ozon result status is invalid")
    stage = stage or ("READBACK" if readback_completed else (
        "DISPATCH" if dispatch_attempted else "PREPARATION"
    ))
    if stage not in {
        "PREPARATION",
        "DISPATCH",
        "READBACK",
        "STOCK_READBACK",
        "STOCK_UPDATE",
    }:
        raise OzonApprovedPublicationError("Ozon evidence stage is invalid")
    evidence = {
        "target_label": OZON_TARGET,
        "status": status,
        "stage": stage,
        "provider_code": provider_code or (
            "ozon_preparation_failed" if stage == "PREPARATION" else "ozon_result"
        ),
        "provider_reason": provider_reason or (
            "Ozon preparation failed" if stage == "PREPARATION" else "Ozon result classified"
        ),
        "request_attempted": dispatch_attempted,
        "outcome_unknown": external_write_count is None,
        "external_write_count": external_write_count,
    }
    return {
        "schema_version": PLATFORM_RESULT_SCHEMA_VERSION,
        "platform": "OZON",
        "targets": [{"target_label": OZON_TARGET, "status": status, "evidence": evidence}],
        "dispatch_attempted": dispatch_attempted,
        "readback_completed": readback_completed,
        "external_write_count": external_write_count,
        "requires_human_action": requires_human_action,
    }


def _governed_stock_required(snapshot: Mapping[str, Any]) -> bool:
    product = snapshot.get("product")
    policy = product.get("stock_policy") if isinstance(product, Mapping) else None
    return isinstance(policy, Mapping) and policy.get("source") == "SYSTEM_GOVERNED_DEFAULT"


def _exact_stock_readback(
    raw_rows: object,
    *,
    variants: tuple[dict[str, Any], ...],
) -> bool:
    rows = _sequence(raw_rows, "Ozon stock readback rows")
    if any(not isinstance(row, Mapping) for row in rows):
        raise OzonApprovedPublicationError("Ozon stock readback row is invalid")
    expected = {variant["offer_id"]: variant["stock_quantity"] for variant in variants}
    observed: dict[str, int] = {}
    for row in rows:
        offer_id = str(row.get("offer_id") or "").strip()
        stock = row.get("stock")
        if (
            offer_id not in expected
            or offer_id in observed
            or type(stock) is not int
            or stock < 0
        ):
            raise OzonApprovedPublicationError("Ozon stock readback identity is ambiguous")
        observed[offer_id] = stock
    return observed == expected


def _complete_governed_stock(
    *,
    variants: tuple[dict[str, Any], ...],
    update_stocks: UpdateStocks,
    readback_stocks: ReadbackStocks,
    before_stock_update: Callable[[tuple[dict[str, Any], ...]], object] | None,
    prior_external_write_count: int | None,
    prior_dispatch_attempted: bool,
    prepare_stock_update: PrepareStockUpdate | None = None,
) -> dict[str, Any]:
    offer_ids = tuple(variant["offer_id"] for variant in variants)
    expected_warehouses = {
        variant.get("stock_warehouse_id")
        for variant in variants
        if variant.get("stock_warehouse_id") is not None
    }
    if len(expected_warehouses) > 1:
        raise OzonApprovedPublicationError(
            "approved Ozon stock warehouse identities conflict"
        )
    expected_warehouse = next(iter(expected_warehouses), None)
    def warehouse_identity(rows):
        values = {row.get("warehouse_id") for row in rows if isinstance(row, Mapping)}
        if len(values) == 1 and all(type(value) is int and value > 0 for value in values):
            return next(iter(values))
        return None

    def verified_reason(warehouse_id):
        return (json.dumps({"warehouse_id":warehouse_id,"stock":"FROZEN_EACH_SKU"},separators=(",",":"))
            if warehouse_id is not None else "Ozon stock equals the frozen quantity for every approved SKU")

    try:
        current_stock = readback_stocks(offer_ids)
        current_warehouse = warehouse_identity(current_stock)
        if (
            expected_warehouse is not None
            and current_warehouse != expected_warehouse
        ):
            raise ValueError("live stock warehouse conflicts with approval")
        if prepare_stock_update is not None and current_warehouse is None:
            raise ValueError("live stock readback warehouse is unavailable")
        if _exact_stock_readback(current_stock, variants=variants):
            return _result(
                "PUBLISHED",
                dispatch_attempted=prior_dispatch_attempted,
                readback_completed=True,
                external_write_count=prior_external_write_count,
                requires_human_action=prior_external_write_count is None,
                stage="STOCK_READBACK",
                provider_code="ozon_stock_verified",
                provider_reason=verified_reason(current_warehouse),
            )
    except Exception:
        return _result(
            "PROCESSING",
            dispatch_attempted=prior_dispatch_attempted,
            readback_completed=False,
            external_write_count=prior_external_write_count,
            requires_human_action=prior_external_write_count is None,
            stage="STOCK_READBACK",
            provider_code="ozon_stock_reconciliation_required",
            provider_reason="Ozon stock readback is unavailable; no stock update was attempted",
        )

    updates = tuple(
        {
            "offer_id": variant["offer_id"],
            "stock": variant["stock_quantity"],
            **(
                {"warehouse_id": variant["stock_warehouse_id"]}
                if "stock_warehouse_id" in variant
                else {}
            ),
        }
        for variant in variants
    )
    submit_stock = None
    if prepare_stock_update is not None:
        try:
            submit_stock = prepare_stock_update(deepcopy(updates))
            if type(submit_stock) is not PreparedStockUpdate or submit_stock.warehouse_id != current_warehouse:
                raise TypeError("stock preparation must retain the exact readback warehouse")
        except Exception:
            return _result("PROCESSING" if prior_external_write_count is None else "FAILED",
                dispatch_attempted=prior_dispatch_attempted, readback_completed=True,
                external_write_count=prior_external_write_count, requires_human_action=True,
                stage="PREPARATION", provider_code="ozon_stock_not_attempted",
                provider_reason='{"imports_verified":true,"stock_attempted":false}')
    if before_stock_update is not None:
        try:
            before_stock_update(updates)
        except Exception:
            return _result(
                "PROCESSING" if prior_external_write_count is None else "FAILED",
                dispatch_attempted=prior_dispatch_attempted,
                readback_completed=True,
                external_write_count=prior_external_write_count,
                requires_human_action=True,
                stage="PREPARATION",
                provider_code="ozon_stock_not_attempted",
                provider_reason='{"imports_verified":true,"stock_attempted":false}',
            )
    try:
        fact = submit_stock.submit() if submit_stock is not None else update_stocks(deepcopy(updates))
        if type(fact) is not OzonStockDispatchFact:
            raise TypeError("Ozon stock transport returned an invalid fact")
    except Exception:
        fact = OzonStockDispatchFact(outcome="UNKNOWN")

    if fact.outcome == "PRE_SUBMIT_FAILED":
        return _result(
            "FAILED",
            dispatch_attempted=prior_dispatch_attempted,
            readback_completed=False,
            external_write_count=prior_external_write_count,
            requires_human_action=True,
            stage="PREPARATION",
            provider_code=fact.provider_code or "ozon_stock_preparation_failed",
            provider_reason=fact.provider_reason or "Ozon stock update preparation failed",
        )
    if fact.outcome == "REJECTED":
        return _result(
            "FAILED",
            dispatch_attempted=True,
            readback_completed=False,
            external_write_count=prior_external_write_count,
            requires_human_action=True,
            stage="STOCK_UPDATE",
            provider_code=fact.provider_code or "ozon_stock_update_rejected",
            provider_reason=fact.provider_reason or "Ozon stock update was rejected",
        )

    combined_count = (
        None
        if prior_external_write_count is None or fact.outcome == "UNKNOWN"
        else prior_external_write_count + 1
    )
    try:
        verified_stock = readback_stocks(offer_ids)
        exact = _exact_stock_readback(verified_stock, variants=variants)
        if submit_stock is not None:
            exact = exact and warehouse_identity(verified_stock) == submit_stock.warehouse_id
    except Exception:
        exact = False
    if exact:
        return _result(
            "PUBLISHED",
            dispatch_attempted=True,
            readback_completed=True,
            external_write_count=combined_count,
            requires_human_action=combined_count is None,
            stage="STOCK_READBACK",
            provider_code="ozon_stock_verified",
            provider_reason=verified_reason(submit_stock.warehouse_id if submit_stock is not None else None),
        )
    return _result(
        "PROCESSING",
        dispatch_attempted=True,
        readback_completed=False,
        external_write_count=combined_count,
        requires_human_action=combined_count is None,
        stage="STOCK_READBACK",
        provider_code="ozon_stock_reconciliation_required",
        provider_reason="Ozon stock update did not converge to the frozen quantity",
    )


def execute_ozon_v4_publication(
    snapshot: Mapping[str, Any],
    *,
    target_labels: tuple[str, ...],
    official_profile_resolver: OfficialProfileResolver | None = None,
    localized_copy_resolver: LocalizedCopyResolver | None = None,
    dispatch_variant: DispatchVariant,
    readback_variants: ReadbackVariants,
    update_stocks: UpdateStocks | None = None,
    readback_stocks: ReadbackStocks | None = None,
    prepare_stock_update: PrepareStockUpdate | None = None,
    before_dispatch: Callable[[Mapping[str, Any]], object] | None = None,
    before_stock_update: Callable[[tuple[dict[str, Any], ...]], object] | None = None,
    read_before_dispatch: bool = False,
    after_projection: Callable[[tuple[dict[str, Any], ...]], object] | None = None,
) -> dict[str, Any]:
    """Dispatch all variants, then always perform one authoritative readback.

    Production recovery enables ``read_before_dispatch`` so an already-known
    offer is never blindly re-imported.  Direct deterministic callers retain
    the original dispatch-first contract unless they opt in.
    """

    try:
        variants = project_ozon_v4_variants(
            snapshot,
            target_labels=target_labels,
            official_profile_resolver=official_profile_resolver,
            localized_copy_resolver=localized_copy_resolver,
        )
    except (OzonApprovedPublicationError, TypeError, ValueError):
        return _result(
            "FAILED",
            dispatch_attempted=False,
            readback_completed=False,
            external_write_count=0,
            requires_human_action=True,
        )

    if after_projection is not None:
        try:after_projection(deepcopy(variants))
        except Exception:
            return _result('FAILED',dispatch_attempted=False,readback_completed=False,external_write_count=0,requires_human_action=True,stage='PREPARATION',provider_code='ozon_catalog_scope_not_persisted',provider_reason='Exact catalog account and offer scope could not be persisted')
    stock_required = _governed_stock_required(snapshot)
    if stock_required and (
        not callable(update_stocks) or not callable(readback_stocks)
    ):
        return _result(
            "FAILED",
            dispatch_attempted=False,
            readback_completed=False,
            external_write_count=0,
            requires_human_action=True,
            stage="PREPARATION",
            provider_code="ozon_stock_transport_unavailable",
            provider_reason="Governed Ozon stock transport is unavailable",
        )

    offer_ids = tuple(variant["offer_id"] for variant in variants)
    if read_before_dispatch:
        try:
            existing_raw = readback_variants(offer_ids)
            existing_items = _sequence(existing_raw, "Ozon pre-dispatch readback items")
            if any(not isinstance(item, Mapping) for item in existing_items):
                raise OzonApprovedPublicationError(
                    "Ozon pre-dispatch readback item is invalid"
                )
        except Exception:
            return _result(
                "PROCESSING",
                dispatch_attempted=False,
                readback_completed=False,
                external_write_count=0,
                requires_human_action=False,
                stage="READBACK",
                provider_code="ozon_pre_dispatch_readback_unavailable",
                provider_reason="Ozon existing-offer readback is unavailable; import was not retried",
            )
        if existing_items:
            expected_ids = set(offer_ids)
            existing_by_offer: dict[str, list[Mapping[str, Any]]] = {
                offer_id: [] for offer_id in offer_ids
            }
            unexpected = False
            for item in existing_items:
                offer_id = str(item.get("offer_id") or "").strip()
                if offer_id not in expected_ids:
                    unexpected = True
                    continue
                existing_by_offer[offer_id].append(item)
            statuses = []
            for variant in variants:
                matches = existing_by_offer[variant["offer_id"]]
                if len(matches) > 1:
                    statuses.append("FAILED")
                    continue
                statuses.append(
                    _classify_variant(
                        variant,
                        matches[0] if matches else None,
                        OzonDispatchFact(outcome="UNKNOWN"),
                    )
                )
            if unexpected:
                statuses.append("FAILED")
            if all(status == "PUBLISHED" for status in statuses):
                if stock_required:
                    return _complete_governed_stock(
                        variants=variants,
                        update_stocks=update_stocks,
                        readback_stocks=readback_stocks,
                        prepare_stock_update=prepare_stock_update,
                        before_stock_update=before_stock_update,
                        prior_external_write_count=0,
                        prior_dispatch_attempted=False,
                    )
                return _result(
                    "PUBLISHED",
                    dispatch_attempted=False,
                    readback_completed=True,
                    external_write_count=0,
                    requires_human_action=False,
                    stage="READBACK",
                    provider_code="ozon_existing_offer_verified",
                    provider_reason="Existing Ozon variants passed official readback; import was not repeated",
                )
            if any(status == "FAILED" for status in statuses):
                validation_codes = sorted(
                    {
                        str(error.get("code") or "").strip()
                        for item in existing_items
                        for error in item.get("validation_errors") or ()
                        if isinstance(error, Mapping)
                        and str(error.get("code") or "").strip()
                    }
                )
                provider_code = (
                    validation_codes[0]
                    if len(validation_codes) == 1
                    else "ozon_existing_terminal_validation_failed"
                )
                provider_reason = (
                    "Ozon 官方体积重量校验未通过；已保留批准事实且未重复提交"
                    if provider_code == "ML_INCORRECT_VOLUME_WEIGHT"
                    else "Existing Ozon variants have terminal official validation failures; facts were not changed or resubmitted"
                )
                return _result(
                    "FAILED",
                    dispatch_attempted=False,
                    readback_completed=True,
                    external_write_count=0,
                    requires_human_action=True,
                    stage="READBACK",
                    provider_code=provider_code,
                    provider_reason=provider_reason,
                )
            return _result(
                "PROCESSING",
                dispatch_attempted=False,
                readback_completed=True,
                external_write_count=0,
                requires_human_action=False,
                stage="READBACK",
                provider_code="ozon_existing_offer_processing",
                provider_reason="Existing Ozon variants are still processing; import was not repeated",
            )

    dispatch_facts: dict[str, OzonDispatchFact] = {}
    accepted_count = 0
    unknown_write_count = False
    local_stop_index: int | None = None
    for index, variant in enumerate(variants):
        offer_id = variant["offer_id"]
        if before_dispatch is not None:
            try:
                before_dispatch(variant)
            except Exception:
                local_stop_index = index
                break
        try:
            fact = dispatch_variant(deepcopy(variant))
            if type(fact) is not OzonDispatchFact:
                raise TypeError("Ozon dispatch transport returned an invalid fact")
        except Exception:
            fact = OzonDispatchFact(outcome="UNKNOWN")
        dispatch_facts[offer_id] = fact
        if fact.outcome == "ACCEPTED":
            accepted_count += 1
        elif fact.outcome == "UNKNOWN":
            unknown_write_count = True
        elif fact.outcome == "PRE_SUBMIT_FAILED":
            local_stop_index = index
            break

    # A local refusal stops the remaining loop. Its exact suffix is safe to
    # prepare again; an earlier accepted/unknown import is never forgotten.
    if local_stop_index is not None:
        for variant in variants[local_stop_index:]:
            dispatch_facts[variant["offer_id"]] = OzonDispatchFact(outcome="PRE_SUBMIT_FAILED")
    attempted = any(fact.outcome != "PRE_SUBMIT_FAILED" for fact in dispatch_facts.values())

    external_write_count = None if unknown_write_count else accepted_count
    try:
        raw_items = readback_variants(offer_ids)
        items = _sequence(raw_items, "Ozon readback items")
        if any(not isinstance(item, Mapping) for item in items):
            raise OzonApprovedPublicationError("Ozon readback item is invalid")
    except Exception:
        pending = any(
            fact.outcome in {"ACCEPTED", "UNKNOWN"}
            for fact in dispatch_facts.values()
        )
        return _result(
            "PROCESSING" if pending else "FAILED",
            dispatch_attempted=attempted,
            readback_completed=False,
            external_write_count=external_write_count,
            requires_human_action=not pending,
        )

    expected_ids = set(offer_ids)
    by_offer: dict[str, list[Mapping[str, Any]]] = {offer_id: [] for offer_id in offer_ids}
    unexpected = False
    for item in items:
        offer_id = str(item.get("offer_id") or "").strip()
        if offer_id not in expected_ids:
            unexpected = True
            continue
        by_offer[offer_id].append(item)

    statuses: list[str] = []
    for variant in variants:
        matches = by_offer[variant["offer_id"]]
        if len(matches) > 1:
            statuses.append("FAILED")
            continue
        statuses.append(
            _classify_variant(
                variant,
                matches[0] if matches else None,
                dispatch_facts[variant["offer_id"]],
            )
        )
    if unexpected:
        statuses.append("FAILED")
    if local_stop_index is not None:
        import json

        prefix_verified = all(status == "PUBLISHED" for status in statuses[:local_stop_index])
        unresolved = unknown_write_count or any(status == "PROCESSING" for status in statuses[:local_stop_index])
        return _result(
            "PROCESSING" if unresolved else "FAILED",
            dispatch_attempted=attempted,
            readback_completed=True,
            external_write_count=external_write_count,
            requires_human_action=True,
            stage="PREPARATION",
            provider_code="ozon_variant_suffix_not_attempted",
            # Positions refer to the immutable snapshot, avoiding product/SKU
            # text in the redacted run report. No attempt is inferred from an
            # absent official listing: the loop itself proves this suffix.
            provider_reason=json.dumps({"unsent_from":local_stop_index,
                "variant_count":len(variants),
                "verified_prefix":local_stop_index if prefix_verified and not unexpected else None},
                separators=(",", ":")),
        )
    if all(status == "PUBLISHED" for status in statuses):
        target_status = "PUBLISHED"
    elif any(status == "FAILED" for status in statuses):
        target_status = "FAILED"
    else:
        target_status = "PROCESSING"
    rejected_facts = [
        fact for fact in dispatch_facts.values() if fact.outcome == "REJECTED"
    ]
    if target_status == "FAILED" and not items and len(rejected_facts) == len(variants):
        fact = rejected_facts[0]
        return _result(
            target_status,
            dispatch_attempted=True,
            readback_completed=True,
            external_write_count=external_write_count,
            requires_human_action=True,
            stage="DISPATCH",
            provider_code=fact.provider_code or "ozon_dispatch_rejected",
            provider_reason=fact.provider_reason or "Ozon import was rejected",
        )
    if target_status == "PUBLISHED" and stock_required:
        return _complete_governed_stock(
            variants=variants,
            update_stocks=update_stocks,
            readback_stocks=readback_stocks,
            before_stock_update=before_stock_update,
            prior_external_write_count=external_write_count,
            prior_dispatch_attempted=True,
            prepare_stock_update=prepare_stock_update,
        )
    return _result(
        target_status,
        dispatch_attempted=True,
        readback_completed=True,
        external_write_count=external_write_count,
        requires_human_action=target_status == "FAILED",
    )


def build_ozon_v4_executor(
    *,
    dispatch_variant: DispatchVariant,
    readback_variants: ReadbackVariants,
    update_stocks: UpdateStocks | None = None,
    readback_stocks: ReadbackStocks | None = None,
    prepare_stock_update: PrepareStockUpdate | None = None,
    official_profile_resolver: OfficialProfileResolver | None = None,
    localized_copy_resolver: LocalizedCopyResolver | None = None,
    catalog_account_resolver: Callable[[], Mapping[str, str]] | None = None,
    catalog_observer: Callable | None = None,
) -> Callable[[object], dict[str, Any]]:
    """Bind thin provider transports to the shared runner callable shape."""

    if not callable(dispatch_variant) or not callable(readback_variants):
        raise TypeError("Ozon provider transports must be callable")

    def execute(request: object) -> dict[str, Any]:
        if getattr(request, "platform", None) != "OZON":
            raise OzonApprovedPublicationError("publication request platform conflicts")
        raw_targets = getattr(request, "target_labels", None)
        if not isinstance(raw_targets, tuple):
            raise OzonApprovedPublicationError("publication request target scope is invalid")
        snapshot = getattr(request, "snapshot", None)
        sink=getattr(request,'catalog_sink',None);account=None;projected=[]
        if sink is not None:
            try:
                if catalog_account_resolver is None:raise ValueError('ozon_account_scope_unavailable')
                account=dict(catalog_account_resolver())
            except Exception:
                return _result('FAILED',dispatch_attempted=False,readback_completed=False,external_write_count=0,requires_human_action=True,stage='PREPARATION',provider_code='ozon_account_scope_unavailable',provider_reason='Exact Ozon account scope is unavailable')
        def projected_facts(variants):
            projected.extend(variants)
            if sink is not None:sink.prepare_ozon(request,account,variants)
        result = execute_ozon_v4_publication(
            snapshot,
            target_labels=raw_targets,
            official_profile_resolver=official_profile_resolver,
            localized_copy_resolver=localized_copy_resolver,
            dispatch_variant=dispatch_variant,
            readback_variants=readback_variants,
            update_stocks=update_stocks,
            readback_stocks=readback_stocks,
            prepare_stock_update=prepare_stock_update,
            before_dispatch=(
                (
                    lambda _variant: request.write_budget_ledger.reserve_shared(
                        "import_variant"
                    )
                )
                if getattr(request, "write_budget_ledger", None) is not None
                else None
            ),
            before_stock_update=(
                (
                    lambda _updates: request.write_budget_ledger.reserve_shared(
                        "update_stock"
                    )
                )
                if getattr(request, "write_budget_ledger", None) is not None
                else None
            ),
            read_before_dispatch=True,
            after_projection=projected_facts,
        )
        if sink is not None and result['targets'][0]['status']=='PUBLISHED':
            try:
                if catalog_observer is None:raise ValueError('ozon_catalog_query_adapter_unavailable')
                sink.capture(request,catalog_observer(tuple(projected)))
            except Exception:
                sink.failures.append({'run_id':request.run_id,'code':'CATALOG_EVIDENCE_PERSISTENCE_FAILED'})
        return result

    return execute


__all__ = [
    "OZON_TARGET",
    "OzonApprovedPublicationError",
    "OzonDispatchFact",
    "OzonStockDispatchFact",
    "build_ozon_v4_executor",
    "execute_ozon_v4_publication",
    "project_ozon_v4_variants",
]
