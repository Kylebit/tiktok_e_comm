"""MX/UK 派单：同 TK 链接多 SKU 必须整组审批 + 整组 publish。"""
from __future__ import annotations

from typing import Any, Callable

from modules.catalog.sku_key import tk_match_key
from modules.catalog.tk_sku_groups import (
    build_tk_group_index,
    collapse_match_keys_to_units,
    expand_match_keys,
    expand_skip_keys,
    group_info_for_match_key,
)
from modules.miaoshou.mx_migrate import collect_master_images_and_product, fetch_tiktok_product


def _variant_label(sku: dict) -> str:
    attrs = sku.get("sales_attributes") or []
    if attrs:
        return str(attrs[0].get("value_name") or "").strip()
    return ""


def load_ph_variants(match_keys: list[str], master_product_id: str, master_region: str) -> dict[str, dict]:
    product = fetch_tiktok_product(master_product_id, region=master_region)
    out: dict[str, dict] = {}
    want = {str(k).zfill(4)[-4:] for k in match_keys}
    for sku in product.get("skus") or []:
        mk = tk_match_key(sku.get("seller_sku") or "")
        if mk in want:
            out[mk] = {
                "match_key": mk,
                "seller_sku": (sku.get("seller_sku") or "").strip(),
                "model_name": _variant_label(sku),
            }
    missing = [k for k in sorted(want) if k not in out]
    if missing:
        raise RuntimeError(f"母版缺少对齐码: {', '.join(missing)}")
    return out


def scan_ready_units(
    pool: list[str],
    *,
    limit: int,
    skip: set[str],
    prep_fn: Callable[..., dict],
    rate: float,
    index: dict[str, dict] | None = None,
) -> tuple[list[list[str]], list[dict]]:
    """按「搬运单元」计数：同链接多 SKU 算 1 个单元。"""
    idx = index if index is not None else build_tk_group_index()
    skip = expand_skip_keys(skip, index=idx)
    ready_units: list[list[str]] = []
    skipped: list[dict] = []
    consumed: set[str] = set()

    for raw in pool:
        if len(ready_units) >= limit:
            break
        mk = str(raw).zfill(4)[-4:]
        if mk in skip or mk in consumed:
            continue

        info = group_info_for_match_key(mk, index=idx)
        unit = list(info["match_keys"]) if info else [mk]
        if any(k in skip or k in consumed for k in unit):
            consumed.update(unit)
            continue

        unit_skipped: list[dict] = []
        all_ready = True
        for u in unit:
            prep = prep_fn(u, rate=rate)
            if prep.get("status") != "ready":
                all_ready = False
                unit_skipped.append(
                    {
                        "mk": u,
                        "reason": prep.get("status") or prep.get("reason"),
                        "pid": prep.get("product_id"),
                        "group": unit if len(unit) > 1 else None,
                    }
                )
        if all_ready:
            ready_units.append(unit)
            consumed.update(unit)
        else:
            skipped.extend(unit_skipped)
            consumed.update(unit)
    return ready_units, skipped


def build_mx_group_card(match_keys: list[str], *, rate: float):
    from modules.miaoshou.mx_confirm import create_group_confirm_card
    from scripts.mx_pop_pricing import KNOWN_BY_MATCH_KEY, quote_match_key
    from scripts.orbit_mx_migrate_prep import catalog_row, find_common_id

    keys = [str(k).zfill(4)[-4:] for k in match_keys]
    primary = keys[0]
    row = catalog_row(primary)
    if not row:
        raise RuntimeError(f"{primary} 无目录/成本")
    master_product_id = str(row["product_id"])
    master_region = row["region"] or "PH"
    variant_by_mk = load_ph_variants(keys, master_product_id, master_region)
    variant_quotes: list[tuple[Any, str, str]] = []
    for mk in keys:
        v = variant_by_mk[mk]
        q = quote_match_key(mk, cny_mxn=rate)
        variant_quotes.append((q, v["seller_sku"], v.get("model_name") or mk))
    urls, product = collect_master_images_and_product(master_product_id, region=master_region)
    common_id = find_common_id(master_product_id, mk=primary, seller_sku=str(row["seller_sku"]))
    package_cm = str(quote_match_key(primary, cny_mxn=rate).package_cm)
    known = KNOWN_BY_MATCH_KEY.get(primary, {})
    if known.get("l"):
        package_cm = f"{known['l']}×{known['w']}×{known['h']}"
    card = create_group_confirm_card(
        match_keys=keys,
        collect_box_detail_id=int(common_id or 0),
        master_product_id=master_product_id,
        master_region=master_region,
        product_name=str(product.get("title") or primary),
        main_image_url=urls[0] if urls else "",
        package_cm=package_cm,
        variant_quotes=variant_quotes,
    )
    return card, common_id


def build_uk_group_card(match_keys: list[str], *, rate: float):
    from modules.miaoshou.uk_confirm import create_group_confirm_card
    from scripts.mx_pop_pricing import KNOWN_BY_MATCH_KEY
    from scripts.orbit_uk_migrate_prep import catalog_row, find_common_id
    from scripts.uk_pop_pricing import quote_match_key

    keys = [str(k).zfill(4)[-4:] for k in match_keys]
    primary = keys[0]
    row = catalog_row(primary)
    if not row:
        raise RuntimeError(f"{primary} 无目录/成本")
    master_product_id = str(row["product_id"])
    master_region = row["region"] or "PH"
    variant_by_mk = load_ph_variants(keys, master_product_id, master_region)
    variant_quotes: list[tuple[Any, str, str]] = []
    for mk in keys:
        v = variant_by_mk[mk]
        q = quote_match_key(mk, cny_gbp=rate)
        variant_quotes.append((q, v["seller_sku"], v.get("model_name") or mk))
    urls, product = collect_master_images_and_product(master_product_id, region=master_region)
    common_id = find_common_id(master_product_id, mk=primary, seller_sku=str(row["seller_sku"]))
    package_cm = str(quote_match_key(primary, cny_gbp=rate).package_cm)
    known = KNOWN_BY_MATCH_KEY.get(primary, {})
    if known.get("l"):
        package_cm = f"{known['l']}×{known['w']}×{known['h']}"
    card = create_group_confirm_card(
        match_keys=keys,
        collect_box_detail_id=int(common_id or 0),
        master_product_id=master_product_id,
        master_region=master_region,
        product_name=str(product.get("title") or primary),
        main_image_url=urls[0] if urls else "",
        package_cm=package_cm,
        variant_quotes=variant_quotes,
    )
    return card, common_id


def queue_mx_unit(
    unit: list[str],
    *,
    rate: float,
    build_single,
) -> dict[str, Any]:
    from modules.miaoshou.mx_confirm import _write, _write_group  # noqa: PLC2701
    from modules.miaoshou.mx_web_approval import mx_approval_url

    if len(unit) >= 2:
        card, common_id = build_mx_group_card(unit, rate=rate)
        if common_id:
            card.collect_box_detail_id = int(common_id)
        _write_group(card)
        label = f"{unit[0]}–{unit[-1]}"
        return {
            "kind": "group",
            "mk": label,
            "match_keys": unit,
            "token": card.token,
            "list_mxn": [v.list_price_ceil_mxn for v in card.variants],
            "common_id": common_id,
            "web_url": mx_approval_url(card.token),
        }

    mk = unit[0]
    card, common_id = build_single(mk, rate=rate)
    if common_id:
        card.collect_box_detail_id = common_id  # type: ignore[misc]
    _write(card)
    return {
        "kind": "single",
        "mk": mk,
        "match_keys": [mk],
        "token": card.token,
        "list_mxn": card.list_price_ceil_mxn,
        "common_id": common_id,
        "web_url": mx_approval_url(card.token),
    }


def queue_uk_unit(
    unit: list[str],
    *,
    rate: float,
    build_single,
) -> dict[str, Any]:
    from modules.miaoshou.uk_confirm import _write, _write_group  # noqa: PLC2701
    from modules.miaoshou.uk_web_approval import uk_approval_url

    if len(unit) >= 2:
        card, common_id = build_uk_group_card(unit, rate=rate)
        if common_id:
            card.collect_box_detail_id = int(common_id)
        _write_group(card)
        label = f"{unit[0]}–{unit[-1]}"
        return {
            "kind": "group",
            "mk": label,
            "match_keys": unit,
            "token": card.token,
            "list_gbp": [v.list_price_ceil_gbp for v in card.variants],
            "common_id": common_id,
            "web_url": uk_approval_url(card.token),
        }

    mk = unit[0]
    card, common_id = build_single(mk, rate=rate)
    if common_id:
        card.collect_box_detail_id = common_id  # type: ignore[misc]
    _write(card)
    return {
        "kind": "single",
        "mk": mk,
        "match_keys": [mk],
        "token": card.token,
        "list_gbp": card.list_price_ceil_gbp,
        "common_id": common_id,
        "web_url": uk_approval_url(card.token),
    }


def resolve_units(
    match_keys: list[str] | None,
    *,
    prefix: str,
    count: int,
    discover_fn: Callable[..., tuple[list[str], list[dict]]],
) -> tuple[list[list[str]], list[dict], list[str]]:
    """显式 keys 先 expand + collapse；自动发现走 scan_ready_units 由调用方处理。"""
    if not match_keys:
        return [], [], []
    expanded = expand_match_keys(match_keys)
    units = collapse_match_keys_to_units(expanded)
    flat = [mk for unit in units for mk in unit]
    return units, [], flat
