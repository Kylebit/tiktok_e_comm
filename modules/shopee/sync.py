"""从 Shopee Product API 同步 MY/VN/TH/PH 主店商品。"""

from __future__ import annotations

import time

from core.db import connect, init_db
from modules.shopee.auth import ensure_shop_token, load_tokens
from modules.shopee.client import shop_get
from modules.shopee.shops import list_sync_shops

REGION_CURRENCY = {"MY": "MYR", "VN": "VND", "TH": "THB", "PH": "PHP"}
PAGE_SIZE = 50
BATCH_INFO = 50


def _token_for_shop(shop_id: int) -> str:
    """过期前自动 refresh，无需手动重新授权。"""
    return ensure_shop_token(shop_id)


def _price(info_list: list | None) -> tuple[float, str]:
    if not info_list:
        return 0.0, ""
    p = info_list[0] or {}
    return float(p.get("current_price") or p.get("original_price") or 0), p.get("currency") or ""


def _stock(stock_info: dict | None) -> int:
    if not stock_info:
        return 0
    summary = stock_info.get("summary_info") or {}
    if summary.get("total_available_stock") is not None:
        return int(summary.get("total_available_stock") or 0)
    total = 0
    for block in stock_info.get("seller_stock") or []:
        total += int(block.get("stock") or 0)
    return total


def _image(item: dict) -> str:
    img = item.get("image") or {}
    urls = img.get("image_url_list") or []
    return urls[0] if urls else ""


def _fetch_item_ids(shop_id: int, token: str) -> list[int]:
    ids: list[int] = []
    offset = 0
    while True:
        resp = shop_get(
            "/api/v2/product/get_item_list",
            shop_id,
            token,
            {"offset": offset, "page_size": PAGE_SIZE, "item_status": "NORMAL"},
        )
        if resp.get("error"):
            raise RuntimeError(resp.get("message") or resp)
        data = resp.get("response") or {}
        for it in data.get("item") or []:
            ids.append(int(it["item_id"]))
        if not data.get("has_next_page"):
            break
        offset = int(data.get("next_offset") or offset + PAGE_SIZE)
        time.sleep(0.08)
    return ids


def _fetch_items_base(shop_id: int, token: str, item_ids: list[int]) -> list[dict]:
    out: list[dict] = []
    for i in range(0, len(item_ids), BATCH_INFO):
        batch = item_ids[i : i + BATCH_INFO]
        id_str = ",".join(str(x) for x in batch)
        resp = shop_get(
            "/api/v2/product/get_item_base_info",
            shop_id,
            token,
            {"item_id_list": id_str},
        )
        if resp.get("error"):
            raise RuntimeError(resp.get("message") or resp)
        out.extend((resp.get("response") or {}).get("item_list") or [])
        time.sleep(0.08)
    return out


def _rows_from_item(
    shop_id: int,
    region: str,
    token: str,
    item: dict,
    *,
    use_cache: bool = True,
    force_refresh: bool = False,
) -> tuple[list[dict], bool]:
    """返回 (rows, used_cache)。"""
    from modules.catalog.sync_cache import load_shopee_item, save_shopee_item

    item_id = int(item["item_id"])
    title = item.get("item_name") or ""
    status = item.get("item_status") or ""
    main_img = _image(item)
    now = int(time.time())
    currency_default = REGION_CURRENCY.get(region, "")
    used_cache = False

    if item.get("has_model"):
        models = None
        if use_cache and not force_refresh:
            cached = load_shopee_item(shop_id, item_id)
            if cached and isinstance(cached.get("models"), list):
                models = cached["models"]
                used_cache = True
        if models is None:
            resp = shop_get(
                "/api/v2/product/get_model_list",
                shop_id,
                token,
                {"item_id": item_id},
            )
            if resp.get("error"):
                raise RuntimeError(resp.get("message") or resp)
            models = (resp.get("response") or {}).get("model") or []
            if use_cache:
                save_shopee_item(shop_id, item_id, {"models": models})
            time.sleep(0.04)
        rows = []
        for m in models:
            price, currency = _price(m.get("price_info"))
            rows.append(
                {
                    "model_id": str(m.get("model_id") or ""),
                    "shop_id": shop_id,
                    "region": region,
                    "item_id": str(item_id),
                    "seller_sku": (m.get("model_sku") or item.get("item_sku") or "").strip(),
                    "product_name": title,
                    "model_name": m.get("model_name") or "",
                    "image_url": main_img,
                    "price": price,
                    "currency": currency or currency_default,
                    "stock": _stock(m.get("stock_info_v2")),
                    "status": m.get("model_status") or status,
                    "updated_at": now,
                }
            )
        return [r for r in rows if r["model_id"]], used_cache

    price, currency = _price(item.get("price_info"))
    return [
        {
            "model_id": f"item_{item_id}",
            "shop_id": shop_id,
            "region": region,
            "item_id": str(item_id),
            "seller_sku": (item.get("item_sku") or "").strip(),
            "product_name": title,
            "model_name": "",
            "image_url": main_img,
            "price": price,
            "currency": currency or currency_default,
            "stock": _stock(item.get("stock_info_v2")),
            "status": status,
            "updated_at": now,
        }
    ], used_cache


def _upsert_shop(conn, shop_id: int, region: str, name: str) -> None:
    conn.execute(
        """INSERT INTO shopee_shops (shop_id, region, shop_name, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(shop_id) DO UPDATE SET
             region=excluded.region, shop_name=excluded.shop_name, updated_at=excluded.updated_at""",
        (shop_id, region, name, int(time.time())),
    )


def _clear_shop_products(conn, shop_id: int) -> None:
    conn.execute("DELETE FROM shopee_products WHERE shop_id = ?", (shop_id,))


def _upsert_products(conn, rows: list[dict]) -> int:
    if not rows:
        return 0
    conn.executemany(
        """INSERT INTO shopee_products (
            model_id, shop_id, region, item_id, seller_sku, product_name, model_name,
            image_url, price, currency, stock, status, updated_at
        ) VALUES (
            :model_id, :shop_id, :region, :item_id, :seller_sku, :product_name, :model_name,
            :image_url, :price, :currency, :stock, :status, :updated_at
        )
        ON CONFLICT(model_id, shop_id) DO UPDATE SET
            region=excluded.region, item_id=excluded.item_id,
            seller_sku=CASE WHEN excluded.seller_sku != '' THEN excluded.seller_sku ELSE shopee_products.seller_sku END,
            product_name=excluded.product_name, model_name=excluded.model_name,
            image_url=excluded.image_url, price=excluded.price, currency=excluded.currency,
            stock=excluded.stock, status=excluded.status, updated_at=excluded.updated_at""",
        rows,
    )
    return len(rows)


def _shop_stats_from_db(shop_id: int) -> tuple[int, int]:
    conn = connect()
    items = conn.execute(
        "SELECT COUNT(DISTINCT item_id) FROM shopee_products WHERE shop_id = ?",
        (shop_id,),
    ).fetchone()[0]
    skus = conn.execute(
        "SELECT COUNT(*) FROM shopee_products WHERE shop_id = ?",
        (shop_id,),
    ).fetchone()[0]
    conn.close()
    return int(items or 0), int(skus or 0)


def sync_shop(
    shop_id: int,
    region: str,
    shop_name: str = "",
    on_progress=None,
    *,
    use_cache: bool = True,
    force_refresh: bool = False,
) -> dict:
    from modules.catalog.sync_cache import (
        save_shopee_manifest,
        shopee_shop_unchanged,
    )

    token = _token_for_shop(shop_id)
    label = shop_name or str(shop_id)
    print(f"  同步 Shopee {label} [{region}]...", end=" ", flush=True)

    item_ids = _fetch_item_ids(shop_id, token)
    if use_cache and not force_refresh and shopee_shop_unchanged(shop_id, item_ids):
        items, skus = _shop_stats_from_db(shop_id)
        print(f"→ 跳过（清单未变）{items} 商品, {skus} SKU")
        if on_progress:
            on_progress(len(item_ids), len(item_ids), f"Shopee {label} [{region}] 用缓存")
        return {
            "items": items,
            "skus": skus,
            "skipped": True,
            "cache_hits": len(item_ids),
            "api_fetches": 0,
        }

    items = _fetch_items_base(shop_id, token, item_ids)

    conn = connect()
    _upsert_shop(conn, shop_id, region, label)
    if force_refresh:
        _clear_shop_products(conn, shop_id)

    total_rows = 0
    total_items = len(items)
    cache_hits = 0
    api_fetches = 0
    for i, item in enumerate(items, 1):
        rows, hit = _rows_from_item(
            shop_id,
            region,
            token,
            item,
            use_cache=use_cache,
            force_refresh=force_refresh,
        )
        if hit:
            cache_hits += 1
        elif item.get("has_model"):
            api_fetches += 1
        total_rows += _upsert_products(conn, rows)
        if on_progress and (i % 5 == 0 or i == total_items):
            on_progress(
                i,
                total_items,
                f"Shopee {label} [{region}] {i}/{total_items} 商品",
            )
        elif i % 20 == 0 or i == total_items:
            print(f"{i}/{total_items}", end=" ", flush=True)

    conn.commit()
    conn.close()
    if use_cache:
        save_shopee_manifest(shop_id, item_ids)
    print(f"→ {len(items)} 商品, {total_rows} SKU (API {api_fetches}, 缓存 {cache_hits})")
    return {
        "items": len(items),
        "skus": total_rows,
        "cache_hits": cache_hits,
        "api_fetches": api_fetches,
    }


def sync_all(
    on_progress=None,
    *,
    use_cache: bool = True,
    force_refresh: bool = False,
) -> dict:
    init_db()
    targets = list_sync_shops()
    if not targets:
        from modules.shopee.shops import refresh_shop_regions
        refresh_shop_regions(quiet=True)
        targets = list_sync_shops()
    stats = {
        "shops": len(targets),
        "items": 0,
        "skus": 0,
        "cache_hits": 0,
        "api_fetches": 0,
        "shops_skipped": 0,
    }
    n = len(targets) or 1
    for idx, t in enumerate(targets):

        def shop_prog(cur: int, tot: int, msg: str, _i=idx) -> None:
            if on_progress:
                on_progress(_i * tot + cur, n * max(tot, 1), msg)

        r = sync_shop(
            int(t["shop_id"]),
            t["region"],
            t.get("shop_name") or "",
            on_progress=shop_prog if on_progress else None,
            use_cache=use_cache,
            force_refresh=force_refresh,
        )
        stats["items"] += r["items"]
        stats["skus"] += r["skus"]
        stats["cache_hits"] += int(r.get("cache_hits") or 0)
        stats["api_fetches"] += int(r.get("api_fetches") or 0)
        if r.get("skipped"):
            stats["shops_skipped"] += 1
    from modules.shopee.global_sku_map import apply_to_db, hydrate_shop_items_from_map

    stats["global_sku_patched"] = apply_to_db()
    stats["global_shop_hydrated"] = hydrate_shop_items_from_map()
    return stats
