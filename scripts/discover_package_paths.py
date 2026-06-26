"use strict";
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modules.miaoshou.client import post_open

B = {"platform": "tiktok", "site": "GB", "shopId": 10204699, "pageNo": 1, "pageSize": 5}
paths = [
    "/open/v1/order/package/get_list",
    "/open/v1/order/package/batch_get_list",
    "/open/v1/order/package/get_package_list",
    "/open/v1/package/package/get_list",
    "/open/v1/package/get_list",
    "/open/v1/logistics/offline/get_list",
    "/open/v1/logistics/online/get_list",
]
for p in paths:
    r = post_open(p, B)
    if r.get("code") != "routeNotFound":
        print("HIT", p, json.dumps(r, ensure_ascii=False)[:800])
