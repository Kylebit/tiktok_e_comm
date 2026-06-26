"use strict";
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modules.miaoshou.client import post_open

cases = [
    ("get_shop_list+shopId", "/open/v1/product/shop/shop/get_shop_list", {"platform": "tiktok", "site": "GB", "shopId": 10204699, "pageNo": 1, "pageSize": 20}),
    ("get_shop_list+dataType", "/open/v1/product/shop/shop/get_shop_list", {"platform": "tiktok", "site": "GB", "shopId": 10204699, "dataType": "product", "pageNo": 1, "pageSize": 20}),
    ("global shop list", "/open/v1/product/shop/shop/get_shop_list", {"platform": "tiktokGlobal", "site": "GB", "pageNo": 1, "pageSize": 20}),
    ("global TIKTOKGLOBAL", "/open/v1/product/shop/shop/get_shop_list", {"platform": "tiktokGlobal", "site": "TIKTOKGLOBAL", "pageNo": 1, "pageSize": 20}),
]
for label, path, body in cases:
    r = post_open(path, body)
    print("\n===", label, "===")
    print(json.dumps(r, ensure_ascii=False)[:1500])
