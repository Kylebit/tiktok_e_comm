"""Pure, zero-write preparation guards for the one-click channel contract.

This module deliberately imports neither TikTok, Miaoshou nor Shopee clients.
It is the narrow 03 seam that 00 can consume when the final typed dispatch
contract lands: malformed source identity is systemic and must stop a batch
before any claim/create operation can be considered.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
import hashlib
import json
import unicodedata

from domains.product_operations.source_identity import (
    BLOCKED_SOURCE_IDENTITY,
    resolve_source_product_identity,
)


class OneClickPreparationError(ValueError):
    """A pure prepared command cannot be safely formed."""


SYSTEMIC_IDENTITY = "SYSTEMIC_IDENTITY"
SHOPEE_GLOBAL_MASTER_WRITE = "shopee:global_master:update"
SHOPEE_REGIONAL_PUBLISH_WRITE = "shopee:regional_publish"


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_complete_source_pages(pages: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Validate captured source-query pagination without calling Miaoshou."""
    if not isinstance(pages, Sequence) or isinstance(pages, (str, bytes)) or not pages:
        raise OneClickPreparationError("source_query_pages_missing")
    seen_cursors: set[int] = set()
    rows: list[Mapping[str, object]] = []
    terminal_seen = False
    for index, page in enumerate(pages):
        if not isinstance(page, Mapping) or page.get("result") != "success":
            raise OneClickPreparationError("source_query_response_invalid")
        data = page.get("data")
        if not isinstance(data, Mapping):
            raise OneClickPreparationError("source_query_data_invalid")
        page_rows = data.get("detailList", data.get("list"))
        total = data.get("totalCount", data.get("total"))
        if not isinstance(page_rows, list) or type(total) is not int or total < 0:
            raise OneClickPreparationError("source_query_shape_invalid")
        if any(not isinstance(row, Mapping) for row in page_rows):
            raise OneClickPreparationError("source_query_row_invalid")
        rows.extend(page_rows)
        has_next = data.get("hasNextPage")
        next_cursor = data.get("nextPageToken", data.get("nextPage"))
        if has_next is True:
            if type(next_cursor) is not int or next_cursor <= 0 or next_cursor in seen_cursors:
                raise OneClickPreparationError("source_query_cursor_invalid")
            seen_cursors.add(next_cursor)
            continue
        if has_next is not False or index != len(pages) - 1:
            raise OneClickPreparationError("source_query_pagination_incomplete")
        if total != len(rows):
            raise OneClickPreparationError("source_query_total_mismatch")
        terminal_seen = True
    if not terminal_seen:
        raise OneClickPreparationError("source_query_pagination_incomplete")
    return {
        "complete": True,
        "row_count": len(rows),
        "rows_digest": _digest({"row_count": len(rows), "rows": list(rows)}),
    }


def prepare_tiktok_source_query(
    *,
    collect_box: Mapping[str, object] | None = None,
    precollect: Mapping[str, object] | None = None,
    source_record: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Return a source-offer-only query from 01's canonical identity seam."""
    resolution = resolve_source_product_identity(
        collect_box=collect_box,
        precollect=precollect,
        source_record=source_record,
    )
    if not resolution.ready or resolution.identity is None:
        raise OneClickPreparationError(
            f"{SYSTEMIC_IDENTITY}: {BLOCKED_SOURCE_IDENTITY}"
        )
    identity = resolution.identity
    return prepare_tiktok_source_query_from_canonical_identity(identity.payload())


def prepare_tiktok_source_query_from_canonical_identity(
    identity: Mapping[str, object],
) -> dict[str, object]:
    """Build a Miaoshou source query from 00's immutable 01 identity.

    ``source_item_code`` is intentionally ignored: it is display/merchant
    data, never a valid ``sourceItemIdKeyword`` identity.
    """
    if not isinstance(identity, Mapping):
        raise OneClickPreparationError(
            f"{SYSTEMIC_IDENTITY}: {BLOCKED_SOURCE_IDENTITY}"
        )
    source_offer_id = identity.get("source_offer_id")
    source_digest = identity.get("identity_digest")
    if (
        type(source_offer_id) is not str
        or not source_offer_id.isascii()
        or not source_offer_id.isdecimal()
        or int(source_offer_id) <= 0
        or type(source_digest) is not str
        or not _source_identity_digest(source_digest)
    ):
        raise OneClickPreparationError(
            f"{SYSTEMIC_IDENTITY}: {BLOCKED_SOURCE_IDENTITY}"
        )
    payload = {
        "schema_version": "tiktok-miaoshou-source-query/v2",
        "source_identity_class": "CANONICAL_SOURCE_OFFER",
        "source_offer_id": source_offer_id,
        "filter": {"sourceItemIdKeyword": source_offer_id},
        "source_identity_digest": source_digest,
        "external_writes_performed": [],
    }
    return {**payload, "prepared_digest": _digest(payload)}


def prepare_shopee_plan_native_first_attempt(command: Mapping[str, object]) -> dict[str, object]:
    """Pure plan-native Shopee guard; never reaches legacy match-key paths."""
    forbidden = {"publish_match_key", "_find_tk_for_global", "shop.db.products", "tiktok_api"}
    if not isinstance(command, Mapping) or any(key in command for key in forbidden):
        raise OneClickPreparationError("shopee_plan_native_command_invalid")
    target = command.get("target_label")
    if target not in {"shopee:PH", "shopee:MY", "shopee:TH", "shopee:VN"}:
        raise OneClickPreparationError("shopee_target_unsupported")
    seller_sku = command.get("seller_sku")
    model_sku = command.get("model_sku")
    copy = command.get("listing_copy")
    policy = command.get("policy")
    if (
        type(seller_sku) is not str or not seller_sku.strip()
        or type(model_sku) is not str or not model_sku.strip()
        or not isinstance(copy, Mapping)
        or type(copy.get("title")) is not str
        or type(copy.get("description")) is not str
        or not isinstance(policy, Mapping)
        or type(policy.get("schema_version")) is not str or not policy["schema_version"].strip()
        or not _sha256(policy.get("policy_digest"))
    ):
        raise OneClickPreparationError("shopee_plan_native_command_incomplete")
    title = unicodedata.normalize("NFC", copy["title"].strip())
    description = copy["description"]
    if not title or not description.strip():
        raise OneClickPreparationError("shopee_plan_native_command_incomplete")
    from shared_platform.target_scoped_release_contracts import (
        approved_shopee_channel_master_digest,
        approved_shopee_copy_digest,
        approved_source_image_manifest_digest,
    )
    copy_digest = approved_shopee_copy_digest(title, description)
    images = command.get("images")
    if (
        not isinstance(images, list)
        or not images
        or len(images) > 9
    ):
        raise OneClickPreparationError("shopee_images_invalid")
    normalized_images: list[dict[str, object]] = []
    for index, image in enumerate(images, start=1):
        if (
            not isinstance(image, Mapping)
            or type(image.get("position")) is not int
            or image["position"] != index
            or type(image.get("image_url")) is not str
            or not image["image_url"].strip()
        ):
            raise OneClickPreparationError("shopee_images_invalid")
        normalized_images.append({"position": index, "image_url": image["image_url"].strip()})
    urls = [str(image["image_url"]) for image in normalized_images]
    if len(urls) != len(set(urls)):
        raise OneClickPreparationError("shopee_images_invalid")
    approved_master_digest = approved_shopee_channel_master_digest(
        title, description, urls
    )
    source_image_manifest_digest = approved_source_image_manifest_digest(
        urls
    )
    parcel = command.get("parcel")
    if not isinstance(parcel, Mapping):
        raise OneClickPreparationError("shopee_parcel_invalid")
    weight = _positive_decimal(parcel.get("weight_kg"))
    package = parcel.get("package_cm")
    if not isinstance(package, list) or len(package) != 3:
        raise OneClickPreparationError("shopee_parcel_invalid")
    dimensions = [_positive_decimal(value) for value in package]
    pricing = command.get("target_pricing")
    if not isinstance(pricing, Mapping):
        raise OneClickPreparationError("shopee_pricing_invalid")
    price = _positive_decimal(pricing.get("local_original_price"))
    currency = pricing.get("currency")
    expected_currency = {
        "shopee:PH": "PHP", "shopee:MY": "MYR",
        "shopee:TH": "THB", "shopee:VN": "VND",
    }[target]
    if (
        type(currency) is not str
        or currency != expected_currency
    ):
        raise OneClickPreparationError("shopee_pricing_invalid")
    approved = {
        "target_label": target,
        "seller_sku": seller_sku.strip(),
        "model_sku": model_sku.strip(),
        "listing_copy": {
            "title": title,
            "description": description,
            "approved_copy_digest": copy_digest,
            "approved_master_digest": approved_master_digest,
        },
        "ordered_images": normalized_images,
        "approved_source_image_manifest_digest": (
            source_image_manifest_digest
        ),
        "parcel": {"weight_kg": str(weight), "package_cm": [str(value) for value in dimensions]},
        "target_pricing": {"local_original_price": str(price), "currency": currency},
        "policy": {"schema_version": policy["schema_version"].strip(), "policy_digest": policy["policy_digest"]},
    }
    global_create = command.get("global_create")
    if global_create is not None:
        if (
            len(title) > 120
            or len(description) < 500
            or len(description) > 3000
        ):
            raise OneClickPreparationError(
                "shopee_global_create_copy_invalid"
            )
        approved["global_create"] = _normalize_shopee_global_create(
            global_create
        )
    payload = {
        "schema_version": "shopee-plan-native-first-attempt/v2",
        "approved": approved,
        "plan_native": True,
        "legacy_tiktok_dependency": False,
        "external_writes_performed": [],
    }
    return {**payload, "prepared_digest": _digest(payload)}


def _normalize_shopee_global_create(value: object) -> dict[str, object]:
    """Validate every field written by the plan-native global-create call."""
    if not isinstance(value, Mapping):
        raise OneClickPreparationError("shopee_global_create_facts_invalid")
    category_id = value.get("category_id")
    attributes = value.get("attribute_list")
    brand = value.get("brand")
    stock = value.get("seller_stock")
    price = _positive_decimal(value.get("original_price_cny"))
    condition = value.get("condition")
    pre_order = value.get("pre_order")
    if (
        type(category_id) is not int
        or category_id <= 0
        or not isinstance(attributes, list)
        or not attributes
        or any(not isinstance(row, Mapping) for row in attributes)
        or not isinstance(brand, Mapping)
        or not isinstance(stock, Mapping)
        or condition != "NEW"
        or not isinstance(pre_order, Mapping)
    ):
        raise OneClickPreparationError("shopee_global_create_facts_invalid")
    normalized_attributes: list[dict[str, object]] = []
    seen_attribute_ids: set[int] = set()
    for row in attributes:
        attribute_id = row.get("attribute_id")
        values = row.get("attribute_value_list")
        if (
            type(attribute_id) is not int
            or attribute_id <= 0
            or attribute_id in seen_attribute_ids
            or not isinstance(values, list)
            or not values
            or any(not isinstance(item, Mapping) for item in values)
        ):
            raise OneClickPreparationError(
                "shopee_global_create_attributes_invalid"
            )
        normalized_values: list[dict[str, object]] = []
        for item in values:
            value_id = item.get("value_id")
            original_name = item.get("original_value_name")
            if not (
                (type(value_id) is int and value_id > 0)
                or (type(original_name) is str and bool(original_name.strip()))
            ):
                raise OneClickPreparationError(
                    "shopee_global_create_attributes_invalid"
                )
            normalized_values.append(dict(item))
        seen_attribute_ids.add(attribute_id)
        normalized_attributes.append(
            {
                **dict(row),
                "attribute_id": attribute_id,
                "attribute_value_list": normalized_values,
            }
        )
    brand_id = brand.get("brand_id")
    brand_name = brand.get("original_brand_name")
    if (
        type(brand_id) is not int
        or brand_id < 0
        or type(brand_name) is not str
        or not brand_name.strip()
    ):
        raise OneClickPreparationError("shopee_global_create_brand_invalid")
    location_id = stock.get("location_id")
    stock_count = stock.get("stock")
    if (
        type(location_id) is not str
        or not location_id.strip()
        or type(stock_count) is not int
        or stock_count <= 0
    ):
        raise OneClickPreparationError("shopee_global_create_stock_invalid")
    days_to_ship = pre_order.get("days_to_ship")
    if type(days_to_ship) is not int or days_to_ship <= 0:
        raise OneClickPreparationError(
            "shopee_global_create_preorder_invalid"
        )
    normalized = {
        "category_id": category_id,
        "attribute_list": normalized_attributes,
        "brand": {
            "brand_id": brand_id,
            "original_brand_name": brand_name.strip(),
        },
        "seller_stock": {
            "location_id": location_id.strip(),
            "stock": stock_count,
        },
        "original_price_cny": str(price),
        "condition": "NEW",
        "pre_order": {"days_to_ship": days_to_ship},
    }
    try:
        return json.loads(
            json.dumps(normalized, ensure_ascii=False, sort_keys=True)
        )
    except (TypeError, ValueError) as error:
        raise OneClickPreparationError(
            "shopee_global_create_facts_invalid"
        ) from error


def _positive_decimal(value: object) -> Decimal:
    if value is None or isinstance(value, bool):
        raise OneClickPreparationError("shopee_numeric_field_invalid")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise OneClickPreparationError("shopee_numeric_field_invalid") from error
    if not number.is_finite() or number <= 0:
        raise OneClickPreparationError("shopee_numeric_field_invalid")
    return number


def _sha256(value: object) -> bool:
    return type(value) is str and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _source_identity_digest(value: str) -> bool:
    """Accept the 01 contract's prefixed digest, never a loose display value."""
    return _sha256(value) or (
        value.startswith("sha256:") and _sha256(value.removeprefix("sha256:"))
    )


def classify_shopee_dispatch_boundary(
    *,
    global_master_state: str,
    regional_state: str,
) -> dict[str, object]:
    """Build a truthful, append-only write receipt for a Shopee attempt.

    ``global_master_state`` is one of ``not_started``, ``accepted`` or
    ``unknown``.  ``regional_state`` is one of ``not_started``, ``accepted``
    or ``unknown``.  An accepted/unknown global operation is never erased by
    a later regional transport, task, parse or logistics failure.
    """

    allowed = {"not_started", "accepted", "unknown"}
    if global_master_state not in allowed or regional_state not in allowed:
        raise OneClickPreparationError("shopee_dispatch_state_invalid")
    writes: list[str] = []
    if global_master_state != "not_started":
        writes.append(SHOPEE_GLOBAL_MASTER_WRITE)
    if regional_state != "not_started":
        writes.append(SHOPEE_REGIONAL_PUBLISH_WRITE)
    uncertain = "unknown" in {global_master_state, regional_state}
    if not writes:
        outcome = "FAILED_PRE_SUBMIT"
    elif uncertain or regional_state != "accepted":
        outcome = "RECONCILIATION_REQUIRED"
    else:
        outcome = "POST_DISPATCH_READBACK_REQUIRED"
    payload = {
        "schema_version": "shopee-oneclick-dispatch-boundary/v1",
        "global_master_state": global_master_state,
        "regional_state": regional_state,
        "outcome": outcome,
        "reconciliation_required": outcome == "RECONCILIATION_REQUIRED",
        "external_writes_performed": writes,
    }
    return {**payload, "receipt_digest": _digest(payload)}


def remaining_shopee_regions(
    target_states: Mapping[str, Mapping[str, object]],
) -> tuple[str, ...]:
    """Return only pristine/incomplete regions; terminal receipts never replay."""

    regions: list[str] = []
    for label in ("shopee:PH", "shopee:MY", "shopee:TH", "shopee:VN"):
        row = target_states.get(label)
        if not isinstance(row, Mapping):
            continue
        if row.get("status") == "PENDING" and row.get("attempts") == 0:
            regions.append(label)
    return tuple(regions)
