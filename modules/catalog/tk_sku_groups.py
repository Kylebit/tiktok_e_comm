"""同一 TikTok product_id 下多 SKU（单链接多规格）索引。"""
from __future__ import annotations

import sqlite3
from typing import Any

from core.config import ROOT
from modules.catalog.sku_key import parse_search_key, tk_match_key


def build_tk_group_index(conn: sqlite3.Connection | None = None) -> dict[str, dict[str, Any]]:
    """同一 TK product_id 下多 SKU → 整组对齐码（≥2）。"""
    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(ROOT / "data" / "shop.db")
        conn.row_factory = sqlite3.Row

    by_product: dict[tuple[str, str], set[str]] = {}
    for r in conn.execute(
        """SELECT product_id, shop_cipher, seller_sku FROM products
           WHERE status = 'ACTIVATE' AND seller_sku != ''"""
    ):
        pid = (r["product_id"] or "").strip()
        cipher = (r["shop_cipher"] or "").strip()
        if not pid or not cipher:
            continue
        mk = tk_match_key(r["seller_sku"] or "")
        if not mk:
            continue
        by_product.setdefault((pid, cipher), set()).add(mk)

    from modules.shopee.global_sku_map import global_item_id_for_match_key, load_map

    out: dict[str, dict[str, Any]] = {}
    for keys in by_product.values():
        if len(keys) < 2:
            continue
        sorted_keys = sorted(keys)
        gid = global_item_id_for_match_key(sorted_keys[0]) or ""
        info = {
            "match_keys": sorted_keys,
            "size": len(sorted_keys),
            "global_item_id": gid,
            "primary_key": sorted_keys[0],
        }
        for k in sorted_keys:
            out[k] = info

    for _gid, entry in load_map().items():
        if not isinstance(entry, dict):
            continue
        extra = entry.get("match_keys") or []
        parsed = sorted({parse_search_key(str(mk)) for mk in extra if parse_search_key(str(mk))})
        if len(parsed) < 2:
            continue
        info = {
            "match_keys": parsed,
            "size": len(parsed),
            "global_item_id": str(_gid),
            "primary_key": parsed[0],
        }
        for k in parsed:
            out.setdefault(k, info)

    if own_conn:
        conn.close()
    return out


def group_info_for_match_key(match_key: str, *, index: dict[str, dict[str, Any]] | None = None) -> dict[str, Any] | None:
    mk = str(match_key).zfill(4)[-4:]
    idx = index if index is not None else build_tk_group_index()
    info = idx.get(mk)
    if info and info.get("size", 0) >= 2:
        return info
    return None


def collapse_match_keys_to_units(
    match_keys: list[str], *, index: dict[str, dict[str, Any]] | None = None
) -> list[list[str]]:
    """同链接多规格合并为一个搬运单元，禁止拆成多张审批卡。"""
    idx = index if index is not None else build_tk_group_index()
    seen_group: set[tuple[str, ...]] = set()
    seen_key: set[str] = set()
    units: list[list[str]] = []

    for raw in match_keys:
        mk = str(raw).zfill(4)[-4:]
        if mk in seen_key:
            continue
        info = group_info_for_match_key(mk, index=idx)
        if info:
            gkeys = tuple(info["match_keys"])
            if gkeys in seen_group:
                continue
            seen_group.add(gkeys)
            for k in gkeys:
                seen_key.add(k)
            units.append(list(gkeys))
        else:
            seen_key.add(mk)
            units.append([mk])
    return units


def expand_match_keys(match_keys: list[str], *, index: dict[str, dict[str, Any]] | None = None) -> list[str]:
    """给定部分对齐码时，自动补全同链接 siblings。"""
    idx = index if index is not None else build_tk_group_index()
    out: list[str] = []
    seen: set[str] = set()
    for raw in match_keys:
        mk = str(raw).zfill(4)[-4:]
        info = group_info_for_match_key(mk, index=idx)
        keys = info["match_keys"] if info else [mk]
        for k in keys:
            if k not in seen:
                seen.add(k)
                out.append(k)
    return out


def expand_skip_keys(skip: set[str], *, index: dict[str, dict[str, Any]] | None = None) -> set[str]:
    """跳过集合含组内任一 SKU 时，整组跳过。"""
    idx = index if index is not None else build_tk_group_index()
    out = {str(k).zfill(4)[-4:] for k in skip}
    for mk in list(out):
        info = group_info_for_match_key(mk, index=idx)
        if info:
            out.update(info["match_keys"])
    return out
