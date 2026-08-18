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
from typing import Any

from domains.product_operations.approved_publication_snapshot import (
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


DispatchVariant = Callable[[dict[str, Any]], OzonDispatchFact]
ReadbackVariants = Callable[[tuple[str, ...]], Sequence[Mapping[str, Any]]]
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
        raise OzonApprovedPublicationError("approved and official Ozon categories conflict")
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
    title = _text(receipt.get("title"), "Ozon localized title")
    description = _text(receipt.get("description"), "Ozon localized description")

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
    title = _text(product.get("title"), "approved Ozon title")
    description = _text(product.get("description"), "approved Ozon description")
    base_images = _https_urls(product.get("images"), "approved product images")
    target_images = publication_images_for_target(snapshot, OZON_TARGET)
    routed_by_base = dict(zip(base_images, target_images, strict=True))
    category, official_profile = _category_and_profile(
        snapshot, official_profile_resolver
    )
    if official_profile is not None and official_profile["type_id"] == 93785:
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
    if stage not in {"PREPARATION", "DISPATCH", "READBACK"}:
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


def execute_ozon_v4_publication(
    snapshot: Mapping[str, Any],
    *,
    target_labels: tuple[str, ...],
    official_profile_resolver: OfficialProfileResolver | None = None,
    localized_copy_resolver: LocalizedCopyResolver | None = None,
    dispatch_variant: DispatchVariant,
    readback_variants: ReadbackVariants,
) -> dict[str, Any]:
    """Dispatch all variants, then always perform one authoritative readback."""

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

    dispatch_facts: dict[str, OzonDispatchFact] = {}
    accepted_count = 0
    unknown_write_count = False
    for variant in variants:
        offer_id = variant["offer_id"]
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

    pre_submit = [
        fact for fact in dispatch_facts.values()
        if fact.outcome == "PRE_SUBMIT_FAILED"
    ]
    if pre_submit:
        fact = pre_submit[0]
        return _result(
            "FAILED",
            dispatch_attempted=False,
            readback_completed=False,
            external_write_count=0,
            requires_human_action=True,
            stage="PREPARATION",
            provider_code=fact.provider_code or "ozon_preparation_failed",
            provider_reason=fact.provider_reason or "Ozon preparation failed",
        )

    external_write_count = None if unknown_write_count else accepted_count
    offer_ids = tuple(variant["offer_id"] for variant in variants)
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
            dispatch_attempted=True,
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
    official_profile_resolver: OfficialProfileResolver | None = None,
    localized_copy_resolver: LocalizedCopyResolver | None = None,
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
        return execute_ozon_v4_publication(
            snapshot,
            target_labels=raw_targets,
            official_profile_resolver=official_profile_resolver,
            localized_copy_resolver=localized_copy_resolver,
            dispatch_variant=dispatch_variant,
            readback_variants=readback_variants,
        )

    return execute


__all__ = [
    "OZON_TARGET",
    "OzonApprovedPublicationError",
    "OzonDispatchFact",
    "build_ozon_v4_executor",
    "execute_ozon_v4_publication",
    "project_ozon_v4_variants",
]
