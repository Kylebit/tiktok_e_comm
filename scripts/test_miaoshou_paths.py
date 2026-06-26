"""Try URL-path and OAuth style Miaoshou auth."""
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

cfg = json.loads((Path(__file__).resolve().parents[1] / "config" / "miaoshou.local.json").read_text())
APP_ID = cfg["app_id"]
SECRET = cfg["app_secret"]
BASE = "https://erp.91miaoshou.com/api/open"
ts = str(int(time.time()))


def md5(s):
    return hashlib.md5(s.encode()).hexdigest()


def call(method, url, headers=None, body=None):
    data = None
    h = dict(headers or {})
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode()
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            t = r.read().decode("utf-8", errors="replace")
            return r.status, t, len(t)
    except urllib.error.HTTPError as e:
        t = e.read().decode("utf-8", errors="replace")
        return e.code, t, len(t)
    except Exception as e:
        return "ERR", str(e), 0


sign = md5(APP_ID + ts + SECRET)
body = {"app_id": APP_ID, "timestamp": ts, "sign": sign, "page": 1, "page_size": 10}

paths = [
    f"/{APP_ID}/shop/list",
    f"/{APP_ID}/product/shopData/getList",
    f"/v1/{APP_ID}/shop/list",
    f"/app/{APP_ID}/shop/list",
    "/shop/list",
    "/product/shopData/getList",
]

print("=== path in URL ===")
for p in paths:
    url = BASE + p
    for m in ("GET", "POST"):
        if m == "GET":
            q = urllib.parse.urlencode({"app_id": APP_ID, "timestamp": ts, "sign": sign})
            code, text, n = call("GET", url + "?" + q)
        else:
            code, text, n = call("POST", url, body=body)
        if n or (text and "应用[]" not in text):
            print(f"{m} {p} -> {code} len={n}: {text[:200]!r}")

print("\n=== oauth/token endpoints ===")
oauth_paths = [
    "/oauth/token",
    "/auth/token",
    "/token",
    "/access_token",
    f"/{APP_ID}/oauth/token",
]
for p in oauth_paths:
    for payload in [
        {"app_id": APP_ID, "app_secret": SECRET, "grant_type": "client_credentials"},
        {"client_id": APP_ID, "client_secret": SECRET, "grant_type": "client_credentials"},
    ]:
        code, text, n = call("POST", BASE + p, body=payload)
        if code != 404:
            print(f"POST {p} -> {code}: {text[:250]}")

print("\n=== method-style (like top API) ===")
for method in [
    "shop.getList",
    "shop.getDataList",
    "product.shopData.getList",
    "product.shop.getDataList",
    "open.shop.getList",
]:
    payload = {
        "app_id": APP_ID,
        "method": method,
        "timestamp": ts,
        "sign": sign,
        "param": json.dumps({"page": 1, "page_size": 10}, separators=(",", ":")),
        "v": "1.0",
    }
    code, text, n = call("POST", BASE, body=payload)
    if code != 404 and text:
        print(f"method={method} -> {code}: {text[:250]}")

print("done")
