"""Test known Miaoshou open API endpoint (topwms) for auth envelope."""
import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

cfg = json.loads((Path(__file__).resolve().parents[1] / "config" / "miaoshou.local.json").read_text())
APP_ID = cfg["app_id"]
SECRET = cfg["app_secret"]
ts = str(int(time.time()))


def md5(s):
    return hashlib.md5(s.encode()).hexdigest()


def post(url, body, headers=None):
    data = json.dumps(body, separators=(",", ":")).encode()
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, method="POST", headers=h)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


sign = md5(APP_ID + ts + SECRET)
url = "https://erp.91miaoshou.com/api/open/topwms/order/get_order_platform_order_status_list"

variants = [
    {"app_id": APP_ID, "timestamp": ts, "sign": sign},
    {"appId": APP_ID, "timestamp": ts, "sign": sign, "orderIds": []},
    {"open_app_id": APP_ID, "timestamp": ts, "sign": sign},
]

for i, body in enumerate(variants):
    code, text = post(url, body)
    print(f"variant {i}: HTTP {code}")
    print(text[:500] or "(empty)")
    print()

# GET
import urllib.parse
q = urllib.parse.urlencode({"app_id": APP_ID, "timestamp": ts, "sign": sign})
import urllib.request
req = urllib.request.Request(url + "?" + q)
try:
    with urllib.request.urlopen(req, timeout=20) as r:
        print("GET:", r.status, r.read().decode()[:500])
except urllib.error.HTTPError as e:
    print("GET:", e.code, e.read().decode()[:500])
