"""Probe Miaoshou Open Platform connectivity (reads config/miaoshou.local.json)."""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config" / "miaoshou.local.json"


def load_cfg() -> dict:
    if not CFG.exists():
        print(f"Missing {CFG}")
        sys.exit(1)
    return json.loads(CFG.read_text(encoding="utf-8"))


def md5(s: str, upper: bool = False) -> str:
    h = hashlib.md5(s.encode("utf-8")).hexdigest()
    return h.upper() if upper else h


def post_json(url: str, data: dict) -> tuple[int | str, str]:
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return "ERR", str(e)


def get_url(url: str, headers: dict | None = None) -> tuple[int | str, str]:
    req = urllib.request.Request(url, method="GET", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return "ERR", str(e)


def main() -> None:
    cfg = load_cfg()
    app_id = cfg["app_id"]
    secret = cfg["app_secret"]
    base = cfg.get("base_url", "https://erp.91miaoshou.com/api/open").rstrip("/")

    ts = str(int(time.time()))
    ts_ms = str(int(time.time() * 1000))
    ts_fmt = time.strftime("%Y-%m-%d %H:%M:%S")
    nonce = uuid.uuid4().hex

    print(f"Base URL: {base}")
    print(f"App ID: {app_id[:8]}...")

    # 1) Base URL probe
    code, text = get_url(base + "/")
    print(f"\n[GET {base}/] -> {code}: {text[:200]}")

    paths = [
        "/shop/list",
        "/shop/getList",
        "/product/shop/list",
        "/product/shopProduct/list",
        "/product/shopData/list",
        "/product/shop_data/list",
        "/product/getShopDataList",
        "/product/get_shop_data_list",
        "/product/shop/get_data_list",
        "/product/shop/getDataList",
    ]

    bodies = [{}, {"page": 1, "page_size": 10}, {"pageNo": 1, "pageSize": 10}]

    def sign_sorted(body: dict, upper: bool = False) -> str:
        params = {"app_id": app_id, "timestamp": ts, **body}
        s = "&".join(f"{k}={params[k]}" for k in sorted(params))
        return md5(s + secret, upper=upper)

    signers = [
        ("md5(app_id+ts+secret)", lambda b: md5(app_id + ts + secret)),
        ("md5(ts+app_id+secret)", lambda b: md5(ts + app_id + secret)),
        ("sorted+secret", lambda b: sign_sorted(b)),
        ("sorted+&key=secret", lambda b: md5(
            "&".join(f"{k}={v}" for k, v in sorted({"app_id": app_id, "timestamp": ts, **b}.items()))
            + "&key=" + secret
        )),
        ("hmac_sha256", lambda b: hmac.new(secret.encode(), (app_id + ts).encode(), hashlib.sha256).hexdigest()),
    ]

    interesting: list[str] = []
    success: list[str] = []

    for path in paths:
        url = base + path
        for body in bodies:
            for sname, sfn in signers:
                sign = sfn(body)
                payload = {"app_id": app_id, "timestamp": ts, "sign": sign, **body}
                code, text = post_json(url, payload)
                tl = text.lower()
                if code == 200 and any(x in tl for x in ["success", '"data"', "shop"]):
                    success.append(f"OK {path} [{sname}] {text[:400]}")
                elif code not in (404, "ERR") and "not found" not in tl:
                    if any(x in tl for x in ["sign", "app", "auth", "token", "param", "code", "msg", "message"]):
                        interesting.append(f"{path} [{sname}] HTTP {code}: {text[:250]}")

    # Header auth patterns
    for hdr_name in ["X-API-Key", "X-App-Id", "Authorization"]:
        hdr = {hdr_name: app_id}
        if hdr_name == "Authorization":
            hdr = {"Authorization": f"Bearer {app_id}"}
        code, text = get_url(base + "/shop/list", hdr)
        if code != 404:
            interesting.append(f"GET /shop/list header {hdr_name}: HTTP {code}: {text[:200]}")

    print("\n--- SUCCESS ---")
    if success:
        for s in success:
            print(s)
    else:
        print("(none)")

    print("\n--- INTERESTING (non-404) ---")
    seen = set()
    for line in interesting:
        if line not in seen:
            seen.add(line)
            print(line)
    if not seen:
        print("(all paths returned 404 or connection error)")


if __name__ == "__main__":
    main()
