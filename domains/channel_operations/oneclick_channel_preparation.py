"""Pure, zero-write preparation guards for the one-click channel contract.

This module deliberately imports neither TikTok, Miaoshou nor Shopee clients.
It is the narrow 03 seam that 00 can consume when the final typed dispatch
contract lands: malformed source identity is systemic and must stop a batch
before any claim/create operation can be considered.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json

from domains.product_operations.source_identity import (
    BLOCKED_SOURCE_IDENTITY,
    resolve_source_product_identity,
)


class OneClickPreparationError(ValueError):
    """A pure prepared command cannot be safely formed."""


SYSTEMIC_IDENTITY = "SYSTEMIC_IDENTITY"


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
    payload = {
        "schema_version": "tiktok-miaoshou-source-query/v2",
        "source_identity_class": "CANONICAL_SOURCE_OFFER",
        "source_offer_id": identity.source_offer_id,
        "filter": {"sourceItemIdKeyword": identity.source_offer_id},
        "source_identity_digest": identity.identity_digest,
        "external_writes_performed": [],
    }
    return {**payload, "prepared_digest": _digest(payload)}


def prepare_shopee_plan_native_first_attempt(command: Mapping[str, object]) -> dict[str, object]:
    """Pure plan-native Shopee guard; never reaches legacy match-key paths."""
    forbidden = {"publish_match_key", "_find_tk_for_global", "shop.db.products", "tiktok_api"}
    if not isinstance(command, Mapping) or any(key in command for key in forbidden):
        raise OneClickPreparationError("shopee_plan_native_command_invalid")
    required = ("target_label", "seller_sku", "listing_copy", "images", "parcel", "target_pricing")
    if any(not command.get(key) for key in required):
        raise OneClickPreparationError("shopee_plan_native_command_incomplete")
    target = command.get("target_label")
    if target not in {"shopee:PH", "shopee:MY", "shopee:TH", "shopee:VN"}:
        raise OneClickPreparationError("shopee_target_unsupported")
    payload = {
        "schema_version": "shopee-plan-native-first-attempt/v1",
        "target_label": target,
        "seller_sku": command["seller_sku"],
        "plan_native": True,
        "legacy_tiktok_dependency": False,
        "external_writes_performed": [],
    }
    return {**payload, "prepared_digest": _digest(payload)}
