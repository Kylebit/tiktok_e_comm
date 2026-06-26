"""Probe product API paths with working auth pattern."""
import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

cfg = json.loads((Path(__file__).resolve().parents[1] / "config" / "miaoshou.local.json").read_text())
APP_ID = cfg["app_id"]
SECRET = cfg["app_secret"]
BASE = "https://erp.91miaoshou.com/api/open"
ts = str(int(time.time()))


def md5(s):
    return hashlib.md5(s.encode()).hexdigest()


def post(path, body_extra=None):
    sign = md5(APP_ID + ts + SECRET)
    body = {"app_id": APP_ID, "timestamp": ts, "sign": sign}
    if body_extra:
        body.update(body_extra)
    data = json.dumps(body, separators=(",", ":")).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return "ERR", str(e)


paths = [
    "/product/commonCollectBox/getList",
    "/product/common_collect_box/get_list",
    "/product/commonCollectBox/list",
    "/product/collectBox/common/getList",
    "/product/collect_box/common/get_list",
    "/product/publicCollectBox/getList",
    "/product/public_collect_box/get_list",
    "/product/shopProduct/getList",
    "/product/shop_product/get_list",
    "/product/shopData/getList",
    "/product/shop_data/get_list",
    "/product/shopData/getShopDataList",
    "/product/shop/getDataList",
    "/product/shop/get_data_list",
    "/product/tkCollectBox/getList",
    "/product/tiktok/collectBox/getList",
    "/product/tiktokCollectBox/getList",
    "/shop/getList",
    "/shop/getShopList",
    "/shop/list",
    "/open/product/shopData/getList",
    "/product/getShopDataList",
    "/product/get_shop_data_list",
    "/product/shop/getShopDataList",
]

extras = [
    None,
    {"page": 1, "page_size": 10},
    {"pageNo": 1, "pageSize": 10},
]

for path in paths:
    for extra in extras[:1]:
        code, text = post(path, extra)
        if code == 404:
            continue
        if not text:
            print(f"EMPTY {path}")
            continue
        if "应用[]" in text:
            tag = "NO_APP"
        elif "fail" in text:
            tag = "FAIL"
        elif "success" in text or '"data"' in text:
            tag = "OK"
        else:
            tag = "??"
        reason = text[:180].replace("\n", " ")
        print(f"[{tag}] {path} -> {reason}")

print("done")
