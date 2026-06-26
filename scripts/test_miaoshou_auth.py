"""Extended Miaoshou API auth probe."""
from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config" / "miaoshou.local.json"
cfg = json.loads(CFG.read_text(encoding="utf-8"))
APP_ID = cfg["app_id"]
SECRET = cfg["app_secret"]
BASE = cfg.get("base_url", "https://erp.91miaoshou.com/api/open").rstrip("/")
ts = str(int(time.time()))


def md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def req(method: str, url: str, headers: dict | None = None, body: dict | None = None) -> tuple:
    data = None
    h = dict(headers or {})
    if body is not None:
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        h.setdefault("Content-Type", "application/json; charset=utf-8")
    r = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return "ERR", str(e)


def show(label: str, code, text: str) -> None:
    if "fail" not in text.lower() and "不支持" not in text:
        print(f"*** {label} -> {code}: {text[:500]}")
    elif "应用[]" not in text:
        print(f"++  {label} -> {code}: {text[:300]}")


path = "/shop/list"
url = BASE + path

# Query param variants
for q in [
    {"app_id": APP_ID},
    {"appId": APP_ID},
    {"app_key": APP_ID},
    {"ak": APP_ID},
    {"client_id": APP_ID},
    {"app_id": APP_ID, "timestamp": ts},
    {"app_id": APP_ID, "timestamp": ts, "sign": md5(APP_ID + ts + SECRET)},
]:
    code, text = req("GET", url + "?" + urllib.parse.urlencode(q))
    show(f"GET ?{q}", code, text)

# Header variants
for hdr in [
    {"app_id": APP_ID},
    {"App-Id": APP_ID},
    {"X-App-Id": APP_ID},
    {"X-App-Key": APP_ID},
    {"app-key": APP_ID},
    {"open-app-id": APP_ID},
    {"open_app_id": APP_ID},
]:
    code, text = req("GET", url, headers=hdr)
    show(f"GET hdr {list(hdr)[0]}", code, text)

# POST body variants with result envelope
signs = {
    "a": md5(APP_ID + ts + SECRET),
    "b": md5(ts + APP_ID + SECRET),
    "c": md5(SECRET + APP_ID + ts),
}
for sname, sign in signs.items():
    for body in [
        {"app_id": APP_ID, "timestamp": ts, "sign": sign},
        {"appId": APP_ID, "timestamp": int(ts), "sign": sign},
        {"app_id": APP_ID, "timestamp": ts, "sign": sign, "page": 1, "page_size": 10},
    ]:
        code, text = req("POST", url, body=body)
        show(f"POST sign={sname} keys={list(body.keys())}", code, text)

# Try product shop data list paths
for p in [
    "/product/shopData/getList",
    "/product/shop_data/get_list",
    "/product/shop/get_data_list",
    "/product/shop/getDataList",
    "/product/shopProduct/getList",
    "/product/shop_product/get_list",
    "/shop/get_data_list",
    "/shop/getDataList",
]:
    body = {"app_id": APP_ID, "timestamp": ts, "sign": md5(APP_ID + ts + SECRET), "page": 1, "page_size": 10}
    code, text = req("POST", BASE + p, body=body)
    if code != 404:
        show(f"POST {p}", code, text)

print("done")
