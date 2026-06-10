"""从 TikTok Product API 同步商品到 SQLite。"""

import json
import time
from datetime import datetime, timezone

from core import auth, shops
from core.api_client import get, post
from core.db import connect, init_db

SEARCH_PATH = "/product/202309/products/search"
DETAIL_PATH = "/product/202309/products/{product_id}"
# TikTok 允许的状态见 search body；在售商品用 ACTIVATE
SEARCH_STATUS = "ACTIVATE"
ACTIVE_PRODUCT_STATUSES = {"ACTIVATE"}
ACTIVE_SKU_STATUSES = {"", "NORMAL"}


def _first_thumb(images) -> str:
    if not images:
        return ""
    for img in images:
        urls = img.get("thumb_urls") or img.get("urls") or []
        if urls:
            return urls[0]
    return ""


def _sku_name(sku: dict) -> str:
    attrs = sku.get("sales_attributes") or []
    parts = [a.get("value_name") or a.get("name") or "" for a in attrs if a]
    name = " / ".join(p for p in parts if p)
    return name or sku.get("seller_sku") or ""


def _sku_image(sku: dict) -> str:
    for attr in sku.get("sales_attributes") or []:
        img = attr.get("sku_img")
        if img:
            url = _first_thumb([img])
            if url:
                return url
    return ""


def _fetch_product_detail(access_token: str, cipher: str, product_id: str) -> dict:
    path = DETAIL_PATH.format(product_id=product_id)
    result = get(path, access_token, {"shop_cipher": cipher})
    if result.get("code") != 0:
        raise RuntimeError(result.get("message", f"商品详情失败 {product_id}"))
    return result.get("data") or {}


def _is_active_product(product: dict) -> bool:
    status = (product.get("product_status") or product.get("status") or "").upper()
    return status in ACTIVE_PRODUCT_STATUSES


def _is_active_sku(sku: dict) -> bool:
    info = sku.get("status_info") or {}
    status = (info.get("status") or "").upper()
    return status in ACTIVE_SKU_STATUSES


def _rows_from_product(shop: dict, product: dict) -> list[dict]:
    if not _is_active_product(product):
        return []
    cipher = shop.get("cipher") or shop.get("shop_cipher", "")
    region = shop.get("region", "")
    product_id = str(product.get("id", ""))
    title = product.get("title", "")
    status = product.get("product_status") or product.get("status", "")
    main_img = _first_thumb(product.get("main_images"))
    now = int(time.time())
    gpa = product.get("global_product_association") or {}
    global_product_id = str(gpa.get("global_product_id") or "")
    global_by_local = {
        str(m.get("local_sku_id")): str(m.get("global_sku_id"))
        for m in gpa.get("sku_mappings") or []
        if m.get("local_sku_id") and m.get("global_sku_id")
    }
    rows = []
    for sku in product.get("skus") or []:
        if not _is_active_sku(sku):
            continue
        sku_id = str(sku.get("id", ""))
        if not sku_id:
            continue
        price = sku.get("price") or {}
        qty = sum(int(i.get("quantity") or 0) for i in (sku.get("inventory") or []))
        rows.append({
            "sku_id": sku_id,
            "shop_cipher": cipher,
            "region": region,
            "product_id": product_id,
            "global_product_id": global_product_id,
            "global_sku_id": global_by_local.get(sku_id, ""),
            "seller_sku": sku.get("seller_sku") or "",
            "product_name": title,
            "sku_name": _sku_name(sku),
            "image_url": _sku_image(sku) or main_img,
            "price": float(price.get("sale_price") or 0),
            "currency": price.get("currency") or "",
            "stock": qty,
            "status": status,
            "updated_at": now,
        })
    return rows


def _upsert_products(conn, rows: list[dict]) -> int:
    conn.executemany(
        """INSERT INTO products (
            sku_id, shop_cipher, product_id, global_product_id, global_sku_id,
            seller_sku, product_name, sku_name,
            image_url, price, currency, stock, status, updated_at
        ) VALUES (
            :sku_id, :shop_cipher, :product_id, :global_product_id, :global_sku_id,
            :seller_sku, :product_name, :sku_name,
            :image_url, :price, :currency, :stock, :status, :updated_at
        )
        ON CONFLICT(sku_id, shop_cipher) DO UPDATE SET
            product_id=excluded.product_id,
            global_product_id=excluded.global_product_id,
            global_sku_id=excluded.global_sku_id,
            seller_sku=CASE
                WHEN excluded.seller_sku != '' THEN excluded.seller_sku
                ELSE products.seller_sku
            END,
            product_name=excluded.product_name,
            sku_name=excluded.sku_name,
            image_url=excluded.image_url,
            price=excluded.price,
            currency=excluded.currency,
            stock=excluded.stock,
            status=excluded.status,
            updated_at=excluded.updated_at""",
        rows,
    )
    return len(rows)


def _upsert_shop(conn, shop: dict) -> None:
    conn.execute(
        """INSERT INTO shops (cipher, shop_id, name, region, seller_type, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(cipher) DO UPDATE SET
             shop_id=excluded.shop_id, name=excluded.name, region=excluded.region,
             seller_type=excluded.seller_type, updated_at=excluded.updated_at""",
        (
            shop.get("cipher") or shop.get("shop_cipher"),
            str(shop.get("id", "")),
            shop.get("name") or shop.get("shop_name", ""),
            shop.get("region", ""),
            shop.get("seller_type", ""),
            int(time.time()),
        ),
    )


def _clear_shop_products(conn, cipher: str) -> None:
    conn.execute("DELETE FROM products WHERE shop_cipher = ?", (cipher,))


def sync_shop(access_token: str, shop: dict, fetch_images: bool = True) -> int:
    cipher = shop.get("cipher") or shop.get("shop_cipher", "")
    region = shop.get("region", "?")
    name = shop.get("name", "")
    print(f"  同步 {name} [{region}]...", end=" ", flush=True)

    page_token = ""
    product_ids = []
    while True:
        qp = {"shop_cipher": cipher, "page_size": "100"}
        if page_token:
            qp["page_token"] = page_token
        result = post(SEARCH_PATH, access_token, qp, {"status": SEARCH_STATUS})
        if result.get("code") != 0:
            raise RuntimeError(result.get("message", "商品搜索失败"))
        data = result.get("data") or {}
        for p in data.get("products") or []:
            product_ids.append(str(p["id"]))
        page_token = data.get("next_page_token") or ""
        if not page_token:
            break
        time.sleep(0.12)

    conn = connect()
    _upsert_shop(conn, shop)
    _clear_shop_products(conn, cipher)
    total_skus = 0
    for i, pid in enumerate(product_ids, 1):
        if fetch_images:
            detail = _fetch_product_detail(access_token, cipher, pid)
        else:
            detail = {"id": pid, "skus": []}
        rows = _rows_from_product(shop, detail)
        if rows:
            total_skus += _upsert_products(conn, rows)
        if i % 20 == 0 or i == len(product_ids):
            print(f"{i}/{len(product_ids)}", end=" ", flush=True)
        time.sleep(0.1)

    conn.commit()
    conn.close()
    print(f"→ {len(product_ids)} 商品, {total_skus} SKU")
    return total_skus


def sync_all(access_token: str | None = None, fetch_images: bool = True) -> dict:
    init_db()
    token = access_token or auth.access_token()
    shop_list = shops.list_shops(token)
    stats = {"shops": len(shop_list), "skus": 0}
    for shop in shop_list:
        stats["skus"] += sync_shop(token, shop, fetch_images=fetch_images)
    return stats
