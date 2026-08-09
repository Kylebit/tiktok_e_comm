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

    def __post_init__(self) -> None:
        if self.outcome not in {"ACCEPTED", "REJECTED", "UNKNOWN"}:
            raise ValueError("Ozon dispatch outcome is invalid")
        if self.task_id is not None and (
            type(self.task_id) is not str
            or not self.task_id
            or self.task_id != self.task_id.strip()
        ):
            raise ValueError("Ozon dispatch task identity is invalid")
        if self.outcome == "ACCEPTED" and self.task_id is None:
            raise ValueError("accepted Ozon dispatch requires a task identity")


DispatchVariant = Callable[[dict[str, Any]], OzonDispatchFact]
ReadbackVariants = Callable[[tuple[str, ...]], Sequence[Mapping[str, Any]]]


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


def project_ozon_v4_variants(
    snapshot: Mapping[str, Any], *, target_labels: tuple[str, ...]
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
    _https_urls(product.get("images"), "approved product images")
    category = _approved_category(snapshot)
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
        images = _https_urls(sku.get("variant_images"), "approved Ozon variant images")
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
    checks = (
        observed.get("name") == expected["title"],
        _same_decimal(observed.get("price"), expected["price"]),
        _same_decimal(observed.get("old_price"), expected["old_price"]),
        isinstance(images, Sequence)
        and not isinstance(images, (str, bytes, bytearray))
        and len(images) == expected["image_count"],
        str(observed.get("category_id") or "") == expected["category"]["id"],
        _same_parcel(observed, expected["parcel"]),
    )
    return "PUBLISHED" if all(checks) else "FAILED"


def _result(
    status: str,
    *,
    dispatch_attempted: bool,
    readback_completed: bool,
    external_write_count: int | None,
    requires_human_action: bool,
) -> dict[str, Any]:
    return {
        "schema_version": PLATFORM_RESULT_SCHEMA_VERSION,
        "platform": "OZON",
        "targets": [{"target_label": OZON_TARGET, "status": status}],
        "dispatch_attempted": dispatch_attempted,
        "readback_completed": readback_completed,
        "external_write_count": external_write_count,
        "requires_human_action": requires_human_action,
    }


def execute_ozon_v4_publication(
    snapshot: Mapping[str, Any],
    *,
    target_labels: tuple[str, ...],
    dispatch_variant: DispatchVariant,
    readback_variants: ReadbackVariants,
) -> dict[str, Any]:
    """Dispatch all variants, then always perform one authoritative readback."""

    try:
        variants = project_ozon_v4_variants(snapshot, target_labels=target_labels)
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
