"""Fix MX images (drop kwcdn), PHP+20% -> MXN pricing, save and publish."""
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import auth, shops
from core.config import get
from modules.miaoshou.client import post_open
from modules.products.sync import _fetch_product_detail, _first_thumb

TK = 2059296237
MX = 16265910
SELLER_SKU = "770003"
PH_PRODUCT_ID = "1732379849767749563"

GET_PATH = "/open/v1/product/collect_box/tiktok/collect_box/get_shop_collect_item_info"
SAVE_PATH = "/open/v1/product/collect_box/tiktok/collect_box/save_shop_collect_item_info"
PUBLISH_PATH = "/open/v1/product/collect_box/tiktok/collect_box/save_move_collect_task"

# 1 MXN ≈ 0.36 CNY（未在 settings 配置 MXN 时的参考值）
MXN_CNY = 0.36
ALLOWED_HOSTS = ("ibyteimg.com", "tiktokcdn.com", "tiktok.com")
BLOCKED_HOSTS = ("kwcdn.com", "tosoiot.com")


def php_to_mxn(php_price: float) -> float:
    php_cny = float((get("exchange_rates") or {}).get("PHP") or 0.118)
    cny = php_price * 1.2 * php_cny
    return round(cny / MXN_CNY, 2)


def is_ok_image(url: str) -> bool:
    u = (url or "").lower()
    if not u.startswith("http"):
        return False
    if any(b in u for b in BLOCKED_HOSTS):
        return False
    if "algo_check" in u:
        return False
    if any(h in u for h in ALLOWED_HOSTS):
        return True
    ext = u.split("?", 1)[0]
    return ext.endswith((".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp"))


def collect_ph_images() -> list[str]:
    token = auth.access_token()
    ph_shop = next((s for s in shops.list_shops(token) if (s.get("region") or "").upper() == "PH"), None)
    if not ph_shop:
        raise RuntimeError("未找到 PH 店铺授权")
    cipher = ph_shop.get("cipher") or ph_shop.get("shop_cipher")
    product = _fetch_product_detail(token, cipher, PH_PRODUCT_ID)
    urls: list[str] = []
    seen: set[str] = set()

    def add(u: str) -> None:
        if not is_ok_image(u):
            return
        key = u.split("?", 1)[0]
        if key in seen:
            return
        seen.add(key)
        urls.append(u)

    for img in product.get("main_images") or []:
        for u in (img.get("urls") or img.get("thumb_urls") or []):
            add(u)
    for sku in product.get("skus") or []:
        for attr in sku.get("sales_attributes") or []:
            img = attr.get("sku_img")
            if img:
                for u in (img.get("urls") or img.get("thumb_urls") or []):
                    add(u)
    if not urls:
        raise RuntimeError("PH TikTok 商品未取到可用主图")
    return urls[:9]


def clean_notes(notes: str, good_urls: list[str]) -> str:
    """Replace description images with TikTok-safe URLs only."""
    if not good_urls:
        return notes or ""
    imgs = "".join(f'<img src="{u}">' for u in good_urls[:6])
    text = re.sub(r"<[^>]+>", " ", notes or "")
    text = re.sub(r"\s+", " ", text).strip()[:500]
    return f"<div><p>{text}</p>{imgs}</div>" if text else f"<div>{imgs}</div>"


def main() -> int:
    ph_row_price = None
    from core.db import connect, init_db

    init_db()
    conn = connect()
    row = conn.execute(
        """
        SELECT price FROM products p
        JOIN shops s ON p.shop_cipher = s.cipher
        WHERE seller_sku = ? AND UPPER(s.region) = 'PH'
        LIMIT 1
        """,
        (SELLER_SKU,),
    ).fetchone()
    if row:
        ph_row_price = float(row["price"])
    php_price = ph_row_price or 383.0
    mxn_price = php_to_mxn(php_price)
    print(f"PH price={php_price} PHP -> MXN sale/display={mxn_price} (PHP*1.2*rate/MXN rate)")

    good_urls = collect_ph_images()
    print(f"Using {len(good_urls)} TikTok images from PH listing")
    for u in good_urls:
        print(" ", u[:100])

    rd = post_open(GET_PATH, {"detailId": TK, "shopId": MX})
    if rd.get("result") != "success":
        print("get failed:", json.dumps(rd, ensure_ascii=False))
        return 1

    data = rd.get("data") or {}
    info = copy.deepcopy(data.get("shopCollectItemInfo") or {})
    oss_md5 = data.get("ossMd5", "")

    old_imgs = info.get("imgUrls") or []
    removed = [u for u in old_imgs if not is_ok_image(u)]
    if removed:
        print(f"Removed {len(removed)} bad image(s), e.g. {removed[0][:80]}")

    info["imgUrls"] = good_urls
    info["notes"] = clean_notes(info.get("notes") or "", good_urls)

    for prop in info.get("skuPropertyList") or []:
        for val in prop.get("attrValueList") or []:
            img = val.get("imgUrl") or ""
            if not is_ok_image(img):
                val["imgUrl"] = good_urls[0]

    sku_map = info.get("skuMap") or {}
    for sku in sku_map.values():
        sku["price"] = mxn_price
        sku["priceIncludeVat"] = mxn_price
        sku["itemNum"] = SELLER_SKU

    info["skuMap"] = sku_map
    title = info.get("title") or ""
    if len(title) > 255:
        info["title"] = title[:255].rstrip()
    info["packageLength"] = info.get("packageLength") or 10
    info["packageWidth"] = info.get("packageWidth") or 20
    info["packageHeight"] = info.get("packageHeight") or 2
    info["sizeChart"] = ""
    info["sizeChartType"] = ""
    info["deliveryOptionSetType"] = info.get("deliveryOptionSetType") or "default"

    sr = post_open(
        SAVE_PATH,
        {"ossMd5": oss_md5, "detailId": TK, "shopId": MX, "shopCollectItemInfo": info},
    )
    print("\nSave:", json.dumps(sr, ensure_ascii=False, indent=2))
    if sr.get("result") != "success":
        return 2

    rd2 = post_open(GET_PATH, {"detailId": TK, "shopId": MX})
    info2 = (rd2.get("data") or {}).get("shopCollectItemInfo") or {}
    print("\nSaved imgUrls:")
    for u in info2.get("imgUrls") or []:
        print(" ", u[:100])
    print("sku:", json.dumps(info2.get("skuMap"), ensure_ascii=False))

    pr = post_open(PUBLISH_PATH, {"detailIds": [TK], "shopIds": [MX]})
    print("\nPublish:", json.dumps(pr, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
