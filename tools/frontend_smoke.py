from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


BASE = "http://127.0.0.1:8765"


def fetch(path: str, timeout: int = 8) -> tuple[int, str, bytes]:
    url = path if path.startswith("http") else BASE + path
    req = urllib.request.Request(url, headers={"User-Agent": "OrbitHiveSmoke/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.headers.get("Content-Type") or "", resp.read()


def check(name: str, fn) -> bool:
    started = time.time()
    try:
        detail = fn()
        elapsed = int((time.time() - started) * 1000)
        print(f"OK   {name:<24} {elapsed:>5}ms {detail}")
        return True
    except urllib.error.HTTPError as e:
        body = e.read(500).decode("utf-8", "replace")
        print(f"FAIL {name:<24} HTTP {e.code} {body}")
    except Exception as e:
        print(f"FAIL {name:<24} {type(e).__name__}: {e}")
    return False


def json_get(path: str) -> dict:
    status, ctype, body = fetch(path)
    data = json.loads(body.decode("utf-8"))
    if status != 200 or not data.get("ok"):
        raise RuntimeError(data)
    return data


def main() -> int:
    ok = True

    ok &= check("health", lambda: json_get("/api/health")["service"])
    ok &= check(
        "new-product page",
        lambda: f"{fetch('/new-product')[0]} bytes={len(fetch('/new-product')[2])}",
    )
    ok &= check(
        "catalog page",
        lambda: f"{fetch('/catalog')[0]} bytes={len(fetch('/catalog')[2])}",
    )
    ok &= check(
        "static css",
        lambda: f"{fetch('/static/app.css')[0]} bytes={len(fetch('/static/app.css')[2])}",
    )

    catalog_holder: dict[str, dict] = {}

    def catalog_products() -> str:
        data = json_get("/api/catalog/products?limit=5")
        catalog_holder["products"] = data
        return f"total={data.get('total')} sample={len(data.get('items') or [])}"

    ok &= check("catalog products", catalog_products)
    ok &= check(
        "catalog stores",
        lambda: f"stores={len(json_get('/api/catalog/stores').get('stores') or [])}",
    )

    def proxy_image() -> str:
        items = (catalog_holder.get("products") or {}).get("items") or []
        img = ""
        for item in items:
            for block_name in ("tiktok", "shopee", "ozon"):
                block = item.get(block_name) or {}
                img = block.get("image_url") or img
                if img:
                    break
            if img:
                break
        if not img:
            return "no image in sample"
        path = "/api/proxy-image?url=" + urllib.parse.quote(img, safe="")
        status, ctype, body = fetch(path, timeout=20)
        if not ctype.startswith("image/"):
            raise RuntimeError(f"not image: {ctype}")
        return f"{status} {ctype} bytes={len(body)}"

    ok &= check("image proxy", proxy_image)

    def new_product_preview() -> str:
        data = json_get("/api/new-product/preview?offer_id=967648348081")
        source = data.get("source") or {}
        review = data.get("review") or {}
        return (
            f"offer={data.get('offer_id')} "
            f"images={len(review.get('image_actions') or [])} "
            f"title={bool(review.get('title') or source.get('title_source'))}"
        )

    ok &= check("new-product preview", new_product_preview)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
