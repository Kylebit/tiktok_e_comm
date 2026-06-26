"""Probe Miaoshou product list API paths for GB shop."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.miaoshou.client import get_shop_list, post_open

SITE = "GB"
PLATFORM = "tiktok"

shops = get_shop_list(PLATFORM, SITE, page_no=1, page_size=20)
print("shops:", json.dumps(shops, ensure_ascii=False)[:800])
shop_list = (shops.get("data") or {}).get("shopList") or []
if not shop_list:
    print("no GB shops")
    sys.exit(1)

shop_id = shop_list[0]["shopId"]
print("using shopId", shop_id)

paths = [
    "/open/v1/product/shop/shop/get_shop_data_list",
    "/open/v1/product/shop/shop/getShopDataList",
    "/open/v1/product/shop/shop/get_data_list",
    "/open/v1/product/shop/shop/get_product_list",
    "/open/v1/product/shop/shop/get_shop_product_list",
    "/open/v1/product/shop/shop/get_online_product_list",
    "/open/v1/product/shop/shop/get_manage_product_list",
    "/open/v1/product/shop/shop/query_product_list",
    "/open/v1/product/shop/shop/search_product_list",
    "/open/v1/product/shop/shop/get_item_list",
    "/open/v1/product/shop/product/get_list",
    "/open/v1/product/shop/product/get_product_list",
    "/open/v1/product/shop/manage/get_product_list",
    "/open/v1/product/shop/manage/get_list",
    "/open/v1/product/shop/online/get_list",
    "/open/v1/product/shop/online/get_product_list",
    "/open/v1/product/shop/get_product_list",
    "/open/v1/product/shop/get_shop_product_list",
]

base_body = {
    "platform": PLATFORM,
    "site": SITE,
    "shopId": shop_id,
    "pageNo": 1,
    "pageSize": 10,
}

for path in paths:
    resp = post_open(path, base_body)
    code = resp.get("code")
    result = resp.get("result")
    msg = resp.get("message") or resp.get("reason") or ""
    preview = json.dumps(resp, ensure_ascii=False)[:500]
    print(f"\n{path}\n  -> {result}/{code} {msg}\n  {preview}")
