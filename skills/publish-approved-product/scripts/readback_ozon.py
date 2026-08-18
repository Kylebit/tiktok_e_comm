#!/usr/bin/env python3
"""DEPRECATED COMPATIBILITY: direct Ozon readback for incident diagnosis."""
from __future__ import annotations

import time
from typing import Any, Mapping

from _common import add_repo_to_path, expected_model_skus
from _readback_cli import run


# Live Seller API readback for Offer 3838599504 confirmed that Ozon returns
# OFFER_VALIDATED after accepting an asynchronous import but before the item is
# fully created.  It is an in-flight state, not a content mismatch.
PROCESSING_STATUSES = {"PROCESSING", "OFFER_VALIDATED", "IMPORTED"}


def readback(
    snapshot: Mapping[str, Any], dispatch: Mapping[str, Any], args: Any
) -> dict[str, Any]:
    attempts = max(1, int(getattr(args, "poll_attempts", 1)))
    interval = max(0.0, float(getattr(args, "poll_interval_seconds", 0.0)))
    fact: dict[str, Any] = {}
    for attempt in range(attempts):
        fact = _readback_once(snapshot, dispatch, args)
        if fact.get("status") != "PROCESSING" or attempt + 1 >= attempts:
            return fact
        time.sleep(interval)
    return fact


def _readback_once(
    snapshot: Mapping[str, Any], dispatch: Mapping[str, Any], args: Any
) -> dict[str, Any]:
    add_repo_to_path(args.repo)
    from modules.ozon.client import ozon_post

    expected = expected_model_skus(snapshot)
    expected_titles = _expected_titles(snapshot)
    expected_prices = _expected_prices(snapshot)
    expected_image_count = len(snapshot.get("content", {}).get("images") or [])
    response = ozon_post(
        "/v3/product/info/list",
        {"offer_id": expected, "limit": max(10, len(expected)), "visibility": "ALL"},
        timeout=int(args.timeout_seconds),
    )
    items = response.get("items") or []
    by_offer = {
        str(row.get("offer_id") or "").strip(): row
        for row in items
        if isinstance(row, dict) and str(row.get("offer_id") or "").strip()
    }
    rows = []
    for sku in expected:
        item = by_offer.get(sku)
        if not item:
            rows.append({"seller_sku": sku, "exists": False, "verified": False, "status": "NOT_FOUND"})
            continue
        statuses = item.get("statuses") if isinstance(item.get("statuses"), dict) else {}
        state = str(statuses.get("status") or "").upper()
        failed = str(statuses.get("status_failed") or "").upper()
        provider_errors = item.get("errors") if isinstance(item.get("errors"), list) else []
        provider_error_codes = []
        for error in provider_errors:
            if not isinstance(error, dict):
                continue
            code = str(error.get("code") or "").strip().upper()
            if code and code not in provider_error_codes:
                provider_error_codes.append(code[:80])
        created = statuses.get("is_created") is True
        item_id = str(item.get("id") or "").strip()
        checks = {
            "identity_from_item_id": bool(item_id),
            "created_from_statuses": created,
            "not_failed": not bool(failed),
            "title_exact": str(item.get("name") or "") == expected_titles.get(sku, ""),
            "price_exact": _numeric_equal(item.get("price"), expected_prices.get(sku)),
            "images_present": len(item.get("images") or []) >= min(expected_image_count, 1),
        }
        verified = all(checks.values())
        rows.append({
            "seller_sku": sku,
            "exists": True,
            "verified": verified,
            "status": state or ("CREATED" if created else "PROCESSING"),
            "item_id": item_id,
            "provider_failure": failed or None,
            "provider_error_codes": provider_error_codes,
            "checks": checks,
        })
    verified_count = sum(row["verified"] is True for row in rows)
    complete = bool(rows) and verified_count == len(rows)
    processing = any(row["status"] in PROCESSING_STATUSES for row in rows)
    return {
        "schema_version": "platform-readback-fact/v1",
        "platform": "ozon",
        "provider": "official_ozon_seller_api",
        "exists": any(row["exists"] for row in rows),
        "verified": complete,
        "complete": complete,
        "status": "VERIFIED" if complete else ("PROCESSING" if processing else "MISMATCH"),
        "expected_count": len(rows),
        "verified_count": verified_count,
        "mismatch": not complete and not processing,
        "variants": rows,
        "retry_safe": not any(row["exists"] for row in rows),
    }


def _platform_title(snapshot: Mapping[str, Any], channel: str) -> str:
    rows = snapshot.get("content", {}).get("platform_titles") or []
    matches = [
        str(row.get("title") or "").strip()
        for row in rows
        if isinstance(row, dict)
        and str(row.get("channel") or "").lower() == channel
        and str(row.get("title") or "").strip()
    ]
    return matches[0] if len(matches) == 1 else ""


def _expected_titles(snapshot: Mapping[str, Any]) -> dict[str, str]:
    """Mirror the deterministic per-SKU title contract used by Ozon dispatch."""

    from modules.ozon.listing_text import (
        build_ozon_tablecloth_title,
        is_tablecloth_title,
    )
    from modules.ozon.tk_variant import parse_variant_dims

    base_title = _platform_title(snapshot, "ozon")
    source_category = str(
        snapshot.get("content", {}).get("source_category", {}).get("name") or ""
    )
    tablecloth = is_tablecloth_title(f"{base_title} {source_category}")
    titles: dict[str, str] = {}
    for row in snapshot.get("skus") or []:
        if not isinstance(row, Mapping):
            continue
        seller_sku = str(row.get("seller_sku") or "").strip()
        if not seller_sku:
            continue
        if not tablecloth:
            titles[seller_sku] = base_title
            continue
        len_cm, wid_cm = parse_variant_dims(str(row.get("option_name") or ""))
        if not len_cm or not wid_cm:
            len_cm, wid_cm = parse_variant_dims(base_title)
        titles[seller_sku] = build_ozon_tablecloth_title(
            base_title,
            len_cm=len_cm,
            wid_cm=wid_cm,
        )
    return titles


def _expected_prices(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    prices = snapshot.get("prices")
    row = prices.get("ozon:RU") if isinstance(prices, dict) else None
    per_sku = row.get("sku_prices") if isinstance(row, dict) else None
    if not isinstance(per_sku, dict):
        return {}
    return {
        str(sku): facts.get("price")
        for sku, facts in per_sku.items()
        if isinstance(facts, dict)
    }


def _numeric_equal(observed: object, expected: object) -> bool:
    if expected is None:
        return False
    try:
        return abs(float(observed) - float(expected)) < 0.000001
    except (TypeError, ValueError):
        return False


if __name__ == "__main__":
    raise SystemExit(run("ozon", readback))
