"""按站点列出 TK / Shopee / Ozon 商品，按 SKU 后四位对齐；多国合并视图。"""

from __future__ import annotations

import re

from core.db import connect, init_db
from modules.catalog.ozon_data import load_ozon_by_key
from modules.catalog.sku_key import (
    SEA_REGIONS,
    parse_search_key,
    shopee_match_key,
    shopee_sku_needs_edit,
    tk_match_key,
    tk_region,
)
from modules.products import costs as cost_mod
from modules.catalog.logistics_weights import weight_index_by_match_key


def _row_tk(r, region: str) -> dict:
    return {
        "platform": "tiktok",
        "region": region,
        "seller_sku": r["seller_sku"] or "",
        "match_key": tk_match_key(r["seller_sku"] or ""),
        "sku_id": r["sku_id"] or "",
        "shop_cipher": r["shop_cipher"] or "",
        "global_product_id": r["global_product_id"] or "",
        "global_sku_id": r["global_sku_id"] or "",
        "product_id": r["product_id"] or "",
        "product_name": r["product_name"] or "",
        "model_name": r["sku_name"] or "",
        "image_url": r["image_url"] or "",
        "price": r["price"],
        "currency": r["currency"] or "",
        "stock": r["stock"],
        "status": r["status"] or "",
    }


def _row_sp(r) -> dict:
    reg = (r["region"] or "?").upper()
    return {
        "platform": "shopee",
        "region": reg,
        "seller_sku": r["seller_sku"] or "",
        "match_key": shopee_match_key(r["seller_sku"] or ""),
        "sku_id": r["model_id"] or "",
        "product_id": r["item_id"] or "",
        "product_name": r["product_name"] or "",
        "model_name": r["model_name"] or "",
        "image_url": r["image_url"] or "",
        "price": r["price"],
        "currency": r["currency"] or "",
        "stock": r["stock"],
        "status": r["status"] or "",
        "shop_id": r["shop_id"],
    }


def _row_sp_global(row: dict) -> dict:
    reg = (row.get("region") or "?").upper()
    sk = row.get("seller_sku") or ""
    return {
        "platform": "shopee",
        "region": reg,
        "seller_sku": sk,
        "match_key": shopee_match_key(sk),
        "sku_id": row.get("model_id") or "",
        "product_id": row.get("product_id") or "",
        "product_name": row.get("product_name") or "",
        "model_name": row.get("model_name") or "",
        "image_url": row.get("image_url") or "",
        "price": row.get("price"),
        "currency": row.get("currency") or "",
        "stock": row.get("stock"),
        "status": row.get("status") or "GLOBAL_MAP",
        "shop_id": row.get("shop_id") or 0,
        "global_item_id": row.get("global_item_id") or "",
        "source": row.get("source") or "cnsc_global_map",
    }


def _build_tk_group_index(conn) -> dict[str, dict]:
    from modules.catalog.tk_sku_groups import build_tk_group_index

    return build_tk_group_index(conn)


def _merge_platform_rows(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    first = rows[0]
    image_url = next((r["image_url"] for r in rows if r.get("image_url")), "")
    name = next((r["product_name"] for r in rows if r.get("product_name")), "")
    return {
        "image_url": image_url,
        "product_name": name,
        "regions": rows,
        "region_count": len({r["region"] for r in rows}),
    }


def _count_tk_missing_seller_sku(conn) -> int:
    row = conn.execute(
        """SELECT COUNT(*) AS n FROM (
               SELECT DISTINCT sku_id, shop_cipher FROM products
               WHERE status = 'ACTIVATE' AND (seller_sku IS NULL OR seller_sku = '')
           )"""
    ).fetchone()
    return int(row["n"] or 0) if row else 0


def _list_tk_missing_seller_sku(
    conn,
    reg_filter: str,
    search_raw: str = "",
) -> list[dict]:
    """TikTok 已同步但商家 SKU 为空的商品（每 sku_id+shop 一行）。"""
    raw = (search_raw or "").strip()
    sku_id_q = raw if raw and re.fullmatch(r"\d{10,}", raw) else ""
    search_key = parse_search_key(raw) if raw and not sku_id_q else ""
    if search_key:
        return []

    items: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for r in conn.execute(
        """SELECT sku_id, shop_cipher, product_id, seller_sku, product_name, sku_name,
                  image_url, price, currency, stock, status,
                  global_product_id, global_sku_id
           FROM products
           WHERE status = 'ACTIVATE' AND (seller_sku IS NULL OR seller_sku = '')"""
    ):
        reg = tk_region(r["shop_cipher"])
        if reg_filter and reg != reg_filter:
            continue
        sid = (r["sku_id"] or "").strip()
        cipher = (r["shop_cipher"] or "").strip()
        if not sid:
            continue
        uid = (sid, cipher)
        if uid in seen:
            continue
        seen.add(uid)
        if sku_id_q:
            if sid != sku_id_q and sku_id_q not in sid:
                continue
        elif raw:
            pname = (r["product_name"] or "").lower()
            sname = (r["sku_name"] or "").lower()
            q = raw.lower()
            if raw not in sid and q not in pname and q not in sname:
                continue
        tk_row = _row_tk(r, reg)
        tk_m = _merge_platform_rows([tk_row])
        items.append(
            {
                "match_key": "",
                "missing_tk_sku": True,
                "tk_sku_id": sid,
                "shop_cipher": cipher,
                "product_id": (r["product_id"] or "").strip(),
                "matched": {"tiktok": True, "shopee": False, "ozon": False},
                "matched_count": 1,
                "cost_cny": None,
                "cost_sku_ids": [sid],
                "tiktok": tk_m,
                "shopee": None,
                "ozon": None,
            }
        )
    items.sort(
        key=lambda x: (
            (x.get("tiktok") or {}).get("product_name") or "",
            x.get("tk_sku_id") or "",
        )
    )
    return items


def _count_shopee_needs_seller_sku(conn) -> int:
    n = 0
    for r in conn.execute("SELECT seller_sku FROM shopee_products"):
        if shopee_sku_needs_edit(r["seller_sku"]):
            n += 1
    return n


def _list_shopee_needs_seller_sku(
    conn,
    reg_filter: str,
    search_raw: str = "",
) -> list[dict]:
    """Shopee 规格货号为空或非标准 4 位码（每 model_id+shop 一行）。"""
    raw = (search_raw or "").strip()
    id_q = raw if raw and re.fullmatch(r"\d{8,}", raw) else ""
    search_key = parse_search_key(raw) if raw and not id_q else ""
    if search_key:
        return []

    items: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for r in conn.execute(
        """SELECT model_id, shop_id, item_id, seller_sku, product_name, model_name,
                  image_url, price, currency, stock, status, region
           FROM shopee_products"""
    ):
        sk = (r["seller_sku"] or "").strip()
        if not shopee_sku_needs_edit(sk):
            continue
        reg = (r["region"] or "?").upper()
        if reg_filter and reg != reg_filter:
            continue
        mid = (r["model_id"] or "").strip()
        shop_id = int(r["shop_id"] or 0)
        if not mid:
            continue
        uid = (mid, shop_id)
        if uid in seen:
            continue
        seen.add(uid)
        item_id = (r["item_id"] or "").strip()
        if id_q:
            if id_q != mid and id_q != item_id and id_q not in mid and id_q not in item_id:
                continue
        elif raw:
            pname = (r["product_name"] or "").lower()
            mname = (r["model_name"] or "").lower()
            q = raw.lower()
            if raw not in mid and raw not in item_id and q not in pname and q not in mname and q not in sk.lower():
                continue
        sp_row = _row_sp(r)
        sp_m = _merge_platform_rows([sp_row])
        items.append(
            {
                "match_key": "",
                "missing_sp_sku": True,
                "sp_model_id": mid,
                "shop_id": shop_id,
                "item_id": item_id,
                "current_seller_sku": sk,
                "matched": {"tiktok": False, "shopee": True, "ozon": False},
                "matched_count": 1,
                "cost_cny": None,
                "cost_sku_ids": [],
                "tiktok": None,
                "shopee": sp_m,
                "ozon": None,
            }
        )
    items.sort(
        key=lambda x: (
            (x.get("shopee") or {}).get("product_name") or "",
            x.get("sp_model_id") or "",
        )
    )
    return items


def _cost_index() -> tuple[dict[str, float], dict[str, list[str]]]:
    """match_key → cost_cny；match_key → [sku_id]。"""
    init_db()
    conn = connect()
    costs = {r["sku_id"]: float(r["cost_cny"]) for r in conn.execute(
        "SELECT sku_id, cost_cny FROM sku_costs WHERE cost_cny > 0"
    )}
    key_to_skus: dict[str, list[str]] = {}
    for r in conn.execute(
        """SELECT sku_id, seller_sku FROM products
           WHERE seller_sku != '' AND status = 'ACTIVATE'"""
    ):
        key = tk_match_key(r["seller_sku"])
        if not key:
            continue
        key_to_skus.setdefault(key, [])
        sid = r["sku_id"] or ""
        if sid and sid not in key_to_skus[key]:
            key_to_skus[key].append(sid)
    conn.close()

    key_cost: dict[str, float] = {}
    for key, sids in key_to_skus.items():
        for sid in sids:
            if sid in costs:
                key_cost[key] = costs[sid]
                break
    return key_cost, key_to_skus


def save_cost_by_match_key(match_key: str, cost_cny: float, note: str = "") -> int:
    """同一对齐码下所有 TK sku_id 写入相同成本。"""
    key = parse_search_key(match_key)
    if not key:
        return 0
    _, key_to_skus = _cost_index()
    sids = key_to_skus.get(key, [])
    if not sids:
        init_db()
        conn = connect()
        for r in conn.execute(
            """SELECT sku_id, seller_sku FROM products WHERE seller_sku != ''"""
        ):
            if tk_match_key(r["seller_sku"]) == key:
                sid = r["sku_id"] or ""
                if sid and sid not in sids:
                    sids.append(sid)
        conn.close()
    n = 0
    for sid in sids:
        cost_mod.save_cost(sid, cost_cny, note or f"catalog:{key}")
        n += 1
    return n


def global_summary() -> dict:
    """全平台汇总（四国合并 + Ozon）。"""
    init_db()
    conn = connect()
    tk_keys: set[str] = set()
    for r in conn.execute(
        """SELECT seller_sku, shop_cipher FROM products
           WHERE seller_sku != '' AND status = 'ACTIVATE'"""
    ):
        k = tk_match_key(r["seller_sku"])
        if k:
            tk_keys.add(k)
    sp_keys = {
        shopee_match_key(r["seller_sku"])
        for r in conn.execute(
            "SELECT seller_sku FROM shopee_products WHERE seller_sku != ''"
        )
        if shopee_match_key(r["seller_sku"])
    }
    from modules.shopee.global_sku_map import all_match_keys

    sp_keys |= all_match_keys()
    missing_tk = _count_tk_missing_seller_sku(conn)
    missing_sp = _count_shopee_needs_seller_sku(conn)
    conn.close()
    ozon = load_ozon_by_key()
    oz_keys = set(ozon)
    all_keys = tk_keys | sp_keys | oz_keys
    matched_tk_sp = len(tk_keys & sp_keys)
    matched_all = len(tk_keys & sp_keys & oz_keys)
    with_cost, _ = _cost_index()
    return {
        "tiktok_keys": len(tk_keys),
        "shopee_keys": len(sp_keys),
        "ozon_keys": len(oz_keys),
        "total_keys": len(all_keys),
        "matched_tk_shopee": matched_tk_sp,
        "matched_all_three": matched_all,
        "with_cost": len(with_cost),
        "tiktok_missing_sku": missing_tk,
        "shopee_needs_sku": missing_sp,
        "ozon_live": sum(1 for v in ozon.values() if v.get("migrated")),
        "ozon_pending": sum(1 for v in ozon.values() if not v.get("migrated")),
        "regions": list(SEA_REGIONS),
    }


def store_summary() -> list[dict]:
    """保留按站点统计（卡片点击筛选用）。"""
    init_db()
    conn = connect()
    out = []
    for reg in SEA_REGIONS:
        tk_keys: set[str] = set()
        for r in conn.execute(
            """SELECT seller_sku, shop_cipher FROM products
               WHERE seller_sku != '' AND status = 'ACTIVATE'"""
        ):
            if tk_region(r["shop_cipher"]) == reg:
                k = tk_match_key(r["seller_sku"])
                if k:
                    tk_keys.add(k)
        sp_keys = {
            shopee_match_key(r["seller_sku"])
            for r in conn.execute(
                "SELECT seller_sku FROM shopee_products WHERE region = ? AND seller_sku != ''",
                (reg,),
            )
            if shopee_match_key(r["seller_sku"])
        }
        both = len(tk_keys & sp_keys)
        shop = conn.execute(
            "SELECT shop_name FROM shopee_shops WHERE region = ? LIMIT 1", (reg,)
        ).fetchone()
        out.append(
            {
                "region": reg,
                "tiktok_keys": len(tk_keys),
                "shopee_keys": len(sp_keys),
                "matched_keys": both,
                "shopee_shop": shop["shop_name"] if shop else "",
            }
        )
    conn.close()
    g = global_summary()
    out.insert(
        0,
        {
            "region": "ALL",
            "tiktok_keys": g["tiktok_keys"],
            "shopee_keys": g["shopee_keys"],
            "matched_keys": g["matched_tk_shopee"],
            "ozon_keys": g["ozon_keys"],
            "with_cost": g["with_cost"],
            "shopee_shop": "四国合并",
        },
    )
    return out


def resolve_sku_query(query: str) -> dict:
    """解析用户输入：商家 SKU / 对齐码 / TK platform sku_id。"""
    raw = (query or "").strip()
    if not raw:
        return {"query": "", "match_key": "", "resolved_via": None, "hints": []}

    init_db()
    conn = connect()
    hints: list[str] = []

    if re.fullmatch(r"\d{10,}", raw):
        row = conn.execute(
            """SELECT seller_sku, sku_id, shop_cipher FROM products
               WHERE sku_id = ? OR sku_id LIKE ? LIMIT 1""",
            (raw, f"%{raw}%"),
        ).fetchone()
        if row:
            if row["seller_sku"]:
                key = tk_match_key(row["seller_sku"])
                conn.close()
                return {
                    "query": raw,
                    "match_key": key,
                    "resolved_via": "platform_sku_id",
                    "platform_sku_id": row["sku_id"],
                    "seller_sku": row["seller_sku"],
                    "hints": [f"TK sku_id → 商家码 {row['seller_sku']} → 对齐码 {key}"],
                }
            conn.close()
            return {
                "query": raw,
                "match_key": "",
                "resolved_via": "platform_sku_id",
                "platform_sku_id": row["sku_id"],
                "shop_cipher": row["shop_cipher"] or "",
                "hints": ["TK sku_id 已找到，商家 SKU 为空，可在下方填写"],
            }

    for r in conn.execute(
        """SELECT seller_sku, sku_id FROM products
           WHERE seller_sku = ? AND status = 'ACTIVATE' LIMIT 1""",
        (raw,),
    ):
        key = tk_match_key(r["seller_sku"])
        conn.close()
        return {
            "query": raw,
            "match_key": key,
            "resolved_via": "seller_sku",
            "seller_sku": r["seller_sku"],
            "platform_sku_id": r["sku_id"],
            "hints": [f"商家码 {raw} → 对齐码 {key}"],
        }

    for r in conn.execute(
        "SELECT seller_sku, region FROM shopee_products WHERE seller_sku = ? LIMIT 1",
        (raw,),
    ):
        key = shopee_match_key(r["seller_sku"])
        conn.close()
        return {
            "query": raw,
            "match_key": key,
            "resolved_via": "seller_sku",
            "seller_sku": r["seller_sku"],
            "shopee_region": r["region"],
            "hints": [f"Shopee 商家码 {raw} → 对齐码 {key}"],
        }

    conn.close()

    key = parse_search_key(raw)
    if key:
        hints.append(f"对齐码 {key}")
        if len(re.sub(r"\D", "", raw)) > 4:
            hints.append("（由长码后四位解析）")
        return {
            "query": raw,
            "match_key": key,
            "resolved_via": "match_key",
            "hints": hints,
        }

    return {
        "query": raw,
        "match_key": "",
        "resolved_via": None,
        "hints": ["无法识别，请输入 660002 / 0026 / 0002 或 TK sku_id"],
    }


def lookup_sku(query: str, region: str | None = None) -> dict:
    """按 SKU 查询单条合并记录。"""
    resolved = resolve_sku_query(query)
    key = resolved.get("match_key") or ""
    if not key:
        pid = resolved.get("platform_sku_id") or ""
        if pid:
            from modules.catalog import sku_edit as sku_edit_mod
            edit = sku_edit_mod.get_edit_rows(sku_id=pid)
            return {
                "ok": True,
                "found": bool(edit.get("rows")),
                "edit_only": True,
                **resolved,
                "item": None,
            }
        return {"ok": False, "found": False, **resolved}

    data = list_products(region, sku=key, limit=1)
    item = data["items"][0] if data.get("items") else None
    return {
        "ok": True,
        "found": bool(item),
        **resolved,
        "region": data.get("region"),
        "item": item,
    }


def list_products(
    region: str | None = None,
    *,
    sku: str | None = None,
    match_only: bool = False,
    platform: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    """按 match_key 合并四国 TK + Shopee + Ozon；region 可选筛选。"""
    init_db()
    search_key = parse_search_key(sku) if sku else ""
    reg_filter = (region or "").upper()
    if reg_filter == "ALL":
        reg_filter = ""

    conn = connect()
    if platform == "missing_tk_sku":
        items_all = _list_tk_missing_seller_sku(conn, reg_filter, sku or "")
        conn.close()
        total = len(items_all)
        page = items_all[offset : offset + limit]
        return {
            "region": reg_filter or "ALL",
            "total": total,
            "offset": offset,
            "limit": limit,
            "items": page,
            "search_key": search_key or None,
            "platform_filter": "missing_tk_sku",
            "missing_tk_included": total,
            "summary": global_summary(),
        }

    if platform == "missing_sp_sku":
        items_all = _list_shopee_needs_seller_sku(conn, reg_filter, sku or "")
        conn.close()
        total = len(items_all)
        page = items_all[offset : offset + limit]
        return {
            "region": reg_filter or "ALL",
            "total": total,
            "offset": offset,
            "limit": limit,
            "items": page,
            "search_key": search_key or None,
            "platform_filter": "missing_sp_sku",
            "missing_sp_included": total,
            "summary": global_summary(),
        }

    tk_by_key: dict[str, list[dict]] = {}
    for r in conn.execute(
        """SELECT sku_id, shop_cipher, product_id, seller_sku, product_name, sku_name,
                  image_url, price, currency, stock, status,
                  global_product_id, global_sku_id
           FROM products WHERE seller_sku != '' AND status = 'ACTIVATE'"""
    ):
        region = tk_region(r["shop_cipher"])
        if reg_filter and region != reg_filter:
            continue
        key = tk_match_key(r["seller_sku"])
        if not key:
            continue
        if search_key and key != search_key and search_key not in (r["seller_sku"] or ""):
            continue
        tk_by_key.setdefault(key, []).append(_row_tk(r, region))

    sp_by_key: dict[str, list[dict]] = {}
    for r in conn.execute(
        """SELECT model_id, shop_id, item_id, seller_sku, product_name, model_name,
                  image_url, price, currency, stock, status, region
           FROM shopee_products WHERE seller_sku != ''"""
    ):
        if shopee_sku_needs_edit(r["seller_sku"] or ""):
            continue
        row_reg = (r["region"] or "?").upper()
        if reg_filter and row_reg != reg_filter:
            continue
        key = shopee_match_key(r["seller_sku"])
        if not key:
            continue
        if search_key and key != search_key and search_key not in (r["seller_sku"] or ""):
            continue
        sp_by_key.setdefault(key, []).append(_row_sp(r))

    from modules.shopee.global_sku_map import all_match_keys, rows_for_match_key

    global_keys = all_match_keys()
    enrich_keys = set(sp_by_key) | set(tk_by_key) | global_keys
    if search_key:
        enrich_keys = {k for k in enrich_keys if k == search_key}
    for key in enrich_keys:
        existing = sp_by_key.get(key, [])
        existing_regs = {r["region"] for r in existing}
        for grow in rows_for_match_key(key):
            row_reg = (grow.get("region") or "?").upper()
            if reg_filter and row_reg != reg_filter:
                continue
            map_sku = (grow.get("seller_sku") or "").strip()
            patched = False
            for r in existing:
                if r["region"] != row_reg:
                    continue
                if map_sku and (not (r.get("seller_sku") or "").strip() or shopee_match_key(r["seller_sku"]) != key):
                    r["seller_sku"] = map_sku
                    if grow.get("product_id"):
                        r["product_id"] = grow["product_id"]
                    if grow.get("global_item_id"):
                        r["global_item_id"] = grow["global_item_id"]
                    r["source"] = grow.get("source") or "cnsc_global_map"
                patched = True
            if patched or row_reg in existing_regs:
                continue
            sp_by_key.setdefault(key, []).append(_row_sp_global(grow))

    missing_tk_items: list[dict] = []
    missing_sp_items: list[dict] = []
    if platform != "missing_ozon" and not match_only:
        missing_tk_items = _list_tk_missing_seller_sku(conn, reg_filter, sku or "")
        missing_sp_items = _list_shopee_needs_seller_sku(conn, reg_filter, sku or "")
    tk_groups = _build_tk_group_index(conn)
    conn.close()

    ozon_by_key = load_ozon_by_key()
    if reg_filter:
        # Ozon 无 SEA region；选单国站点时不展示 Ozon-only 行除非 TK/SP 有该国
        pass

    keys = sorted(set(tk_by_key) | set(sp_by_key) | set(ozon_by_key) | global_keys)
    if search_key:
        keys = [k for k in keys if k == search_key]

    key_cost, key_to_skus = _cost_index()
    key_weight = weight_index_by_match_key()
    items_all = []
    for k in keys:
        tk_rows = tk_by_key.get(k, [])
        sp_rows = sp_by_key.get(k, [])
        oz = ozon_by_key.get(k)
        tk_m = _merge_platform_rows(tk_rows)
        sp_m = _merge_platform_rows(sp_rows)
        matched = {
            "tiktok": bool(tk_m),
            "shopee": bool(sp_m),
            "ozon": bool(oz),
        }
        if match_only and not (matched["tiktok"] and matched["shopee"]):
            continue
        if platform == "missing_ozon":
            if not (tk_m or sp_m) or oz:
                continue

        items_all.append(
            {
                "match_key": k,
                "matched": matched,
                "matched_count": sum(1 for v in matched.values() if v),
                "cost_cny": key_cost.get(k),
                "cost_sku_ids": key_to_skus.get(k, []),
                "logistics_weight_g": (key_weight.get(k) or {}).get("weight_g"),
                "logistics_package_count": (key_weight.get(k) or {}).get("package_count"),
                "weight_source": (key_weight.get(k) or {}).get("weight_source"),
                "tiktok": tk_m,
                "shopee": sp_m,
                "ozon": oz,
                "tk_group": tk_groups.get(k),
            }
        )

    missing_tk_included = 0
    missing_sp_included = 0
    prefix: list[dict] = []
    if missing_tk_items:
        prefix.extend(missing_tk_items)
        missing_tk_included = len(missing_tk_items)
    if missing_sp_items:
        prefix.extend(missing_sp_items)
        missing_sp_included = len(missing_sp_items)
    if prefix:
        items_all = prefix + items_all

    total = len(items_all)
    page = items_all[offset : offset + limit]
    return {
        "region": reg_filter or "ALL",
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": page,
        "search_key": search_key or None,
        "missing_tk_included": missing_tk_included,
        "missing_sp_included": missing_sp_included,
        "summary": global_summary(),
    }
