"""1688 商品页 HTML 采集（无需万邦 API）。"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from core.http_retry import DEFAULT_SSL_CTX as SSL_CTX
from core.http_retry import urlopen as urlopen_retry

from modules.sourcing.onebound import parse_offer_id

ROOT = Path(__file__).resolve().parents[2]
SOURCING_DIR = ROOT / "data" / "sourcing"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _fetch(url: str, *, timeout: int = 45) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA}, method="GET")
    with urlopen_retry(req, timeout=timeout, context=SSL_CTX) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _find_json_after(html: str, key: str):
    idx = html.find(f'"{key}"')
    if idx < 0:
        return None
    start = None
    for i in range(idx, min(idx + 50, len(html))):
        if html[i] in "[{":
            start = i
            break
    if start is None:
        return None
    open_c = html[start]
    close_c = "]" if open_c == "[" else "}"
    depth = 0
    for j in range(start, min(start + 500_000, len(html))):
        c = html[j]
        if c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start : j + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _find_object_after(html: str, key: str) -> dict | None:
    idx = html.find(f'"{key}"')
    if idx < 0:
        return None
    start = None
    for i in range(idx, min(idx + 30, len(html))):
        if html[i] == "{":
            start = i
            break
    if start is None:
        return None
    depth = 0
    for j in range(start, min(start + 100_000, len(html))):
        c = html[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start : j + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _extract_str(html: str, key: str) -> str:
    m = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]*)"', html)
    return m.group(1) if m else ""


def _detail_images(detail_url: str) -> list[str]:
    if not detail_url:
        return []
    try:
        html = _fetch(detail_url, timeout=30)
    except Exception:
        return []
    urls = re.findall(r'https?://[^"\']+\.(?:jpg|jpeg|png|webp)[^"\']*', html)
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if "alicdn" not in u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def parse_html(html: str, *, offer_id: str = "") -> dict:
    """从 1688 详情页 HTML 解析商品结构。"""
    offer_detail = _find_object_after(html, "offerDetail") or {}
    attrs = {
        a.get("name"): a.get("value")
        for a in offer_detail.get("featureAttributes") or []
        if a.get("name")
    }
    image_list = _find_json_after(html, "imageList") or []
    main_images = [
        img.get("fullPathImageURI") or img.get("size310x310ImageURI") or ""
        for img in image_list
        if isinstance(img, dict)
    ]
    main_images = [u for u in main_images if u]

    sku_map = _find_json_after(html, "skuMap") or []
    skus = []
    for row in sku_map:
        if not isinstance(row, dict):
            continue
        skus.append(
            {
                "spec": row.get("specAttrs") or "",
                "sku_id": row.get("skuId"),
                "spec_id": row.get("specId") or "",
                "stock": row.get("canBookCount"),
                "price": row.get("price") or row.get("discountPrice"),
            }
        )

    price_ranges = _find_json_after(html, "disPriceRanges") or []
    if not price_ranges:
        m = re.search(r'"disPriceRanges"\s*:\s*(\[.*?\])\s*,\s*"hasPromotion"', html, re.S)
        if m:
            try:
                price_ranges = json.loads(m.group(1))
            except json.JSONDecodeError:
                price_ranges = []

    detail_url = offer_detail.get("detailUrl") or _extract_str(html, "detailUrl")
    detail_images = _detail_images(detail_url)

    num_iid = offer_id or _extract_str(html, "offerId")
    if not num_iid:
        m = re.search(r'"offerId"\s*:\s*(\d+)', html)
        num_iid = m.group(1) if m else ""

    sale_count = None
    m = re.search(r'"offerId"\s*:\s*\d+\s*,\s*"offerPriceModel".*?"saleCount"\s*:\s*(\d+)', html, re.S)
    if not m:
        m = re.search(r'"saleCount"\s*:\s*(\d+)\s*,\s*"skuMap"', html, re.S)
    if m:
        sale_count = int(m.group(1))

    return {
        "source": "1688_scrape",
        "num_iid": str(num_iid),
        "url": f"https://detail.1688.com/offer/{num_iid}.html" if num_iid else "",
        "title": _extract_str(html, "subject"),
        "seller": {
            "company": _extract_str(html, "companyName"),
            "login_id": _extract_str(html, "sellerLoginId") or _extract_str(html, "loginId"),
            "member_id": _extract_str(html, "memberId"),
        },
        "price": {
            "display": _extract_str(html, "offerPriceDisplay") or _extract_str(html, "priceDisplay"),
            "min": _extract_str(html, "offerMinPrice") or _extract_str(html, "minPrice"),
            "max": _extract_str(html, "offerMaxPrice") or _extract_str(html, "maxPrice"),
            "moq": int(m.group(1)) if (m := re.search(r'"offerBeginAmount"\s*:\s*(\d+)', html)) else None,
            "unit": _extract_str(html, "unit") or "个",
            "ranges": price_ranges,
        },
        "sale_count": sale_count,
        "attributes": attrs,
        "skus": skus,
        "images": {
            "main": main_images,
            "detail_url": detail_url,
            "detail": detail_images,
        },
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def _is_blocked(html: str) -> bool:
    if len(html) < 10_000:
        return True
    return "_____tmd_____/punish" in html or '"skuMap"' not in html


def parse_html_file(path: str | Path, *, offer_id: str = "") -> dict:
    html = Path(path).read_text(encoding="utf-8", errors="replace")
    return parse_html(html, offer_id=offer_id or parse_offer_id(str(path)))


def scrape_offer(url_or_id: str, *, save: bool = True, html: str | None = None) -> dict:
    """抓取 1688 商品页并可选写入 data/sourcing/{offer_id}.json。

    html: 可选，直接传入已采集的页面 HTML（浏览器另存或绕过反爬时使用）。
    """
    offer_id = parse_offer_id(url_or_id)
    if not offer_id:
        raise ValueError("无效 1688 链接或 offer id")

    url = f"https://detail.1688.com/offer/{offer_id}.html"
    if html is None:
        html = _fetch(url)
    if _is_blocked(html):
        raise RuntimeError(
            "1688 返回反爬验证页，curl/脚本无法直接抓取。"
            " 请用浏览器打开详情页后执行: python3 main.py sourcing fetch --url ... --html page.html"
        )

    data = parse_html(html, offer_id=offer_id)
    data["fetch_url"] = url

    if save:
        SOURCING_DIR.mkdir(parents=True, exist_ok=True)
        out = SOURCING_DIR / f"{offer_id}.json"
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        data["saved_to"] = str(out)

    return data


def item_summary(data: dict) -> dict:
    """终端预览用摘要。"""
    imgs = data.get("images") or {}
    return {
        "num_iid": data.get("num_iid"),
        "title": data.get("title"),
        "seller": (data.get("seller") or {}).get("company"),
        "price": (data.get("price") or {}).get("display"),
        "sku_count": len(data.get("skus") or []),
        "main_images": len(imgs.get("main") or []),
        "detail_images": len(imgs.get("detail") or []),
        "item_no": (data.get("attributes") or {}).get("货号"),
    }
