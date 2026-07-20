"""New product listing workbench.
This module is intentionally light on model usage. It builds a structured
first-review payload from local scrape/preview files and only records requests
for expensive image/API work.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.config import ROOT

from modules.sourcing.pipeline import load_scrape

WORKSPACE_ROOT = ROOT.parent.parent
OUTPUTS_DIR = WORKSPACE_ROOT / "outputs"
STATE_DIR = ROOT / "data" / "new_product_workbench"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

SEA_MARKETS = [
    {"id": "lh_ph", "shop": "LivelyHive", "region": "PH", "currency": "PHP", "enabled": True, "shop_id": 7676267, "publish_group": "lively"},
    {"id": "lh_my", "shop": "LivelyHive", "region": "MY", "currency": "MYR", "enabled": True, "shop_id": 13295169, "publish_group": "lively"},
    {"id": "lh_th", "shop": "LivelyHive", "region": "TH", "currency": "THB", "enabled": True, "shop_id": 13295228, "publish_group": "lively"},
    {"id": "lh_vn", "shop": "LivelyHive", "region": "VN", "currency": "VND", "enabled": True, "shop_id": 13295291, "publish_group": "lively"},
    {"id": "hb_ph", "shop": "HomeBloom", "region": "PH", "currency": "PHP", "enabled": False, "shop_id": 15173238, "publish_group": "homebloom"},
    {"id": "hb_my", "shop": "HomeBloom", "region": "MY", "currency": "MYR", "enabled": False, "shop_id": 16770639, "publish_group": "homebloom"},
    {"id": "hb_th", "shop": "HomeBloom", "region": "TH", "currency": "THB", "enabled": False, "shop_id": 16770557, "publish_group": "homebloom"},
    {"id": "hb_vn", "shop": "HomeBloom", "region": "VN", "currency": "VND", "enabled": False, "shop_id": 16783702, "publish_group": "homebloom"},
    {"id": "mx", "shop": "LivelyHive", "region": "MX", "currency": "MXN", "enabled": False, "shop_id": 16265910, "publish_group": "lively"},
    {"id": "gb", "shop": "LivelyHive", "region": "GB", "currency": "GBP", "enabled": False, "shop_id": 10204699, "publish_group": "lively"},
]

DISCOUNT_RESERVE_RATE = 0.35
DEFAULT_LISTING_STOCK = 200
_SITE_DRAFT_LOCKS: dict[str, threading.Lock] = {}
_TIKTOK_CLAIM_LOCKS: dict[str, threading.Lock] = {}


@dataclass(frozen=True)
class SeaRegionRule:
    region: str
    currency: str
    cny_per_local: float
    commission_rate: float
    transaction_rate: float
    extra_rate: float
    extra_label: str
    extra_cap_local: float
    affiliate_rate: float
    ad_rate: float
    creator_rate: float
    seller_tax_rate: float
    fixed_fee_local: float
    rounding_step: float


SEA_REGION_RULES = {
    "PH": SeaRegionRule(
        region="PH",
        currency="PHP",
        cny_per_local=0.1264,
        commission_rate=0.062,
        transaction_rate=0.0224,
        extra_rate=0.05,
        extra_label="成长/优惠券服务费",
        extra_cap_local=0.0,
        affiliate_rate=0.0,
        ad_rate=0.20,
        creator_rate=0.08,
        seller_tax_rate=0.0,
        fixed_fee_local=3.0,
        rounding_step=1.0,
    ),
    "MY": SeaRegionRule(
        region="MY",
        currency="MYR",
        cny_per_local=1.6868,
        commission_rate=0.092,
        transaction_rate=0.0378,
        extra_rate=0.0486,
        extra_label="BXP费率",
        extra_cap_local=0.0,
        affiliate_rate=0.0,
        ad_rate=0.20,
        creator_rate=0.08,
        seller_tax_rate=0.10,
        fixed_fee_local=0.54,
        rounding_step=1.0,
    ),
    "TH": SeaRegionRule(
        region="TH",
        currency="THB",
        cny_per_local=0.2211,
        commission_rate=0.074,
        transaction_rate=0.0321,
        extra_rate=0.0642,
        extra_label="平台支持费",
        extra_cap_local=199.0,
        affiliate_rate=0.0,
        ad_rate=0.20,
        creator_rate=0.08,
        seller_tax_rate=0.10,
        fixed_fee_local=1.07,
        rounding_step=1.0,
    ),
    "VN": SeaRegionRule(
        region="VN",
        currency="VND",
        cny_per_local=0.0003,
        commission_rate=0.13,
        transaction_rate=0.06,
        extra_rate=0.04,
        extra_label="VXP费率",
        extra_cap_local=30000.0,
        affiliate_rate=0.0,
        ad_rate=0.20,
        creator_rate=0.08,
        seller_tax_rate=0.10,
        fixed_fee_local=3000.0,
        rounding_step=1000.0,
    ),
}

SEA_TARGET_MARGIN = {
    "LivelyHive": 0.15,
    "HomeBloom": 0.10,
}

MX_RULE = {
    "currency": "MXN",
    "cny_per_local": 2.5765,
    "import_tax_rate": 0.1396,
    "commission_rate": 0.06,
    "sfp_rate": 0.08,
    "affiliate_rate": 0.08,
    "ad_rate": 0.10,
    "per_item_fee_local": 6.0,
    "target_margin": 0.2111,
    "discount_reserve_rate": 0.30,
}

GB_RULE = {
    "currency": "GBP",
    "cny_per_local": 9.15,
    "commission_rate": 0.09,
    "vat_rate": 1 / 6,
    "smart_promo_rate": 0.018,
    "affiliate_rate": 0.0,
    "ad_rate": 0.20,
    "target_margin": 0.1695,
    "discount_reserve_rate": 0.25,
}

# Editable FX panel defaults: CNY per 1 unit of local currency.
# PHP/THB/VND use approximate inverses of common "local per CNY" quotes (7.9 / 4.9 / 3500).
DEFAULT_FX_RATES: dict[str, float] = {
    "PHP": round(1 / 7.9, 6),
    "MYR": 1.55,
    "THB": round(1 / 4.9, 6),
    "VND": round(1 / 3500, 8),
    "USD": 7.2,
}


def default_fx_rates() -> dict[str, float]:
    rates = dict(DEFAULT_FX_RATES)
    for rule in SEA_REGION_RULES.values():
        rates.setdefault(rule.currency, float(rule.cny_per_local))
    return rates


def merge_fx_rates(overrides: dict[str, Any] | None = None) -> dict[str, float]:
    rates = default_fx_rates()
    if not isinstance(overrides, dict):
        return rates
    for key, raw in overrides.items():
        cur = str(key or "").strip().upper()
        if not cur:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            rates[cur] = value
    return rates


def _sea_rule_with_rates(region: str, fx_rates: dict[str, float]) -> SeaRegionRule:
    rule = SEA_REGION_RULES[region]
    rate = fx_rates.get(rule.currency)
    if rate is None or rate <= 0:
        return rule
    return replace(rule, cny_per_local=float(rate))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_offer_id(value: str) -> str:
    m = re.search(r"offer/(\d+)\.html", value or "")
    if m:
        return m.group(1)
    parsed = urllib.parse.urlparse(value or "")
    if parsed.netloc.lower() == "qr.1688.com":
        request = urllib.request.Request(value, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                expanded = response.geturl()
                body = response.read(8000).decode("utf-8", errors="replace")
            for candidate in (expanded, body):
                m = re.search(
                    r"(?:offer(?:%2[fF]|/)|offer\?id=)(\d+)(?:\.html|%2[eE]html)?",
                    candidate,
                )
                if m:
                    return m.group(1)
                m = re.search(r"wireless1688://[^\s\"']+?[?&]id=(\d+)", candidate)
                if m:
                    return m.group(1)
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
    m = re.search(r"(\d{9,})", value or "")
    if m:
        return m.group(1)
    raise ValueError("Cannot find 1688 offer id. Paste the expanded detail.1688.com URL or an offer id.")


def parse_common_collect_id(value: str) -> str:
    raw = str(value or "").strip()
    explicit = re.search(r"(?:ms|miaoshou|erp|common_collect|collect|采集箱)[:#\s-]*(\d{6,12})", raw, re.I)
    if explicit:
        return explicit.group(1)
    if re.fullmatch(r"\d{6,10}", raw):
        return raw
    m = re.search(r"commonCollectBoxDetailId[=/:\s]+(\d{6,12})", raw, re.I)
    return m.group(1) if m else ""


def resolve_offer_key(value: str) -> str:
    common_id = parse_common_collect_id(value)
    if common_id:
        if (STATE_DIR / f"{common_id}_miaoshou.json").is_file():
            return common_id
        from modules.sourcing.miaoshou_precollect import import_common_collect_detail

        key, _payload = import_common_collect_detail(common_id, state_key=common_id)
        return key
    return parse_offer_id(value)


def _state_path(offer_id: str) -> Path:
    return STATE_DIR / f"{offer_id}.json"


def _false_checks(site_state: dict[str, Any]) -> list[str]:
    return [key for key, value in (site_state.get("checks") or {}).items() if not value]


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _site_draft_lock(offer_id: str) -> threading.Lock:
    lock = _SITE_DRAFT_LOCKS.get(offer_id)
    if lock is None:
        lock = threading.Lock()
        _SITE_DRAFT_LOCKS[offer_id] = lock
    return lock


def _tiktok_claim_lock(offer_id: str) -> threading.Lock:
    lock = _TIKTOK_CLAIM_LOCKS.get(offer_id)
    if lock is None:
        lock = threading.Lock()
        _TIKTOK_CLAIM_LOCKS[offer_id] = lock
    return lock


def _is_english_variant_value(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    letters = [c for c in value if c.isalpha()]
    if not letters:
        return False
    if any("\u4e00" <= c <= "\u9fff" for c in value):
        return False
    ascii_letters = sum(1 for c in letters if ord(c) < 128)
    return ascii_letters / len(letters) >= 0.85


def _english_variant_checks_pass(verified: dict[str, Any]) -> bool:
    props = verified.get("skuPropertyList") or []
    if not props:
        return True
    values = (props[0].get("attrValueList") or [])
    if not values:
        return True
    return all(_is_english_variant_value(value.get("attrValue") or "") for value in values)


def _public_source_type(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    if "temu." in host or host.endswith("temu.com"):
        return "temu"
    if "shopee." in host:
        return "shopee"
    if "lazada." in host:
        return "lazada"
    if "amazon." in host:
        return "amazon"
    if "tiktok." in host:
        return "tiktok"
    return "overseas"


def _html_attr(text: str, key: str) -> str:
    pat = (
        r'<meta[^>]+(?:property|name)=["\']'
        + re.escape(key)
        + r'["\'][^>]+content=["\']([^"\']+)["\']'
    )
    m = re.search(pat, text, re.I)
    if not m:
        pat = (
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']'
            + re.escape(key)
            + r'["\']'
        )
        m = re.search(pat, text, re.I)
    return _html_unescape(m.group(1).strip()) if m else ""


def _html_unescape(value: str) -> str:
    return (
        value.replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )


def _extract_jsonld(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        text,
        re.I | re.S,
    ):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            out.extend([x for x in data if isinstance(x, dict)])
        elif isinstance(data, dict):
            out.append(data)
    return out


def _first_jsonld_product(items: list[dict[str, Any]]) -> dict[str, Any]:
    for item in items:
        typ = item.get("@type")
        types = typ if isinstance(typ, list) else [typ]
        if any(str(x).lower() == "product" for x in types):
            return item
    return {}


def _fetch_public_page(url: str, timeout: int = 18) -> tuple[str, str | None]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(2_000_000)
            ctype = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(ctype, errors="replace"), None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return "", str(e)


def _query_image_candidates(url: str) -> list[str]:
    """Extract public product images embedded in social-share URLs."""
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    images: list[str] = []
    for key in ("share_img", "share_image", "image", "image_url", "img"):
        for value in query.get(key, []):
            candidate = str(value or "").strip()
            for _ in range(2):
                decoded = urllib.parse.unquote(candidate)
                if decoded == candidate:
                    break
                candidate = decoded
            parsed = urllib.parse.urlparse(candidate)
            if parsed.scheme == "https" and parsed.netloc:
                images.append(parsed.geturl())
    return _dedupe_urls(images)


def extract_overseas_material(url: str, *, fetch: bool = True) -> dict[str, Any]:
    clean_url = url.strip()
    if not clean_url:
        raise ValueError("Missing overseas URL.")
    source_type = _public_source_type(clean_url)
    result: dict[str, Any] = {
        "url": clean_url,
        "source_type": source_type,
        "status": "recorded",
        "title": "",
        "description": "",
        "images": _query_image_candidates(clean_url),
        "videos": [],
        "attributes": [],
        "notes": [],
        "fetched_at": None,
    }
    if not fetch:
        result["status"] = "partial" if result["images"] else "recorded"
        result["notes"].append("Recorded only. Fetch was not requested.")
        return result

    html, err = _fetch_public_page(clean_url)
    result["fetched_at"] = _now()
    if err or not html:
        result["status"] = "partial" if result["images"] else "fetch_failed"
        result["notes"].append(f"Fetch failed: {err or 'empty response'}")
        return result

    product = _first_jsonld_product(_extract_jsonld(html))
    title = (
        str(product.get("name") or "")
        or _html_attr(html, "og:title")
        or _html_attr(html, "twitter:title")
    )
    desc = (
        str(product.get("description") or "")
        or _html_attr(html, "og:description")
        or _html_attr(html, "description")
    )
    images: list[str] = list(result["images"])
    image_value = product.get("image")
    if isinstance(image_value, str):
        images.append(image_value)
    elif isinstance(image_value, list):
        images.extend([str(x) for x in image_value if x])
    for key in ("og:image", "twitter:image"):
        img = _html_attr(html, key)
        if img:
            images.append(img)

    videos: list[str] = []
    for key in ("og:video", "og:video:url", "og:video:secure_url"):
        v = _html_attr(html, key)
        if v:
            videos.append(v)

    result.update(
        {
            "status": "fetched" if title or images or videos else "fetch_failed",
            "title": title[:220],
            "description": desc[:700],
            "images": _dedupe_urls(images)[:16],
            "videos": _dedupe_urls(videos)[:4],
        }
    )
    if not result["images"]:
        result["notes"].append("No reusable image URL found in meta/json-ld. Page may require browser rendering.")
    elif not title:
        result["status"] = "partial"
        result["notes"].append("Recovered share image, but the dynamic page did not expose a title.")
    return result


def extract_overseas_material_from_common_collect(common_id: str, *, post=None) -> dict[str, Any]:
    from modules.sourcing.miaoshou_precollect import import_common_collect_detail

    kwargs = {"state_key": f"overseas_{common_id}"}
    if post is not None:
        kwargs["post"] = post
    key, payload = import_common_collect_detail(common_id, **kwargs)
    normalized = payload.get("normalized") or {}
    record = ((payload.get("records") or [{}])[0]) or {}
    source_url = normalized.get("source_url") or record.get("url") or f"miaoshou://common_collect/{common_id}"
    video = normalized.get("video_url") or ""
    return {
        "url": f"ms:{common_id}",
        "source_url": source_url,
        "source_type": (record.get("source") or _public_source_type(source_url) or "miaoshou").lower(),
        "provider": "miaoshou_common_collect",
        "common_collect_id": str(common_id),
        "source_id": normalized.get("source_id") or record.get("source_id") or "",
        "status": "fetched",
        "title": str(normalized.get("title") or record.get("title") or "")[:220],
        "description": "",
        "images": _dedupe_urls([str(x) for x in normalized.get("images") or []])[:24],
        "videos": [video] if video else [],
        "attributes": normalized.get("attributes") or {},
        "notes": ["Imported from Miaoshou common collect detail."],
        "fetched_at": _now(),
        "state_key": key,
    }


def extract_overseas_material_any(value: str, *, fetch: bool = True) -> dict[str, Any]:
    common_id = parse_common_collect_id(value)
    if common_id:
        return extract_overseas_material_from_common_collect(common_id)
    return extract_overseas_material(value, fetch=fetch)


def _dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        u = str(url or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def load_state(offer_id: str) -> dict[str, Any]:
    return _load_json(_state_path(offer_id)) or {}


def save_state(offer_id: str, state: dict[str, Any]) -> dict[str, Any]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state["offer_id"] = offer_id
    state["updated_at"] = _now()
    _state_path(offer_id).write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return state


def _load_source(offer_id: str) -> dict[str, Any]:
    try:
        scrape = load_scrape(offer_id)
    except FileNotFoundError:
        scrape = {}
    sea = _load_json(OUTPUTS_DIR / f"sea_pipeline_preview_{offer_id}.json") or {}
    common = _load_json(OUTPUTS_DIR / f"miaoshou_common_collect_{offer_id}.json") or {}
    precollect = _load_json(STATE_DIR / f"{offer_id}_miaoshou.json") or {}
    return {"scrape": scrape, "sea_preview": sea, "common_collect": common, "precollect": precollect}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _dims(value: Any) -> list[float]:
    if isinstance(value, list):
        nums = [_float(x) for x in value[:3]]
    elif isinstance(value, str):
        nums = [_float(x) for x in re.findall(r"\d+(?:\.\d+)?", value)[:3]]
    else:
        nums = []
    while len(nums) < 3:
        nums.append(0.0)
    return nums[:3]


def _round_up(value: float, step: float) -> float:
    if step <= 0:
        return value
    return math.ceil(value / step) * step


def _ceil_price(value: float, region: str) -> float:
    step = SEA_REGION_RULES.get(region).rounding_step if SEA_REGION_RULES.get(region) else 1
    rounded = _round_up(value, step)
    return int(rounded) if step >= 1 else round(rounded, 2)


def _volumetric_kg(package_cm: list[float]) -> float:
    l, w, h = package_cm
    return round((l * w * h) / 8000, 4) if l and w and h else 0.0


def _money(value: float | None, currency: str, digits: int | None = None) -> str:
    if value is None:
        return "-"
    if digits is None:
        digits = 0 if currency in {"VND"} else 2
    if digits == 0:
        return f"{currency} {value:,.0f}"
    return f"{currency} {value:,.{digits}f}"


def _cny(value: float | None) -> str:
    if value is None:
        return "-"
    digits = 0 if float(value).is_integer() else 2
    return f"CNY {value:,.{digits}f}"


def _local_to_cny(value: float | None, cny_per_local: float) -> float | None:
    if value is None:
        return None
    return round(value * cny_per_local, 4)


def _round_weight_g(weight_g: float) -> int:
    return int(math.ceil(weight_g / 10.0) * 10)


def _sea_logistics_local(region: str, weight_g: float) -> float:
    weight = _round_weight_g(weight_g)
    if region == "PH":
        return round(weight * 0.45, 2)
    if region == "MY":
        return round(weight * 0.015, 2)
    if region == "TH":
        return round(weight * 0.10, 2)
    if region == "VN":
        return float(11700 + max(0, ((weight - 10) // 10)) * 900)
    raise KeyError(region)


def _capped_fee(sale_local: float, rate: float, cap_local: float) -> tuple[float, bool]:
    raw = sale_local * rate
    if rate <= 0:
        return 0.0, False
    if cap_local > 0 and raw > cap_local:
        return cap_local, True
    return raw, False


def _solve_sea_sale_price(rule: SeaRegionRule, goods_local: float, logistics_local: float, target_margin: float) -> tuple[float, dict[str, Any]]:
    fixed_cost = goods_local + logistics_local + rule.fixed_fee_local
    base_variable = (
        rule.commission_rate
        + rule.transaction_rate
        + rule.affiliate_rate
        + rule.ad_rate
        + rule.creator_rate
        + rule.seller_tax_rate
    )
    denominator = 1 - target_margin - base_variable - rule.extra_rate
    sale_local = fixed_cost / denominator
    extra_fee_local, cap_hit = _capped_fee(sale_local, rule.extra_rate, rule.extra_cap_local)
    if cap_hit:
        denominator = 1 - target_margin - base_variable
        sale_local = (fixed_cost + extra_fee_local) / denominator
        extra_fee_local, cap_hit = _capped_fee(sale_local, rule.extra_rate, rule.extra_cap_local)
    return sale_local, {
        "fixed_cost_local": fixed_cost,
        "base_variable_rate": base_variable,
        "extra_fee_local": extra_fee_local,
        "cap_hit": cap_hit,
    }


def _sea_market_row(
    market: dict[str, Any],
    cost_cny: float,
    weight_kg: float,
    package_cm: list[float],
    *,
    fx_rates: dict[str, float] | None = None,
) -> dict[str, Any]:
    region = str(market["region"])
    rates = fx_rates or default_fx_rates()
    rule = _sea_rule_with_rates(region, rates)
    target_margin = SEA_TARGET_MARGIN.get(str(market["shop"]), 0.15)
    actual_weight_g = weight_kg * 1000
    rounded_weight_g = _round_weight_g(actual_weight_g)
    goods_cost_local = cost_cny / rule.cny_per_local
    logistics_local = _sea_logistics_local(region, actual_weight_g)
    sale_local, _meta = _solve_sea_sale_price(rule, goods_cost_local, logistics_local, target_margin)
    commission_local = sale_local * rule.commission_rate
    transaction_local = sale_local * rule.transaction_rate
    extra_fee_local, cap_hit = _capped_fee(sale_local, rule.extra_rate, rule.extra_cap_local)
    affiliate_local = sale_local * rule.affiliate_rate
    ad_local = sale_local * rule.ad_rate
    creator_local = sale_local * rule.creator_rate
    seller_tax_local = sale_local * rule.seller_tax_rate
    profit_local = sale_local - (
        goods_cost_local
        + logistics_local
        + commission_local
        + transaction_local
        + extra_fee_local
        + affiliate_local
        + ad_local
        + creator_local
        + seller_tax_local
        + rule.fixed_fee_local
    )
    list_price_raw = sale_local / (1 - DISCOUNT_RESERVE_RATE)
    list_price = _ceil_price(list_price_raw, region)
    effective_sale_local = round(list_price * (1 - DISCOUNT_RESERVE_RATE), 2)
    effective_profit_local = effective_sale_local - (
        goods_cost_local
        + logistics_local
        + (effective_sale_local * rule.commission_rate)
        + (effective_sale_local * rule.transaction_rate)
        + _capped_fee(effective_sale_local, rule.extra_rate, rule.extra_cap_local)[0]
        + (effective_sale_local * rule.affiliate_rate)
        + (effective_sale_local * rule.ad_rate)
        + (effective_sale_local * rule.creator_rate)
        + (effective_sale_local * rule.seller_tax_rate)
        + rule.fixed_fee_local
    )
    margin_pct = round((effective_profit_local / effective_sale_local) * 100, 2) if effective_sale_local else None
    return {
        **market,
        "currency": rule.currency,
        "target_margin_pct": round(target_margin * 100, 2),
        "cost_cny": round(cost_cny, 2),
        "weight_g": int(round(actual_weight_g)),
        "rounded_weight_g": rounded_weight_g,
        "billable_kg": round(weight_kg, 4),
        "volumetric_kg": _volumetric_kg(package_cm),
        "goods_cost_local": round(goods_cost_local, 2),
        "goods_cost_cny": round(_local_to_cny(goods_cost_local, rule.cny_per_local) or 0, 2),
        "logistics_local": round(logistics_local, 2),
        "logistics_cny": round(_local_to_cny(logistics_local, rule.cny_per_local) or 0, 2),
        "commission_local": round(commission_local, 2),
        "transaction_local": round(transaction_local, 2),
        "extra_fee_local": round(extra_fee_local, 2),
        "extra_fee_cap_local": rule.extra_cap_local,
        "extra_fee_cap_hit": cap_hit,
        "affiliate_local": round(affiliate_local, 2),
        "ad_local": round(ad_local, 2),
        "creator_local": round(creator_local, 2),
        "seller_tax_local": round(seller_tax_local, 2),
        "fixed_fee_local": round(rule.fixed_fee_local, 2),
        "discount_price": round(effective_sale_local, 2),
        "sale_after_discount_local": round(effective_sale_local, 2),
        "list_price": list_price,
        "list_price_raw_local": round(list_price_raw, 2),
        "discount_reserve_pct": int(DISCOUNT_RESERVE_RATE * 100),
        "estimated_profit_local": round(effective_profit_local, 2),
        "estimated_profit_cny": round(_local_to_cny(effective_profit_local, rule.cny_per_local) or 0, 2),
        "profit_margin_on_sale_pct": margin_pct,
        "header_meta": {
            "commission_rate": round(rule.commission_rate * 100, 2),
            "transaction_rate": round(rule.transaction_rate * 100, 2),
            "extra_rate": round(rule.extra_rate * 100, 2),
            "extra_label": rule.extra_label,
            "extra_cap_local": rule.extra_cap_local,
            "affiliate_rate": round(rule.affiliate_rate * 100, 2),
            "ad_rate": round(rule.ad_rate * 100, 2),
            "creator_rate": round(rule.creator_rate * 100, 2),
            "seller_tax_rate": round(rule.seller_tax_rate * 100, 2),
            "fixed_fee_local": round(rule.fixed_fee_local, 2),
            "target_margin_pct": round(target_margin * 100, 2),
        },
        "notes": "SEA reverse pricing; seller absorbs tax; 35% backend discount reserve included.",
        "status": "ok" if margin_pct is not None and margin_pct >= target_margin * 100 - 0.5 else "warn",
    }


def _mx_hidden_shipping(billable_kg: float) -> float:
    if billable_kg <= 0.2:
        return 34.0
    if billable_kg <= 0.5:
        return 48.0
    if billable_kg <= 1:
        return 68.0
    return 88.0


def _uk_shipping(billable_kg: float) -> float:
    if billable_kg <= 0.45:
        return 2.79
    if billable_kg <= 1:
        return 3.49
    return 4.29


def _mx_pricing_row(cost_cny: float, weight_kg: float, package_cm: list[float]) -> dict[str, Any]:
    volumetric = _volumetric_kg(package_cm)
    billable = max(weight_kg, volumetric)
    hidden_shipping_local = _mx_hidden_shipping(billable)
    goods_cost_local = cost_cny * MX_RULE["cny_per_local"]
    sale_local = (goods_cost_local + hidden_shipping_local + MX_RULE["per_item_fee_local"]) / (
        1
        - MX_RULE["import_tax_rate"]
        - MX_RULE["commission_rate"]
        - MX_RULE["sfp_rate"]
        - MX_RULE["affiliate_rate"]
        - MX_RULE["ad_rate"]
        - MX_RULE["target_margin"]
    )
    sale_local = _round_up(sale_local, 1.0)
    import_tax_local = sale_local * MX_RULE["import_tax_rate"]
    commission_local = sale_local * MX_RULE["commission_rate"]
    sfp_local = sale_local * MX_RULE["sfp_rate"]
    affiliate_local = sale_local * MX_RULE["affiliate_rate"]
    ad_local = sale_local * MX_RULE["ad_rate"]
    profit_local = sale_local - (
        goods_cost_local
        + hidden_shipping_local
        + import_tax_local
        + commission_local
        + sfp_local
        + affiliate_local
        + ad_local
        + MX_RULE["per_item_fee_local"]
    )
    list_price_raw = sale_local / (1 - MX_RULE["discount_reserve_rate"])
    list_price = int(math.ceil(list_price_raw))
    effective_sale_local = round(list_price * (1 - MX_RULE["discount_reserve_rate"]), 2)
    effective_profit_local = effective_sale_local - (
        goods_cost_local
        + hidden_shipping_local
        + (effective_sale_local * MX_RULE["import_tax_rate"])
        + (effective_sale_local * MX_RULE["commission_rate"])
        + (effective_sale_local * MX_RULE["sfp_rate"])
        + (effective_sale_local * MX_RULE["affiliate_rate"])
        + (effective_sale_local * MX_RULE["ad_rate"])
        + MX_RULE["per_item_fee_local"]
    )
    margin_pct = round((effective_profit_local / effective_sale_local) * 100, 2) if effective_sale_local else None
    return {
        "region": "MX",
        "shop": "LivelyHive",
        "currency": str(MX_RULE["currency"]),
        "cost_cny": round(cost_cny, 2),
        "billable_kg": round(billable, 4),
        "volumetric_kg": volumetric,
        "goods_cost_local": round(goods_cost_local, 2),
        "goods_cost_cny": round(cost_cny, 2),
        "hidden_shipping_local": hidden_shipping_local,
        "hidden_shipping_cny": round(_local_to_cny(hidden_shipping_local, 1 / MX_RULE["cny_per_local"]) or 0, 2),
        "import_tax_local": round(import_tax_local, 2),
        "commission_local": round(commission_local, 2),
        "sfp_local": round(sfp_local, 2),
        "affiliate_local": round(affiliate_local, 2),
        "ad_local": round(ad_local, 2),
        "fixed_fee_local": round(MX_RULE["per_item_fee_local"], 2),
        "discount_price": round(effective_sale_local, 2),
        "sale_after_discount_local": round(effective_sale_local, 2),
        "list_price": list_price,
        "list_price_raw_local": round(list_price_raw, 2),
        "estimated_profit": round(effective_profit_local, 2),
        "estimated_profit_cny": round(effective_profit_local / MX_RULE["cny_per_local"], 2),
        "profit_margin_on_sale_pct": margin_pct,
        "header_meta": {
            "import_tax_rate": round(MX_RULE["import_tax_rate"] * 100, 2),
            "commission_rate": round(MX_RULE["commission_rate"] * 100, 2),
            "sfp_rate": round(MX_RULE["sfp_rate"] * 100, 2),
            "affiliate_rate": round(MX_RULE["affiliate_rate"] * 100, 2),
            "ad_rate": round(MX_RULE["ad_rate"] * 100, 2),
            "target_margin_pct": round(MX_RULE["target_margin"] * 100, 2),
            "discount_reserve_pct": round(MX_RULE["discount_reserve_rate"] * 100, 2),
            "fixed_fee_local": round(MX_RULE["per_item_fee_local"], 2),
        },
        "volumetric_dominates": volumetric > weight_kg,
        "status": "ok" if margin_pct is not None and margin_pct >= MX_RULE["target_margin"] * 100 - 0.5 else "warn",
        "notes": "MX includes import tax, SFP, affiliate, ad, and 30% list discount reserve.",
    }


def _uk_pricing_row(cost_cny: float, weight_kg: float, package_cm: list[float]) -> dict[str, Any]:
    volumetric = _volumetric_kg(package_cm)
    billable = max(weight_kg, volumetric)
    shipping_local = _uk_shipping(billable)
    goods_cost_local = cost_cny / GB_RULE["cny_per_local"]
    sale_local = (goods_cost_local + shipping_local) / (
        1
        - GB_RULE["commission_rate"]
        - GB_RULE["vat_rate"]
        - GB_RULE["smart_promo_rate"]
        - GB_RULE["affiliate_rate"]
        - GB_RULE["ad_rate"]
        - GB_RULE["target_margin"]
    )
    sale_local = round(sale_local, 2)
    vat_local = sale_local * GB_RULE["vat_rate"]
    commission_local = sale_local * GB_RULE["commission_rate"]
    smart_promo_local = sale_local * GB_RULE["smart_promo_rate"]
    affiliate_local = sale_local * GB_RULE["affiliate_rate"]
    ad_local = sale_local * GB_RULE["ad_rate"]
    profit_local = sale_local - (
        goods_cost_local + shipping_local + vat_local + commission_local + smart_promo_local + affiliate_local + ad_local
    )
    list_price_raw = sale_local / (1 - GB_RULE["discount_reserve_rate"])
    list_price = int(math.ceil(list_price_raw))
    effective_sale_local = round(list_price * (1 - GB_RULE["discount_reserve_rate"]), 2)
    effective_profit_local = effective_sale_local - (
        goods_cost_local
        + shipping_local
        + (effective_sale_local * GB_RULE["vat_rate"])
        + (effective_sale_local * GB_RULE["commission_rate"])
        + (effective_sale_local * GB_RULE["smart_promo_rate"])
        + (effective_sale_local * GB_RULE["affiliate_rate"])
        + (effective_sale_local * GB_RULE["ad_rate"])
    )
    margin_pct = round((effective_profit_local / effective_sale_local) * 100, 2) if effective_sale_local else None
    return {
        "region": "GB",
        "shop": "LivelyHive",
        "currency": str(GB_RULE["currency"]),
        "cost_cny": round(cost_cny, 2),
        "billable_kg": round(billable, 4),
        "volumetric_kg": volumetric,
        "goods_cost_local": round(goods_cost_local, 2),
        "goods_cost_cny": round(cost_cny, 2),
        "shipping_local": round(shipping_local, 2),
        "shipping_cny": round(shipping_local * GB_RULE["cny_per_local"], 2),
        "vat_local": round(vat_local, 2),
        "commission_local": round(commission_local, 2),
        "smart_promo_local": round(smart_promo_local, 2),
        "affiliate_local": round(affiliate_local, 2),
        "ad_local": round(ad_local, 2),
        "discount_price": round(effective_sale_local, 2),
        "sale_after_discount_local": round(effective_sale_local, 2),
        "list_price": list_price,
        "list_price_raw_local": round(list_price_raw, 2),
        "estimated_profit": round(effective_profit_local, 2),
        "estimated_profit_cny": round(effective_profit_local * GB_RULE["cny_per_local"], 2),
        "profit_margin_on_sale_pct": margin_pct,
        "header_meta": {
            "commission_rate": round(GB_RULE["commission_rate"] * 100, 2),
            "vat_rate": round(GB_RULE["vat_rate"] * 100, 2),
            "smart_promo_rate": round(GB_RULE["smart_promo_rate"] * 100, 2),
            "affiliate_rate": round(GB_RULE["affiliate_rate"] * 100, 2),
            "ad_rate": round(GB_RULE["ad_rate"] * 100, 2),
            "target_margin_pct": round(GB_RULE["target_margin"] * 100, 2),
            "discount_reserve_pct": round(GB_RULE["discount_reserve_rate"] * 100, 2),
        },
        "volumetric_dominates": volumetric > weight_kg,
        "status": "ok" if margin_pct is not None and margin_pct >= GB_RULE["target_margin"] * 100 - 0.5 else "warn",
        "notes": "GB includes VAT-effective deduction, commission, smart promo, ad, and 25% list discount reserve.",
    }


def price_review(
    cost_cny: float,
    weight_kg: float,
    package_cm: list[float],
    *,
    fx_rates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rates = merge_fx_rates(fx_rates)
    volumetric = _volumetric_kg(package_cm)
    billable = max(weight_kg, volumetric)
    if cost_cny <= 0:
        sea_missing = []
        for market in SEA_MARKETS:
            if market["region"] not in SEA_REGION_RULES:
                continue
            rule = _sea_rule_with_rates(market["region"], rates)
            sea_missing.append({
                **market,
                "currency": rule.currency,
                "cost_cny": None,
                "billable_kg": round(billable, 4),
                "volumetric_kg": volumetric,
                "list_price": None,
                "discount_price": None,
                "profit_margin_on_sale_pct": None,
                "estimated_profit_cny": None,
                "status": "missing_cost",
                "notes": "Source cost is required before pricing review.",
            })
        pending = {
            "cost_cny": None,
            "billable_kg": round(billable, 4),
            "volumetric_kg": volumetric,
            "list_price": None,
            "discount_price": None,
            "estimated_shipping": None,
            "estimated_profit": None,
            "profit_margin_on_sale_pct": None,
            "volumetric_dominates": volumetric > weight_kg,
            "status": "missing_cost",
            "notes": "Source cost is required before pricing review.",
        }
        return {
            "input": {
                "cost_cny": cost_cny,
                "weight_kg": weight_kg,
                "package_cm": package_cm,
                "volumetric_kg": volumetric,
                "billable_kg": round(billable, 4),
            },
            "sea": sea_missing,
            "mx": {"region": "MX", "currency": "MXN", **pending},
            "uk": {"region": "GB", "currency": "GBP", **pending},
            "rates": rates,
            "audit": {"sections": []},
        }

    sea = [
        _sea_market_row(market, cost_cny, weight_kg, package_cm, fx_rates=rates)
        for market in SEA_MARKETS
        if market["region"] in SEA_REGION_RULES
    ]
    mx = _mx_pricing_row(cost_cny, weight_kg, package_cm)
    uk = _uk_pricing_row(cost_cny, weight_kg, package_cm)
    sea_audit_rows: list[dict[str, Any]] = []
    for row in sea:
        header = row.get("header_meta") or {}
        rule = _sea_rule_with_rates(str(row["region"]), rates)
        sea_audit_rows.append({
            "section": f"SEA_{row['shop']}_{row['region']}",
            "title": f"{row['shop']} {row['region']} · 1 {row['currency']} = {rule.cny_per_local:g} CNY",
            "currency": row["currency"],
            "header_labels": [
                "品牌",
                "物流重",
                "货值",
                "物流",
                f"佣金({header.get('commission_rate', 0):.2f}%)",
                f"交易费({header.get('transaction_rate', 0):.2f}%)",
                f"{header.get('extra_label', '额外费')}({header.get('extra_rate', 0):.2f}%)",
                f"广告费({header.get('ad_rate', 0):.2f}%)",
                f"达人费({header.get('creator_rate', 0):.2f}%)",
                f"卖家税({header.get('seller_tax_rate', 0):.2f}%)",
                "35%后成交价",
                "挂牌价",
                "利润",
                "利润率",
            ],
            "notes": [
                f"目标利润 {header.get('target_margin_pct', 0):.2f}%",
                f"汇率 1 {rule.currency} = {rule.cny_per_local:g} CNY",
                f"固定费用 {_money(header.get('fixed_fee_local', 0), rule.currency)}",
                (
                    f"费率封顶 {_money(header.get('extra_cap_local', 0), rule.currency)}"
                    if header.get("extra_cap_local")
                    else ""
                ),
            ],
            "rows": [[
                row["shop"],
                f"{row.get('rounded_weight_g', 0)} g",
                f"{_money(row.get('goods_cost_local'), row['currency'])}\n{_cny(row.get('goods_cost_cny'))}",
                f"{_money(row.get('logistics_local'), row['currency'])}\n{_cny(row.get('logistics_cny'))}",
                _money(row.get("commission_local"), row["currency"]),
                _money(row.get("transaction_local"), row["currency"]),
                _money(row.get("extra_fee_local"), row["currency"]),
                _money(row.get("ad_local"), row["currency"]),
                _money(row.get("creator_local"), row["currency"]),
                _money(row.get("seller_tax_local"), row["currency"]),
                f"{_money(row.get('discount_price'), row['currency'])}\n{_cny(round((row.get('discount_price') or 0) * rule.cny_per_local, 2))}",
                f"{_money(row.get('list_price'), row['currency'], 0 if row['currency'] == 'VND' else 2)}\n{_cny(round((row.get('list_price') or 0) * rule.cny_per_local, 2))}",
                f"{_money(row.get('estimated_profit_local'), row['currency'])}\n{_cny(row.get('estimated_profit_cny'))}",
                f"{row.get('profit_margin_on_sale_pct', 0):.2f}%",
            ]],
        })

    mx_header = mx.get("header_meta") or {}
    uk_header = uk.get("header_meta") or {}

    return {
        "input": {
            "cost_cny": cost_cny,
            "weight_kg": weight_kg,
            "package_cm": package_cm,
            "volumetric_kg": volumetric,
            "billable_kg": round(billable, 4),
        },
        "sea": sea,
        "mx": {
            **mx,
            "estimated_shipping": mx.get("hidden_shipping_local"),
            "estimated_profit": mx.get("estimated_profit"),
        },
        "uk": {
            **uk,
            "estimated_shipping": uk.get("shipping_local"),
            "estimated_profit": uk.get("estimated_profit"),
        },
        "rates": rates,
        "audit": {
            "sections": sea_audit_rows + [
                {
                    "section": "MX",
                    "title": "LivelyHive MX",
                    "currency": "MXN",
                    "header_labels": [
                        "物流重",
                        "货值",
                        "隐藏物流",
                        f"进口税({mx_header.get('import_tax_rate', 0):.2f}%)",
                        f"佣金({mx_header.get('commission_rate', 0):.2f}%)",
                        f"SFP({mx_header.get('sfp_rate', 0):.2f}%)",
                        f"达人费({mx_header.get('affiliate_rate', 0):.2f}%)",
                        f"广告费({mx_header.get('ad_rate', 0):.2f}%)",
                        "35%后成交价",
                        "挂牌价",
                        "利润",
                        "利润率",
                    ],
                    "notes": [
                        f"目标利润 {mx_header.get('target_margin_pct', 0):.2f}%",
                        f"固定费用 {_money(mx_header.get('fixed_fee_local', 0), 'MXN')}",
                        f"后台折扣预留 {mx_header.get('discount_reserve_pct', 0):.2f}%",
                    ],
                    "rows": [[
                        f"{mx.get('billable_kg', 0):.2f} kg",
                        f"{_money(mx.get('goods_cost_local'), 'MXN')}\n{_cny(mx.get('goods_cost_cny'))}",
                        f"{_money(mx.get('hidden_shipping_local'), 'MXN')}\n{_cny(mx.get('hidden_shipping_cny'))}",
                        _money(mx.get("import_tax_local"), "MXN"),
                        _money(mx.get("commission_local"), "MXN"),
                        _money(mx.get("sfp_local"), "MXN"),
                        _money(mx.get("affiliate_local"), "MXN"),
                        _money(mx.get("ad_local"), "MXN"),
                        f"{_money(mx.get('discount_price'), 'MXN')}\n{_cny(round((mx.get('discount_price') or 0) / MX_RULE['cny_per_local'], 2))}",
                        f"{_money(mx.get('list_price'), 'MXN')}\n{_cny(round((mx.get('list_price') or 0) / MX_RULE['cny_per_local'], 2))}",
                        f"{_money(mx.get('estimated_profit'), 'MXN')}\n{_cny(mx.get('estimated_profit_cny'))}",
                        f"{mx.get('profit_margin_on_sale_pct', 0):.2f}%",
                    ]],
                },
                {
                    "section": "GB",
                    "title": "LivelyHive GB",
                    "currency": "GBP",
                    "header_labels": [
                        "物流重",
                        "货值",
                        "本地物流",
                        f"VAT({uk_header.get('vat_rate', 0):.2f}%)",
                        f"佣金({uk_header.get('commission_rate', 0):.2f}%)",
                        f"Smart Promo({uk_header.get('smart_promo_rate', 0):.2f}%)",
                        f"达人费({uk_header.get('affiliate_rate', 0):.2f}%)",
                        f"广告费({uk_header.get('ad_rate', 0):.2f}%)",
                        "35%后成交价",
                        "挂牌价",
                        "利润",
                        "利润率",
                    ],
                    "notes": [
                        f"目标利润 {uk_header.get('target_margin_pct', 0):.2f}%",
                        f"后台折扣预留 {uk_header.get('discount_reserve_pct', 0):.2f}%",
                    ],
                    "rows": [[
                        f"{uk.get('billable_kg', 0):.2f} kg",
                        f"{_money(uk.get('goods_cost_local'), 'GBP')}\n{_cny(uk.get('goods_cost_cny'))}",
                        f"{_money(uk.get('shipping_local'), 'GBP')}\n{_cny(uk.get('shipping_cny'))}",
                        _money(uk.get("vat_local"), "GBP"),
                        _money(uk.get("commission_local"), "GBP"),
                        _money(uk.get("smart_promo_local"), "GBP"),
                        _money(uk.get("affiliate_local"), "GBP"),
                        _money(uk.get("ad_local"), "GBP"),
                        f"{_money(uk.get('discount_price'), 'GBP')}\n{_cny(round((uk.get('discount_price') or 0) * GB_RULE['cny_per_local'], 2))}",
                        f"{_money(uk.get('list_price'), 'GBP')}\n{_cny(round((uk.get('list_price') or 0) * GB_RULE['cny_per_local'], 2))}",
                        f"{_money(uk.get('estimated_profit'), 'GBP')}\n{_cny(uk.get('estimated_profit_cny'))}",
                        f"{uk.get('profit_margin_on_sale_pct', 0):.2f}%",
                    ]],
                },
            ],
        },
    }


def _source_summary(offer_id: str) -> dict[str, Any]:
    src = _load_source(offer_id)
    scrape = src["scrape"]
    sea = src["sea_preview"]
    precollect = src["precollect"]
    miaoshou = precollect.get("normalized") or {}
    price_obj = scrape.get("price") if isinstance(scrape.get("price"), dict) else {}
    images = []
    if sea.get("images"):
        images = [{"url": u, "kind": "main", "action": "keep", "note": ""} for u in sea.get("images") or []]
    elif scrape:
        for u in ((scrape.get("images") or {}).get("main") or []):
            images.append({"url": u, "kind": "main", "action": "keep", "note": ""})
        for u in ((scrape.get("images") or {}).get("detail") or [])[:12]:
            images.append({"url": u, "kind": "detail", "action": "review", "note": ""})
    elif miaoshou.get("images"):
        main_count = int(miaoshou.get("main_image_count") or 0)
        for idx, url in enumerate(miaoshou.get("images") or []):
            images.append(
                {
                    "url": url,
                    "kind": "main" if idx < main_count else "detail",
                    "action": "review",
                    "note": "Miaoshou source capture; check Chinese text and usefulness.",
                }
            )

    sea_weight = sea.get("weightKg")
    ms_weight = miaoshou.get("weight_kg") if miaoshou.get("weight_present") else None
    sea_package = sea.get("packageCm")
    ms_package = miaoshou.get("package_cm") or None
    category_path = miaoshou.get("category_path") or []
    precollect_records = [
        {k: row.get(k) for k in ("source", "source_id", "common_collect_id", "status", "title", "url", "notes")}
        for row in precollect.get("records") or []
    ]
    precollect_risks = [
        f"Miaoshou precollect failed for {row.get('source') or row.get('source_id') or 'source'}"
        for row in precollect_records
        if row.get("status") == "fail"
    ]

    return {
        "offer_id": offer_id,
        "source_url": sea.get("sourceUrl") or scrape.get("url") or miaoshou.get("source_url") or f"https://detail.1688.com/offer/{offer_id}.html",
        "source_item_code": sea.get("sourceItemCode") or miaoshou.get("source_item_code") or "",
        "title_source": sea.get("sourceTitle") or scrape.get("title") or miaoshou.get("title") or "",
        "title_source_kind": "miaoshou" if miaoshou.get("title") and not (sea.get("sourceTitle") or scrape.get("title")) else "",
        "title_recommended": ((sea.get("intel") or {}).get("recommended_title") or ""),
        "cost_cny": _float(sea.get("sourcePrice") or price_obj.get("display") or price_obj.get("min") or miaoshou.get("cost_cny")),
        "stock": int(_float(sea.get("sourceStock") or scrape.get("stock") or miaoshou.get("stock"), 0)),
        "weight_kg": _float(sea_weight if sea_weight is not None else ms_weight, 0.2),
        "weight_is_estimate": sea_weight is None and ms_weight is None,
        "package_cm": _dims(sea_package or ms_package or [20, 20, 3]),
        "package_is_estimate": not bool(sea_package or ms_package),
        "seller_sku": str(sea.get("proposedSellerSku") or "").zfill(4)[-4:] if sea.get("proposedSellerSku") else "",
        "category": {
            "id": "",
            "name": " > ".join(category_path) if category_path else "Home Supplies > Home Decor > Statues & Figurines",
            "confidence": "miaoshou-source" if category_path else "manual-default",
        },
        "video": {
            "url": sea.get("videoUrl") or miaoshou.get("video_url") or "",
            "action": "keep" if (sea.get("videoUrl") or miaoshou.get("video_url")) else "none",
        },
        "support_cod": True,
        "images": images,
        "attributes": miaoshou.get("attributes") or {},
        "skus": miaoshou.get("skus") or [],
        "precollect": {
            "mode": precollect.get("mode"),
            "records": precollect_records,
            "claimed": bool(precollect.get("claimed")),
            "published": bool(precollect.get("published")),
            "updated_at": precollect.get("updated_at"),
        },
        "risks": ((sea.get("intel") or {}).get("risks") or []) + precollect_risks,
    }


def build_preview(offer_id_or_url: str, *, source_code: str = "") -> dict[str, Any]:
    offer_id = resolve_offer_key(offer_id_or_url)
    source = _source_summary(offer_id)
    state = load_state(offer_id)
    review = state.get("review") or {}
    overseas_sources = state.get("overseas_sources") or []
    overseas_primary = next(
        (x for x in overseas_sources if x.get("title") or x.get("images") or x.get("videos")),
        {},
    )
    if not source.get("title_source") and overseas_primary.get("title"):
        source["title_source"] = overseas_primary["title"]
        source["title_source_kind"] = overseas_primary.get("source_type") or "overseas"
    if not source.get("images") and overseas_primary.get("images"):
        source["images"] = [
            {
                "url": url,
                "kind": "overseas",
                "action": "redraw",
                "note": f"remove platform branding; candidate from {overseas_primary.get('source_type') or 'overseas'}",
            }
            for url in overseas_primary.get("images") or []
        ]
    if not (source.get("video") or {}).get("url") and overseas_primary.get("videos"):
        source["video"] = {"url": overseas_primary["videos"][0], "action": "review"}

    missing_fields = []
    if not source.get("title_source") and not review.get("title"):
        missing_fields.append("title")
    if not source.get("cost_cny"):
        missing_fields.append("source_price")
    if not source.get("images"):
        missing_fields.append("images")
    if source.get("weight_is_estimate"):
        missing_fields.append("weight")
    if source.get("package_is_estimate"):
        missing_fields.append("package_dimensions")
    source["data_status"] = "ready" if not missing_fields else "incomplete"
    source["missing_fields"] = missing_fields
    source["data_notes"] = (
        []
        if not missing_fields
        else ["1688 automatic fetch was blocked or no local scrape cache exists."]
    )
    overseas_images = []
    for src in overseas_sources:
        for img_url in src.get("images") or []:
            overseas_images.append(
                {
                    "url": img_url,
                    "kind": "overseas",
                    "action": "redraw",
                    "note": f"remove platform branding; candidate from {src.get('source_type') or 'overseas'}",
                    "source_url": src.get("url") or "",
                }
            )
    if source_code and not source.get("source_item_code"):
        source["source_item_code"] = source_code
    weight = _float(review.get("weight_kg"), source["weight_kg"])
    dims = _dims(review.get("package_cm") or source["package_cm"])
    cost = _float(review.get("cost_cny"), source["cost_cny"])
    fx_rates = merge_fx_rates(review.get("fx_rates"))
    miaoshou_draft = _load_json(STATE_DIR / f"{offer_id}_miaoshou_draft.json") or {}
    tiktok_claim = _load_json(STATE_DIR / f"{offer_id}_tiktok_claim.json") or {}
    site_drafts = _load_json(STATE_DIR / f"{offer_id}_site_drafts.json") or {}

    return {
        "ok": True,
        "mode": "first_review_no_model_call",
        "offer_id": offer_id,
        "source": source,
        "review": {
            "selected_sites": review.get("selected_sites") or [m["id"] for m in SEA_MARKETS if m.get("enabled")],
            "title": review.get("title") or source.get("title_recommended") or source.get("title_source"),
            "seller_sku": review.get("seller_sku") or source.get("seller_sku"),
            "category": review.get("category") or source.get("category"),
            "weight_kg": weight,
            "package_cm": dims,
            "video_action": review.get("video_action") or source.get("video", {}).get("action"),
            "support_cod": True,
            "image_actions": review.get("image_actions") or source.get("images"),
            "overseas_image_candidates": overseas_images,
            "image_generation_requests": review.get("image_generation_requests") or [],
            "fields_locked": bool(review.get("fields_locked")),
            "fx_rates": fx_rates,
        },
        "overseas_sources": overseas_sources,
        "target_sites": SEA_MARKETS,
        "pricing": price_review(cost, weight, dims, fx_rates=fx_rates),
        "miaoshou_draft": {
            "ready": bool(miaoshou_draft.get("ready")),
            "written_to_miaoshou": bool(miaoshou_draft.get("written_to_miaoshou")),
            "verified": bool(miaoshou_draft.get("verified")),
            "second_review_approved": bool(miaoshou_draft.get("second_review_approved")),
            "item_num": ((miaoshou_draft.get("draft") or {}).get("itemNum") or ""),
            "image_count": len((miaoshou_draft.get("draft") or {}).get("imgUrls") or []),
            "claimed": bool(miaoshou_draft.get("claimed")),
            "published": bool(miaoshou_draft.get("published")),
        },
        "tiktok_claim": {
            "claimed": bool(tiktok_claim.get("claimed")),
            "tiktok_detail_id": tiktok_claim.get("tiktok_detail_id"),
            "claimed_shop_count": len(tiktok_claim.get("shops") or {}),
            "blocked_sites": tiktok_claim.get("blocked_sites") or {},
            "published": bool(tiktok_claim.get("published")),
            "in_progress": bool(tiktok_claim.get("in_progress")),
            "current_run_id": tiktok_claim.get("current_run_id"),
            "last_error": tiktok_claim.get("last_error") or "",
            "started_at": tiktok_claim.get("started_at"),
            "updated_at": tiktok_claim.get("updated_at"),
        },
        "site_drafts": {
            "ready": bool(site_drafts.get("ready")),
            "site_count": len(site_drafts.get("sites") or {}),
            "sites": site_drafts.get("sites") or {},
            "failed_checks": {k: _false_checks(v) for k, v in (site_drafts.get("sites") or {}).items()},
            "blocked_sites": site_drafts.get("blocked_sites") or {},
            "published": bool(site_drafts.get("published")),
            "in_progress": bool(site_drafts.get("in_progress")),
            "current_run_id": site_drafts.get("current_run_id"),
            "last_error": site_drafts.get("last_error") or "",
            "started_at": site_drafts.get("started_at"),
            "updated_at": site_drafts.get("updated_at"),
        },
        "steps": [
            "First review page only uses local data and rule pricing.",
            "Changing weight or package dimensions recalculates SEA, MX, and UK audits immediately.",
            "Image generation requests are saved but do not call paid APIs until an optimize step is approved.",
            "COD support is forced on for all new products in this workflow.",
            "Hive notification is intentionally absent in this workflow version.",
        ],
        "updated_at": state.get("updated_at"),
    }


def precollect_preview(
    offer_id_or_url: str,
    *,
    overseas_urls: list[str] | None = None,
    source_code: str = "",
    force: bool = False,
) -> dict[str, Any]:
    from modules.sourcing.miaoshou_precollect import import_common_collect_detail, refresh_precollect

    common_id = parse_common_collect_id(offer_id_or_url)
    if common_id:
        offer_id, _payload = import_common_collect_detail(common_id, state_key=common_id)
        result = build_preview(offer_id, source_code=source_code)
        result["mode"] = "first_review_miaoshou_common_collect_detail"
        return result

    offer_id = parse_offer_id(offer_id_or_url)
    source_url = f"https://detail.1688.com/offer/{offer_id}.html"
    state = load_state(offer_id)
    urls = [str(x).strip() for x in overseas_urls or [] if str(x).strip()]
    if not urls:
        urls = [str(x.get("url") or "").strip() for x in state.get("overseas_sources") or [] if x.get("url")]
    common_inputs = [x for x in urls if parse_common_collect_id(x)]
    link_inputs = [x for x in urls if not parse_common_collect_id(x)]
    if common_inputs:
        existing = {x.get("url"): x for x in state.get("overseas_sources") or [] if x.get("url")}
        for item in common_inputs:
            material = extract_overseas_material_from_common_collect(parse_common_collect_id(item))
            existing[material["url"]] = material
        state["overseas_sources"] = list(existing.values())
        save_state(offer_id, state)
    refresh_precollect(offer_id, source_url, link_inputs, force=force)
    result = build_preview(offer_id, source_code=source_code)
    result["mode"] = "first_review_miaoshou_precollect"
    return result


def save_review(offer_id_or_url: str, review: dict[str, Any]) -> dict[str, Any]:
    offer_id = resolve_offer_key(offer_id_or_url)
    state = load_state(offer_id)
    current = state.get("review") or {}
    current.update(review)
    state["review"] = current
    save_state(offer_id, state)
    return build_preview(offer_id)


def add_image_request(offer_id_or_url: str, prompt: str, *, kind: str = "supplement") -> dict[str, Any]:
    offer_id = resolve_offer_key(offer_id_or_url)
    state = load_state(offer_id)
    review = state.setdefault("review", {})
    reqs = review.setdefault("image_generation_requests", [])
    reqs.append({
        "id": f"imgreq_{len(reqs) + 1:03d}",
        "kind": kind,
        "prompt": prompt.strip(),
        "status": "pending_api_approval",
        "created_at": _now(),
    })
    save_state(offer_id, state)
    return build_preview(offer_id)


def add_overseas_source(offer_id_or_url: str, url: str, *, fetch: bool = True) -> dict[str, Any]:
    offer_id = resolve_offer_key(offer_id_or_url)
    state = load_state(offer_id)
    sources = state.setdefault("overseas_sources", [])
    material = extract_overseas_material_any(url, fetch=fetch)
    sources[:] = [x for x in sources if x.get("url") != material["url"]]
    sources.append(material)
    save_state(offer_id, state)
    return build_preview(offer_id)


def save_overseas_sources(offer_id_or_url: str, urls: list[str], *, fetch: bool = False) -> dict[str, Any]:
    offer_id = resolve_offer_key(offer_id_or_url)
    state = load_state(offer_id)
    existing = {x.get("url"): x for x in state.get("overseas_sources") or [] if x.get("url")}
    sources: list[dict[str, Any]] = []
    for url in urls:
        clean = str(url or "").strip()
        if not clean:
            continue
        if fetch:
            sources.append(extract_overseas_material_any(clean, fetch=True))
        else:
            material = extract_overseas_material_any(clean, fetch=False) if parse_common_collect_id(clean) else extract_overseas_material(clean, fetch=False)
            sources.append(existing.get(material["url"]) or existing.get(clean) or material)
    state["overseas_sources"] = sources
    save_state(offer_id, state)
    return build_preview(offer_id)


def _next_seller_sku() -> str:
    db_path = ROOT / "data" / "shop.db"
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        values: list[int] = []
        for (raw,) in conn.execute(
            "select seller_sku from products "
            "where seller_sku is not null and length(seller_sku) > 0"
        ):
            digits = "".join(ch for ch in str(raw) if ch.isdigit())
            if digits:
                values.append(int(digits[-4:]))
    finally:
        conn.close()
    if not values:
        raise RuntimeError("商品目录中没有可用于分配 Seller SKU 的数字记录")
    return f"{max(values) + 1:04d}"[-4:]


def _sequential_sku_numbers(sku_map: dict[str, Any], base_sku: str) -> dict[str, str]:
    digits = "".join(ch for ch in str(base_sku or "") if ch.isdigit())
    if not digits:
        raise RuntimeError("缺少可连续编号的平台 SKU 起始值")
    start = int(digits[-4:])
    return {
        key: f"{start + index:04d}"[-4:]
        for index, key in enumerate(sku_map)
    }


def _normalize_title(title: str) -> str:
    value = re.sub(r"\s+", " ", str(title or "")).strip()
    if not value:
        return ""
    tokens = value.split(" ")
    if len(tokens) >= 6 and len(tokens) % 2 == 0:
        half = len(tokens) // 2
        if [token.lower() for token in tokens[:half]] == [token.lower() for token in tokens[half:]]:
            tokens = tokens[:half]
    for size in range(min(8, len(tokens) // 2), 1, -1):
        if [token.lower() for token in tokens[-2 * size:-size]] == [token.lower() for token in tokens[-size:]]:
            tokens = tokens[:-size]
            break
    return " ".join(tokens).strip()


def _distribute_total(total: int, count: int) -> list[int]:
    count = max(1, int(count or 0))
    total = max(count, int(total or 0))
    base = total // count
    remainder = total % count
    return [base + (1 if index < remainder else 0) for index in range(count)]


def _stock_per_shop(sku: dict[str, Any], shop_count: int) -> int:
    mapped = []
    for warehouses in (sku.get("shopIdToWarehouseIdAndStockMap") or {}).values():
        for value in (warehouses or {}).values():
            try:
                if int(value) > 0:
                    mapped.append(int(value))
            except (TypeError, ValueError):
                pass
    if mapped:
        return max(mapped)
    total = int(sku.get("stock") or DEFAULT_LISTING_STOCK)
    return max(1, total // max(1, shop_count))


def _publish_group_for_target(target: dict[str, Any]) -> str:
    group = str(target.get("publish_group") or "").strip().lower()
    if group:
        return group
    return "homebloom" if str(target.get("shop") or "").strip().lower() == "homebloom" else "lively"


def _anchor_group_key(target: dict[str, Any]) -> str:
    shop = str(target.get("shop") or "").strip().lower()
    if shop == "homebloom":
        return "homebloom"
    if shop == "livelyhive" or not shop:
        return "lively"
    region = str(target.get("region") or "").strip().upper()
    return f"{shop or 'shop'}_{region or 'default'}"


def _claim_anchor_shop_ids(group_targets: list[tuple[str, dict[str, Any], str]]) -> list[str]:
    anchors: dict[str, str] = {}
    for _target_id, target, shop_id in group_targets:
        anchors.setdefault(_anchor_group_key(target), str(shop_id))
    return sorted(anchors.values())


def _claim_all_shop_ids(group_targets: list[tuple[str, dict[str, Any], str]]) -> list[str]:
    return sorted({
        str(shop_id).strip()
        for _target_id, _target, shop_id in (group_targets or [])
        if str(shop_id).strip()
    })


def _detail_group_for_target(target: dict[str, Any]) -> str:
    return str(target.get("detail_group") or _anchor_group_key(target))


def _pick_default_warehouse_id(warehouse_rows: list[dict[str, Any]]) -> str:
    active = [row for row in (warehouse_rows or []) if str(row.get("warehouseEffectStatus") or "1") == "1"]
    if not active:
        return ""
    active.sort(
        key=lambda row: (
            str(row.get("isDefault") or "0") != "1",
            str(row.get("warehouseSubType") or "") != "3",
            str(row.get("warehouseId") or ""),
        )
    )
    return str(active[0].get("warehouseId") or "")


def _web_related_shop_rows(payload: dict[str, Any], anchor_shop_id: str) -> list[dict[str, Any]]:
    related_map = payload.get("shopIdAndRelatedShopListMap") or {}
    rows = related_map.get(str(anchor_shop_id)) or []
    return [dict(row or {}) for row in rows]


def _web_collect_payload_for_targets(
    payload: dict[str, Any],
    *,
    selected_targets: list[tuple[str, dict[str, Any], dict[str, Any]]],
    draft: dict[str, Any],
    cod_enabled: bool,
    stock_total: int = DEFAULT_LISTING_STOCK,
) -> dict[str, Any]:
    info = json.loads(json.dumps(payload.get("shopCollectItemInfo") or {}, ensure_ascii=False))
    if not info:
        raise RuntimeError("Miaoshou web collect payload is missing shopCollectItemInfo")
    anchor_shop_id = str(info.get("shopId") or "")
    if not anchor_shop_id:
        raise RuntimeError("Miaoshou web collect payload is missing anchor shopId")

    target_by_region = {
        str(shop.get("region") or ""): (target_id, shop, pricing)
        for target_id, shop, pricing in selected_targets
    }
    anchor_region = str(info.get("site") or "")
    anchor_target = target_by_region.get(anchor_region)
    if not anchor_target:
        raise RuntimeError(f"Anchor region {anchor_region} is not present in selected targets")

    info["title"] = _normalize_title(draft.get("title") or info.get("title") or "")
    info["notes"] = draft.get("notes") or info.get("notes") or ""
    info["imgUrls"] = list(draft.get("imgUrls") or [])
    info["weight"] = float(draft.get("weight") or 0)
    info["packageLength"] = float(draft.get("packageLength") or 0)
    info["packageWidth"] = float(draft.get("packageWidth") or 0)
    info["packageHeight"] = float(draft.get("packageHeight") or 0)
    info["mainImgVideoUrl"] = draft.get("mainImgVideoUrl") or ""
    info["mainImgAppVideoId"] = ""
    info["mainImgPlatformVideoId"] = ""
    info["isCodOpen"] = "1" if cod_enabled else "0"
    info["itemNum"] = str(draft.get("itemNum") or info.get("itemNum") or "")[-4:]

    sku_map = info.get("skuMap") or {}
    sku_numbers = _sequential_sku_numbers(sku_map, info["itemNum"])
    per_sku_stock = _distribute_total(stock_total, len(sku_map) or 1)
    anchor_price = float(anchor_target[2]["list_price"])
    anchor_default_map = info.get("shopIdAndDefaultWarehouseIdsMap") or {}
    anchor_warehouse_id = str(anchor_default_map.get(anchor_shop_id) or info.get("warehouseId") or "")

    for index, (sku_key, sku) in enumerate(sku_map.items()):
        sku_stock = per_sku_stock[index] if index < len(per_sku_stock) else stock_total
        sku["price"] = anchor_price
        sku["priceIncludeVat"] = anchor_price
        sku["itemNum"] = sku_numbers[sku_key]
        sku["stock"] = str(sku_stock)
        sku["weight"] = info["weight"]
        sku["packageLength"] = info["packageLength"]
        sku["packageWidth"] = info["packageWidth"]
        sku["packageHeight"] = info["packageHeight"]
        if anchor_warehouse_id:
            sku["shopIdToWarehouseIdAndStockMap"] = {
                anchor_shop_id: {anchor_warehouse_id: str(sku_stock)}
            }

    related_rows = _web_related_shop_rows(payload, anchor_shop_id=anchor_shop_id)
    related_by_region = {str(row.get("site") or ""): row for row in related_rows}
    selected_related_rows: list[dict[str, Any]] = []
    selected_related_regions: list[str] = []
    for region, (_target_id, _shop, pricing) in target_by_region.items():
        if region == anchor_region:
            continue
        row = dict(related_by_region.get(region) or {})
        if not row:
            continue
        selected_related_regions.append(region)
        warehouse_id = _pick_default_warehouse_id(row.get("warehouseList") or [])
        region_price = float(pricing["list_price"])
        total_row_stock = 0
        prices: list[float] = []
        for index, sku in enumerate(row.get("skus") or []):
            sku_stock = per_sku_stock[index] if index < len(per_sku_stock) else per_sku_stock[-1]
            sku["priceIncludeVat"] = f"{region_price:.2f}"
            sku["stock"] = str(sku_stock)
            sku["stockInfos"] = (
                [{"warehouseId": warehouse_id, "availableStock": str(sku_stock)}]
                if warehouse_id
                else []
            )
            total_row_stock += sku_stock
            prices.append(region_price)
        row["stock"] = str(total_row_stock)
        if prices:
            row["minPriceIncludeVat"] = f"{min(prices):.2f}"
            row["maxPriceIncludeVat"] = f"{max(prices):.2f}"
        selected_related_rows.append(row)

    info["shopIdAndReplicatedProductsMap"] = {anchor_shop_id: selected_related_rows}
    return {
        "shopCollectItemInfo": info,
        "anchor_shop_id": anchor_shop_id,
        "anchor_region": anchor_region,
        "selected_related_regions": selected_related_regions,
        "sku_item_nums": list(sku_numbers.values()),
    }


def _expected_region_site_state(
    region: str,
    region_shops: list[tuple[str, dict[str, Any]]],
    prepared_targets: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    expected_detail_ids = sorted({
        int(prepared_targets[target_id]["detail_id"])
        for target_id, _shop in region_shops
        if prepared_targets.get(target_id) and prepared_targets[target_id].get("detail_id")
    })
    expected_shop_ids = sorted({
        str(shop.get("shop_id") or "")
        for _target_id, shop in region_shops
        if str(shop.get("shop_id") or "").strip()
    })
    return {
        "region": region,
        "detail_ids": expected_detail_ids,
        "site_collect_shop_ids": expected_shop_ids,
    }


def _site_state_matches_expected(existing_site: dict[str, Any], expected_state: dict[str, Any]) -> bool:
    if not existing_site or not existing_site.get("ready"):
        return False
    if int(existing_site.get("sku_scheme_version") or 0) != 3:
        return False
    actual_detail_ids = sorted(
        int(detail_id)
        for detail_id in (existing_site.get("detail_ids") or [])
        if detail_id not in (None, "")
    )
    actual_shop_ids = sorted(
        str(shop_id)
        for shop_id in (existing_site.get("site_collect_shop_ids") or [])
        if str(shop_id or "").strip()
    )
    return (
        actual_detail_ids == list(expected_state.get("detail_ids") or [])
        and actual_shop_ids == list(expected_state.get("site_collect_shop_ids") or [])
    )


def prepare_miaoshou_draft(offer_id_or_url: str) -> dict[str, Any]:
    """Build a local, no-write draft for the Miaoshou second review."""
    offer_id = resolve_offer_key(offer_id_or_url)
    preview = build_preview(offer_id)
    review = preview.get("review") or {}
    source = preview.get("source") or {}
    blockers: list[str] = []

    if not review.get("fields_locked"):
        blockers.append("第一轮审核尚未锁定")

    seller_sku = str(review.get("seller_sku") or "").strip()
    if not seller_sku:
        try:
            seller_sku = _next_seller_sku()
        except (OSError, sqlite3.Error, RuntimeError) as exc:
            blockers.append(f"Seller SKU 自动分配失败: {exc}")

    selected_images: list[str] = []
    optimization_items: list[dict[str, str]] = []
    for item in review.get("image_actions") or []:
        action = str(item.get("action") or "review")
        url = str(item.get("output_url") or item.get("url") or "").strip()
        if action in ("translate", "redraw") and not item.get("output_url"):
            optimization_items.append({"action": action, "url": url, "note": str(item.get("note") or "")})
            continue
        if action == "keep" and url and url not in selected_images:
            selected_images.append(url)
    if len(selected_images) < 3:
        blockers.append("通过且去重后的商品图片少于 3 张")
    if optimization_items:
        blockers.append(f"仍有 {len(optimization_items)} 张图片需要翻译或重绘")

    package = [float(x or 0) for x in (review.get("package_cm") or [0, 0, 0])]
    if len(package) != 3 or any(x <= 0 for x in package):
        blockers.append("商品尺寸不完整")
    weight = float(review.get("weight_kg") or 0)
    if weight <= 0:
        blockers.append("商品重量不完整")
    title = _normalize_title(str(review.get("title") or "").strip())
    if not title:
        blockers.append("英文标题为空")

    description = "<p>" + title + "</p>" + "".join(
        f'<p><img src="{url}" alt="Product detail" style="display:block;width:100%;height:auto;"/></p>'
        for url in selected_images
    )
    draft = {
        "commonCollectBoxDetailId": offer_id,
        "sourceItemId": source.get("source_id") or source.get("offer_id") or "",
        "title": title,
        "itemNum": seller_sku,
        "weight": weight,
        "packageLength": package[0] if len(package) == 3 else 0,
        "packageWidth": package[1] if len(package) == 3 else 0,
        "packageHeight": package[2] if len(package) == 3 else 0,
        "imgUrls": selected_images,
        "notes": description,
        "mainImgVideoUrl": source.get("video", {}).get("url") if review.get("video_action") == "keep" else "",
        "selectedSites": list(review.get("selected_sites") or []),
        "supportCod": True,
    }
    result = {
        "ok": True,
        "ready": not blockers,
        "mode": "miaoshou_second_review_preparation_no_write",
        "offer_id": offer_id,
        "draft": draft,
        "blockers": blockers,
        "optimization_items": optimization_items,
        "written_to_miaoshou": False,
        "updated_at": _now(),
    }
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / f"{offer_id}_miaoshou_draft.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if seller_sku and seller_sku != review.get("seller_sku"):
        state = load_state(offer_id)
        state.setdefault("review", {})["seller_sku"] = seller_sku
        save_state(offer_id, state)
    return result


def write_miaoshou_draft(offer_id_or_url: str, *, post=None) -> dict[str, Any]:
    """Write an approved draft to the common collect box, without claiming or publishing it."""
    prepared = prepare_miaoshou_draft(offer_id_or_url)
    if not prepared.get("ready"):
        raise RuntimeError("妙手草稿仍有阻塞项: " + "; ".join(prepared.get("blockers") or []))
    draft = prepared["draft"]
    detail_id = int(draft["commonCollectBoxDetailId"])
    if post is None:
        from modules.miaoshou.client import post_open

        post = post_open

    detail_path = "/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_detail"
    edit_path = "/open/v1/product/common_collect_box/common_collect_box/edit_common_collect_box_detail"
    current_resp = post(detail_path, {"commonCollectBoxDetailId": detail_id})
    if current_resp.get("result") != "success":
        raise RuntimeError(f"妙手详情读取失败: {current_resp.get('code')} {current_resp.get('message', '')}")
    data = current_resp.get("data") or {}
    current = data.get("editCommonCollectBoxDetail") or {}
    oss_md5 = str(data.get("ossMd5") or "")
    if not current or not oss_md5:
        raise RuntimeError("妙手详情缺少编辑数据或 ossMd5")

    updated = dict(current)
    for key in (
        "title", "itemNum", "weight", "packageLength", "packageWidth",
        "packageHeight", "imgUrls", "notes", "mainImgVideoUrl",
    ):
        updated[key] = draft[key]
    updated_skus = {}
    sku_numbers = _sequential_sku_numbers(current.get("skuMap") or {}, draft["itemNum"])
    for key, value in (current.get("skuMap") or {}).items():
        sku = dict(value)
        sku.update({
            "itemNum": sku_numbers[key],
            "weight": draft["weight"],
            "packageLength": draft["packageLength"],
            "packageWidth": draft["packageWidth"],
            "packageHeight": draft["packageHeight"],
        })
        updated_skus[key] = sku
    updated["skuMap"] = updated_skus

    save_resp = post(edit_path, {
        "commonCollectBoxDetailId": detail_id,
        "editCommonCollectBoxDetail": updated,
        "ossMd5": oss_md5,
    })
    if save_resp.get("result") != "success":
        raise RuntimeError(f"妙手草稿写入失败: {save_resp.get('code')} {save_resp.get('message', '')}")

    verify_resp = post(detail_path, {"commonCollectBoxDetailId": detail_id})
    if verify_resp.get("result") != "success":
        raise RuntimeError("妙手草稿写入后验证失败")
    verified = (verify_resp.get("data") or {}).get("editCommonCollectBoxDetail") or {}
    verified_sku_map = verified.get("skuMap") or {}
    verified_skus = list(verified_sku_map.values())
    checks = {
        "title": verified.get("title") == draft["title"],
        "seller_sku": str(verified.get("itemNum") or "") == draft["itemNum"],
        "weight": abs(float(verified.get("weight") or 0) - draft["weight"]) < 0.0001,
        "dimensions": [
            float(verified.get("packageLength") or 0),
            float(verified.get("packageWidth") or 0),
            float(verified.get("packageHeight") or 0),
        ] == [draft["packageLength"], draft["packageWidth"], draft["packageHeight"]],
        "images": list(verified.get("imgUrls") or []) == draft["imgUrls"],
        "description_images": str(verified.get("notes") or "").count("<img ") == len(draft["imgUrls"]),
        "video_action": not draft["mainImgVideoUrl"] and not verified.get("mainImgVideoUrl"),
        "sku_fields": bool(verified_skus) and all(
            str(sku.get("itemNum") or "") == sku_numbers.get(key)
            and abs(float(sku.get("weight") or 0) - draft["weight"]) < 0.0001
            for key, sku in verified_sku_map.items()
        ),
    }
    result = {
        **prepared,
        "written_to_miaoshou": True,
        "verified": all(checks.values()),
        "checks": checks,
        "sku_item_nums": list(sku_numbers.values()),
        "claimed": False,
        "published": False,
        "updated_at": _now(),
    }
    (STATE_DIR / f"{prepared['offer_id']}_miaoshou_draft.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def _miaoshou_post_retry(post, path: str, payload: dict[str, Any], action: str) -> dict[str, Any]:
    response: dict[str, Any] = {}
    for attempt in range(5):
        response = post(path, payload)
        if response.get("result") == "success":
            return response
        if response.get("code") != "platformQpsRateLimit":
            break
        time.sleep(2 + attempt * 2)
    raise RuntimeError(f"{action}失败: {response.get('code')} {response.get('message', '')}")


def _tiktok_collect_rows_for_source_item(post, source_item_id: str) -> list[dict[str, Any]]:
    response = _miaoshou_post_retry(
        post,
        "/open/v1/product/collect_box/tiktok/collect_box/search_collect_box_detail_list",
        {"pageNo": 1, "pageSize": 100, "filter": {"sourceItemIdKeyword": str(source_item_id)}},
        f"检索 TikTok 采集箱 {source_item_id}",
    )
    data = response.get("data") or {}
    return list(data.get("detailList") or data.get("list") or [])


def _shop_detail_map_from_collect_rows(
    rows: list[dict[str, Any]],
    *,
    common_detail_id: str,
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    filtered = [row for row in rows if str(row.get("commonCollectBoxDetailId") or "") == str(common_detail_id)]
    filtered.sort(key=lambda row: str(row.get("gmtCreate") or ""), reverse=True)
    shop_to_detail: dict[str, int] = {}
    normalized_rows: list[dict[str, Any]] = []
    for row in filtered:
        detail_id_raw = row.get("collectBoxDetailId") or row.get("detailId")
        if not detail_id_raw:
            continue
        detail_id = int(detail_id_raw)
        shop_ids: list[str] = []
        for shop_row in row.get("collectBoxDetailShopList") or []:
            shop_id = str(shop_row.get("shopId") or "").strip()
            if not shop_id:
                continue
            shop_ids.append(shop_id)
            shop_to_detail.setdefault(shop_id, detail_id)
        normalized_rows.append({
            "detail_id": detail_id,
            "common_detail_id": str(row.get("commonCollectBoxDetailId") or ""),
            "gmt_create": row.get("gmtCreate"),
            "item_num": row.get("itemNum"),
            "title": row.get("title"),
            "shop_ids": shop_ids,
        })
    return shop_to_detail, normalized_rows


def _resolve_shop_detail_id(
    post,
    *,
    common_detail_id: str,
    source_item_id: str,
    shop_id: str,
    fallback_detail_id: int,
    retry_claim: bool = False,
) -> tuple[int | None, list[dict[str, Any]]]:
    rows = _tiktok_collect_rows_for_source_item(post, source_item_id)
    shop_map, normalized_rows = _shop_detail_map_from_collect_rows(rows, common_detail_id=common_detail_id)
    detail_id = shop_map.get(str(shop_id))
    if detail_id:
        return int(detail_id), normalized_rows
    if retry_claim:
        _claim_detail_to_shops(post, int(fallback_detail_id), [str(shop_id)], f"补认领店铺 {shop_id}")
        time.sleep(1.0)
        rows = _tiktok_collect_rows_for_source_item(post, source_item_id)
        shop_map, normalized_rows = _shop_detail_map_from_collect_rows(rows, common_detail_id=common_detail_id)
        detail_id = shop_map.get(str(shop_id))
        if not detail_id:
            try:
                probe = _miaoshou_post_retry(
                    post,
                    "/open/v1/product/collect_box/tiktok/collect_box/get_shop_collect_item_info",
                    {"detailId": int(fallback_detail_id), "shopId": str(shop_id)},
                    f"探测店铺 {shop_id} detail",
                )
                if (probe.get("data") or {}).get("shopCollectItemInfo"):
                    detail_id = int(fallback_detail_id)
            except RuntimeError:
                pass
    return (int(detail_id) if detail_id else None), normalized_rows


def ensure_common_sequential_skus(offer_id_or_url: str, *, post=None) -> dict[str, Any]:
    """Assign sequential four-digit SKU numbers while preserving all other approved fields."""
    offer_id = resolve_offer_key(offer_id_or_url)
    if post is None:
        from modules.miaoshou.client import post_open

        post = post_open
    detail_path = "/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_detail"
    edit_path = "/open/v1/product/common_collect_box/common_collect_box/edit_common_collect_box_detail"
    response = _miaoshou_post_retry(
        post, detail_path, {"commonCollectBoxDetailId": int(offer_id)}, "读取妙手 SKU"
    )
    data = response.get("data") or {}
    current = data.get("editCommonCollectBoxDetail") or {}
    oss_md5 = str(data.get("ossMd5") or "")
    if not current or not oss_md5:
        raise RuntimeError("妙手详情缺少编辑数据或 ossMd5")
    base_sku = str(current.get("itemNum") or (load_state(offer_id).get("review") or {}).get("seller_sku") or "")[-4:]
    sku_map = current.get("skuMap") or {}
    sku_numbers = _sequential_sku_numbers(sku_map, base_sku)
    updated = dict(current)
    updated["itemNum"] = base_sku
    updated["skuMap"] = {
        key: {**value, "itemNum": sku_numbers[key]}
        for key, value in sku_map.items()
    }
    _miaoshou_post_retry(post, edit_path, {
        "commonCollectBoxDetailId": int(offer_id),
        "editCommonCollectBoxDetail": updated,
        "ossMd5": oss_md5,
    }, "保存连续 SKU 编号")
    verify_response = _miaoshou_post_retry(
        post, detail_path, {"commonCollectBoxDetailId": int(offer_id)}, "验证连续 SKU 编号"
    )
    verified = (verify_response.get("data") or {}).get("editCommonCollectBoxDetail") or {}
    verified_map = verified.get("skuMap") or {}
    checks = {
        "top_level": str(verified.get("itemNum") or "") == base_sku,
        "variants": bool(verified_map) and all(
            str(sku.get("itemNum") or "") == sku_numbers.get(key)
            for key, sku in verified_map.items()
        ),
    }
    draft_path = STATE_DIR / f"{offer_id}_miaoshou_draft.json"
    draft_state = _load_json(draft_path) or {}
    draft_state["sku_item_nums"] = list(sku_numbers.values())
    draft_state["sku_scheme_version"] = 2
    draft_state["updated_at"] = _now()
    draft_path.write_text(json.dumps(draft_state, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "offer_id": offer_id,
        "base_sku": base_sku,
        "sku_item_nums": list(sku_numbers.values()),
        "verified": all(checks.values()),
        "checks": checks,
    }


def sync_miaoshou_second_review(offer_id_or_url: str, *, post=None) -> dict[str, Any]:
    """Read the user's final Miaoshou edits back into the locked local snapshot."""
    offer_id = resolve_offer_key(offer_id_or_url)
    if post is None:
        from modules.miaoshou.client import post_open

        post = post_open
    detail_path = "/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_detail"
    response = _miaoshou_post_retry(
        post, detail_path, {"commonCollectBoxDetailId": int(offer_id)}, "回读妙手二审商品"
    )
    detail = (response.get("data") or {}).get("editCommonCollectBoxDetail") or {}
    if str(detail.get("commonCollectBoxDetailId") or offer_id) != offer_id:
        raise RuntimeError("妙手二审详情 ID 与当前商品不一致")
    images = list(dict.fromkeys(str(x).strip() for x in detail.get("imgUrls") or [] if str(x).strip()))
    package = [
        float(detail.get("packageLength") or 0),
        float(detail.get("packageWidth") or 0),
        float(detail.get("packageHeight") or 0),
    ]
    state = load_state(offer_id)
    review = state.setdefault("review", {})
    review.update({
        "title": str(detail.get("title") or review.get("title") or "").strip(),
        "seller_sku": str(detail.get("itemNum") or review.get("seller_sku") or "").strip()[-4:],
        "weight_kg": float(detail.get("weight") or 0),
        "package_cm": package,
        "video_action": "keep" if detail.get("mainImgVideoUrl") else "remove",
        "image_actions": [
            {"url": url, "kind": "miaoshou_final", "action": "keep", "note": "Approved in Miaoshou second review."}
            for url in images
        ],
        "fields_locked": True,
        "support_cod": True,
    })
    save_state(offer_id, state)

    draft_path = STATE_DIR / f"{offer_id}_miaoshou_draft.json"
    draft_state = _load_json(draft_path) or {}
    draft = draft_state.setdefault("draft", {})
    draft.update({
        "commonCollectBoxDetailId": offer_id,
        "title": review["title"],
        "itemNum": review["seller_sku"],
        "weight": review["weight_kg"],
        "packageLength": package[0],
        "packageWidth": package[1],
        "packageHeight": package[2],
        "imgUrls": images,
        "notes": str(detail.get("notes") or ""),
        "mainImgVideoUrl": str(detail.get("mainImgVideoUrl") or ""),
        "selectedSites": list(review.get("selected_sites") or []),
        "supportCod": True,
        "skuItemNums": [
            str(sku.get("itemNum") or "")
            for sku in (detail.get("skuMap") or {}).values()
        ],
    })
    draft_state.update({
        "ok": True,
        "ready": True,
        "written_to_miaoshou": True,
        "verified": True,
        "second_review_approved": True,
        "claimed": False,
        "published": False,
        "updated_at": _now(),
    })
    draft_path.write_text(json.dumps(draft_state, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "offer_id": offer_id,
        "title": review["title"],
        "seller_sku": review["seller_sku"],
        "weight_kg": review["weight_kg"],
        "package_cm": package,
        "image_count": len(images),
        "description_image_count": str(detail.get("notes") or "").count("<img"),
        "sku_count": len(detail.get("skuMap") or {}),
        "video_kept": bool(detail.get("mainImgVideoUrl")),
        "second_review_approved": True,
    }


def claim_miaoshou_to_tiktok(offer_id_or_url: str, *, post=None) -> dict[str, Any]:
    """Claim an approved common item to TikTok and available selected shops; never publish."""
    offer_id = resolve_offer_key(offer_id_or_url)
    claim_path = STATE_DIR / f"{offer_id}_tiktok_claim.json"
    lock = _tiktok_claim_lock(offer_id)
    if not lock.acquire(blocking=False):
        current = _load_json(claim_path) or {"ok": True, "offer_id": offer_id, "shops": {}, "blocked_sites": {}}
        current["in_progress"] = True
        current.setdefault("updated_at", _now())
        return current

    run_id = f"{int(time.time() * 1000)}-{threading.get_ident()}"
    try:
        if post is None:
            from modules.miaoshou.client import post_open

            post = post_open
        sync = sync_miaoshou_second_review(offer_id, post=post)
        sku_numbering = ensure_common_sequential_skus(offer_id, post=post)
        preview = build_preview(offer_id)
        source = preview.get("source") or {}
        source_item_id = (
            source.get("source_id")
            or ((source.get("precollect") or {}).get("records") or [{}])[0].get("source_id")
            or offer_id
        )
        state = load_state(offer_id)
        selected = list((state.get("review") or {}).get("selected_sites") or [])
        target_map = {row["id"]: row for row in SEA_MARKETS}
        existing = _load_json(claim_path) or {}
        tiktok_detail_id = existing.get("tiktok_detail_id")
        result = existing or {
            "ok": True,
            "offer_id": offer_id,
            "shops": {},
            "blocked_sites": {},
            "published": False,
        }
        result.update({
            "ok": True,
            "offer_id": offer_id,
            "claimed": False,
            "published": False,
            "in_progress": True,
            "current_run_id": run_id,
            "last_error": "",
            "started_at": _now(),
            "updated_at": _now(),
        })
        claim_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        shops: dict[str, Any] = dict(existing.get("shops") or {})
        blocked: dict[str, str] = {}
        claimable_targets: list[tuple[str, dict[str, Any], str]] = []
        for target_id in selected:
            target = target_map.get(target_id)
            if not target:
                blocked[target_id] = "unknown target site"
                continue
            shop_id = target.get("shop_id")
            if not shop_id:
                blocked[target_id] = f"shop not authorized in Miaoshou: {target['shop']} {target['region']}"
                continue
            claimable_targets.append((target_id, target, str(shop_id)))

        grouped_targets: dict[str, list[tuple[str, dict[str, Any], str]]] = {}
        for target_id, target, shop_id in claimable_targets:
            grouped_targets.setdefault(_anchor_group_key(target), []).append((target_id, target, shop_id))

        detail_group_detail_ids: dict[str, int] = {}
        detail_group_targets: dict[str, dict[str, Any]] = {}
        serial_number = 1
        for detail_group, group_targets in grouped_targets.items():
            group_detail_id = _claim_common_to_tiktok_detail(post, offer_id, serial_number=serial_number)
            serial_number += 1
            detail_group_detail_ids[detail_group] = int(group_detail_id)
            anchor_shop_ids = _claim_anchor_shop_ids(group_targets)
            detail_group_targets[detail_group] = {
                "detail_id": int(group_detail_id),
                "target_ids": [target_id for target_id, _target, _shop_id in group_targets],
                "shop_ids": [shop_id for _target_id, _target, shop_id in group_targets],
                "anchor_shop_ids": list(anchor_shop_ids),
            }
            _claim_detail_to_shops(
                post,
                int(group_detail_id),
                anchor_shop_ids,
                f"claim {detail_group} anchor shops",
            )
            time.sleep(0.5)

        tiktok_detail_id = int(tiktok_detail_id or next(iter(detail_group_detail_ids.values()), 0) or 0)

        detail_rows = _tiktok_collect_rows_for_source_item(post, str(source_item_id))
        shop_detail_ids, normalized_rows = _shop_detail_map_from_collect_rows(detail_rows, common_detail_id=offer_id)

        for target_id, target, shop_id in claimable_targets:
            existing_shop_state = dict(shops.get(target_id) or {})
            cached_warehouses = existing_shop_state.get("warehouses") or {}
            warehouse_response = {"data": cached_warehouses} if cached_warehouses else _miaoshou_post_retry(
                post,
                "/open/v1/product/collect_box/tiktok/collect_box/get_shop_warehouse_list",
                {"shopIds": [str(shop_id)]},
                f"read warehouse {target['shop']} {target['region']}",
            )
            detail_id_for_shop = shop_detail_ids.get(str(shop_id))
            shops[target_id] = {
                "shop_id": str(shop_id),
                "shop": target["shop"],
                "region": target["region"],
                "currency": target["currency"],
                "publish_group": _publish_group_for_target(target),
                "detail_group": _anchor_group_key(target),
                "detail_id": int(detail_group_detail_ids.get(_anchor_group_key(target), 0) or 0) or None,
                "title": None,
                "cid": None,
                "image_count": None,
                "sku_count": None,
                "weight": None,
                "package_cm": [None, None, None],
                "warehouses": warehouse_response.get("data"),
                "claimed": True,
            }
            region_detail_ids = {
                str(shop.get("region") or ""): int(shop.get("detail_id") or 0)
                for shop in shops.values()
                if shop.get("region") and shop.get("detail_id")
            }
            region_publish_groups = {
                str(shop.get("region") or ""): str(shop.get("publish_group") or _publish_group_for_target(shop))
                for shop in shops.values()
                if shop.get("region")
            }
            result.update({
                "tiktok_detail_id": tiktok_detail_id,
                "shop_detail_ids": {k: v.get("detail_id") for k, v in shops.items()},
                "detail_rows": normalized_rows,
                "publish_group_detail_ids": detail_group_detail_ids,
                "detail_group_detail_ids": detail_group_detail_ids,
                "detail_group_targets": detail_group_targets,
                "region_detail_ids": region_detail_ids,
                "region_publish_groups": region_publish_groups,
                "shops": shops,
                "blocked_sites": blocked,
                "claimed": False,
                "updated_at": _now(),
            })
            claim_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        result = {
            "ok": True,
            "offer_id": offer_id,
            "common_detail_id": int(offer_id),
            "tiktok_detail_id": tiktok_detail_id,
            "source_item_id": str(source_item_id),
            "second_review": sync,
            "sku_numbering": sku_numbering,
            "selected_sites": selected,
            "detail_rows": normalized_rows,
            "publish_group_detail_ids": detail_group_detail_ids,
            "detail_group_detail_ids": detail_group_detail_ids,
            "detail_group_targets": detail_group_targets,
            "region_detail_ids": {
                str(shop.get("region") or ""): int(shop.get("detail_id") or 0)
                for shop in shops.values()
                if shop.get("region") and shop.get("detail_id")
            },
            "region_publish_groups": {
                str(shop.get("region") or ""): str(shop.get("publish_group") or _publish_group_for_target(shop))
                for shop in shops.values()
                if shop.get("region")
            },
            "shop_detail_ids": {k: v.get("detail_id") for k, v in shops.items()},
            "shops": shops,
            "blocked_sites": blocked,
            "claimed": True,
            "published": False,
            "in_progress": False,
            "current_run_id": run_id,
            "last_error": "",
            "started_at": result.get("started_at") or _now(),
            "updated_at": _now(),
        }
        claim_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        draft_path = STATE_DIR / f"{offer_id}_miaoshou_draft.json"
        draft_state = _load_json(draft_path) or {}
        draft_state.update({"claimed": True, "tiktok_detail_id": tiktok_detail_id, "published": False, "updated_at": _now()})
        draft_path.write_text(json.dumps(draft_state, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    except Exception as exc:
        failed = _load_json(claim_path) or {"ok": False, "offer_id": offer_id, "shops": {}, "blocked_sites": {}}
        failed.update({
            "ok": False,
            "offer_id": offer_id,
            "claimed": False,
            "in_progress": False,
            "current_run_id": run_id,
            "last_error": str(exc),
            "updated_at": _now(),
        })
        claim_path.write_text(json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    finally:
        lock.release()


def start_claim_miaoshou_to_tiktok(offer_id_or_url: str) -> dict[str, Any]:
    """Start the TikTok-claim step in the background and return current state."""
    offer_id = resolve_offer_key(offer_id_or_url)
    claim_path = STATE_DIR / f"{offer_id}_tiktok_claim.json"
    current = _load_json(claim_path) or {
        "ok": True,
        "offer_id": offer_id,
        "shops": {},
        "blocked_sites": {},
        "published": False,
    }
    lock = _tiktok_claim_lock(offer_id)
    if lock.locked():
        current["in_progress"] = True
        current.setdefault("updated_at", _now())
        return current

    thread = threading.Thread(
        target=claim_miaoshou_to_tiktok,
        args=(offer_id,),
        kwargs={},
        daemon=True,
        name=f"np-claim-{offer_id}",
    )
    thread.start()
    current.update({
        "ok": True,
        "offer_id": offer_id,
        "claimed": bool(current.get("claimed")),
        "in_progress": True,
        "last_error": "",
        "updated_at": _now(),
    })
    claim_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def _preferred_warehouse_id(warehouse_data: dict[str, Any]) -> str:
    rows = []
    for shop_row in warehouse_data.get("shopWarehouseList") or []:
        rows.extend(shop_row.get("warehouseList") or [])
    active = [row for row in rows if str(row.get("warehouseEffectStatus") or "1") == "1"]
    if not active:
        return ""
    active.sort(key=lambda row: (
        str(row.get("isDefault") or "0") != "1",
        str(row.get("warehouseSubType") or "") != "3",
    ))
    return str(active[0].get("warehouseId") or "")


def _claim_detail_to_shops(post, detail_id: int, shop_ids: list[str], action: str) -> None:
    wanted = sorted({str(shop_id).strip() for shop_id in (shop_ids or []) if str(shop_id).strip()})
    if not wanted:
        return
    _miaoshou_post_retry(
        post,
        "/open/v1/product/collect_box/tiktok/collect_box/claim_to_shop",
        {"detailIds": [int(detail_id)], "shopIds": wanted},
        action,
    )


def _safe_claim_detail_to_shops(
    post,
    *,
    detail_id: int,
    preferred_shop_ids: list[str],
    fallback_shop_ids: list[str] | None = None,
    action: str,
) -> list[str]:
    preferred = sorted({str(shop_id).strip() for shop_id in (preferred_shop_ids or []) if str(shop_id).strip()})
    fallback = sorted({str(shop_id).strip() for shop_id in (fallback_shop_ids or []) if str(shop_id).strip()})
    try:
        _claim_detail_to_shops(post, detail_id, preferred, action)
        return preferred
    except RuntimeError as exc:
        message = str(exc)
        if (
            fallback
            and fallback != preferred
            and "同个全球店铺下只能选择一个子站点店铺" in message
        ):
            _claim_detail_to_shops(post, detail_id, fallback, f"{action} fallback")
            return fallback
        raise


def _claim_common_to_tiktok_detail(post, common_detail_id: str, serial_number: int = 1) -> int:
    response = _miaoshou_post_retry(
        post,
        "/open/v1/product/common_collect_box/common_collect_box/claimed",
        {"detailSerialNumberPlatformList": [{
            "detailId": int(common_detail_id), "platform": "tiktok", "serialNumber": int(serial_number),
        }]},
        "claim TikTok collect box",
    )
    platform_map = ((response.get("data") or {}).get("platformCollectBoxDetailIdMap") or {}).get("tiktok") or {}
    detail_id = platform_map.get(common_detail_id) or platform_map.get(int(common_detail_id))
    if not detail_id:
        raise RuntimeError("Miaoshou claim succeeded but did not return a TikTok collect-box detail id")
    return int(detail_id)


def _prepare_shop_mode_draft(
    post,
    *,
    detail_id: int,
    region: str,
    shop: dict[str, Any],
    pricing: dict[str, Any],
    draft: dict[str, Any],
    category_id: str,
    cod_enabled: bool = False,
    claim_shop_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    shop_id = str(shop["shop_id"])
    warehouse_id = _preferred_warehouse_id(shop.get("warehouses") or {})
    if not warehouse_id:
        raise RuntimeError(f"{shop.get('shop')} {region} 没有可用仓库")
    get_path = "/open/v1/product/collect_box/tiktok/collect_box/get_shop_collect_item_info"
    save_path = "/open/v1/product/collect_box/tiktok/collect_box/save_shop_collect_item_info"
    read_payload = {"detailId": detail_id, "shopId": str(shop_id)}
    try:
        read = _miaoshou_post_retry(post, get_path, read_payload, f"读取 {region} 店铺草稿")
    except RuntimeError as e:
        if "\u672a\u9009\u62e9\u9884\u53d1\u5e03\u5e97\u94fa" not in str(e):
            raise
        _claim_detail_to_shops(
            post,
            detail_id,
            claim_shop_ids or [str(shop_id)],
            f"重新认领 {region} 店铺",
        )
        read = _miaoshou_post_retry(post, get_path, read_payload, f"重读 {region} 店铺草稿")
    data = read.get("data") or {}
    info = dict(data.get("shopCollectItemInfo") or {})
    oss_md5 = str(data.get("ossMd5") or "")
    if not info or not oss_md5:
        raise RuntimeError(f"{region} 缺少店铺草稿或 ossMd5")
    list_price = float(pricing["list_price"])
    info.update({
        "title": draft.get("title") or info.get("title"),
        "notes": draft.get("notes") or info.get("notes") or "",
        "imgUrls": list(draft.get("imgUrls") or []),
        "weight": float(draft.get("weight") or 0),
        "packageLength": float(draft.get("packageLength") or 0),
        "packageWidth": float(draft.get("packageWidth") or 0),
        "packageHeight": float(draft.get("packageHeight") or 0),
        "cid": category_id,
        "isCodOpen": "1" if cod_enabled else "0",
        "mainImgVideoUrl": draft.get("mainImgVideoUrl") or "",
        "mainImgAppVideoId": "",
        "mainImgPlatformVideoId": "",
        "sizeChart": "",
        "sizeChartType": "",
        "brandId": "0",
        "brandName": "No Brand",
        "deliveryOptionSetType": info.get("deliveryOptionSetType") or "default",
        "deliveryOptionIds": info.get("deliveryOptionIds") or [],
        "manufacturerIds": info.get("manufacturerIds") or [],
        "responsiblePersonIds": info.get("responsiblePersonIds") or [],
        "productAttributes": [],
        "productCertifications": info.get("productCertifications") or [],
    })
    props = info.get("skuPropertyList") or []
    if props:
        props[0]["attrName"] = "Color"
        for value in props[0].get("attrValueList") or []:
            value_id = str(value.get("attrValueId") or "")
            if value_id == "87333b5fe4":
                value["attrValue"] = "Ivory Red"
            elif value_id == "a8fefa8b1f":
                value["attrValue"] = "Ivory Pink"
    sku_numbers = _sequential_sku_numbers(info.get("skuMap") or {}, draft.get("itemNum") or "")
    for sku_key, sku in (info.get("skuMap") or {}).items():
        stock = int(DEFAULT_LISTING_STOCK)
        sku.update({
            "price": list_price,
            "priceIncludeVat": list_price,
            "itemNum": sku_numbers[sku_key],
            "stock": stock,
            "weight": float(draft.get("weight") or 0),
            "packageLength": float(draft.get("packageLength") or 0),
            "packageWidth": float(draft.get("packageWidth") or 0),
            "packageHeight": float(draft.get("packageHeight") or 0),
            "shopIdToWarehouseIdAndStockMap": {str(shop_id): {warehouse_id: str(stock)}},
        })
    _miaoshou_post_retry(post, save_path, {
        "ossMd5": oss_md5,
        "detailId": detail_id,
        "shopId": str(shop_id),
        "shopCollectItemInfo": info,
    }, f"保存 {region} 店铺草稿")
    verify = _miaoshou_post_retry(
        post, get_path, {"detailId": detail_id, "shopId": str(shop_id)}, f"验证 {region} 店铺草稿"
    )
    verified = (verify.get("data") or {}).get("shopCollectItemInfo") or {}
    verified_claim_shop_ids = [str(x) for x in ((verify.get("data") or {}).get("claimToShopIds") or [])]
    verified_sku_map = verified.get("skuMap") or {}
    verified_skus = list(verified_sku_map.values())
    checks = {
        "category": str(verified.get("cid") or "") == category_id,
        "title": verified.get("title") == info["title"],
        "images": list(verified.get("imgUrls") or []) == info["imgUrls"],
        "description_images": str(verified.get("notes") or "").count("<img") == len(info["imgUrls"]),
        "package": [
            float(verified.get("packageLength") or 0),
            float(verified.get("packageWidth") or 0),
            float(verified.get("packageHeight") or 0),
        ] == [info["packageLength"], info["packageWidth"], info["packageHeight"]],
        "cod": str(verified.get("isCodOpen") or "0") == info["isCodOpen"],
        "sku_price": bool(verified_skus) and all(float(sku.get("price") or 0) == list_price for sku in verified_skus),
        "seller_sku": bool(verified_skus) and all(
            str(sku.get("itemNum") or "") == sku_numbers.get(key)
            for key, sku in verified_sku_map.items()
        ),
        "warehouse_stock": bool(verified_skus) and all(
            shop_id in (sku.get("shopIdToWarehouseIdAndStockMap") or {}) for sku in verified_skus
        ),
        "english_variants": _english_variant_checks_pass(verified),
    }
    return {
        "currency": pricing.get("currency"),
        "list_price": pricing.get("list_price"),
        "discount_price": pricing.get("discount_price"),
        "profit_margin_pct": pricing.get("profit_margin_on_sale_pct"),
        "shop_ids": [shop_id],
        "warehouse_ids": {shop_id: warehouse_id},
        "cod_enabled": cod_enabled,
        "mode": "shop",
        "sku_item_nums": list(sku_numbers.values()),
        "verified_claim_shop_ids": verified_claim_shop_ids,
        "sku_scheme_version": 2,
        "checks": checks,
        "ready": all(checks.values()),
    }


def _prepare_site_mode_draft(
    post,
    *,
    detail_id: int,
    region: str,
    region_targets: list[tuple[str, dict[str, Any], dict[str, Any]]],
    draft: dict[str, Any],
    category_id: str,
    cod_enabled: bool = False,
) -> dict[str, Any]:
    get_path = "/open/v1/product/collect_box/tiktok/collect_box/get_site_collect_item_info"
    save_path = "/open/v1/product/collect_box/tiktok/collect_box/save_site_collect_item_info"
    shop_get_path = "/open/v1/product/collect_box/tiktok/collect_box/get_shop_collect_item_info"
    data: dict[str, Any] = {}
    info: dict[str, Any] = {}
    oss_md5 = ""
    try:
        read = _miaoshou_post_retry(post, get_path, {"detailId": detail_id, "site": region}, f"读取 {region} 站点草稿")
        data = read.get("data") or {}
        info = dict(data.get("siteCollectItemInfo") or {})
        oss_md5 = str(data.get("ossMd5") or "")
    except RuntimeError:
        data = {}
        info = {}
        oss_md5 = ""

    anchor_target_id, anchor_shop, anchor_pricing = region_targets[0]
    anchor_shop_id = str(anchor_shop["shop_id"])
    if not info or not oss_md5:
        shop_read = _miaoshou_post_retry(
            post,
            shop_get_path,
            {"detailId": detail_id, "shopId": anchor_shop_id},
            f"读取 {region} 锚点店铺草稿",
        )
        shop_data = shop_read.get("data") or {}
        info = dict(shop_data.get("shopCollectItemInfo") or {})
        oss_md5 = str(shop_data.get("ossMd5") or "")
        data = {"claimToShopIds": shop_data.get("claimToShopIds") or []}
    if not info or not oss_md5:
        raise RuntimeError(f"{region} 缺少站点草稿或 ossMd5")

    primary_target_id, _primary_shop, primary_pricing = region_targets[0]
    list_price = float(primary_pricing["list_price"])
    shop_ids = [str(shop["shop_id"]) for _target_id, shop, _pricing in region_targets]
    warehouse_ids: dict[str, str] = {}
    for _target_id, shop, _pricing in region_targets:
        warehouse_id = _preferred_warehouse_id(shop.get("warehouses") or {})
        if not warehouse_id:
            raise RuntimeError(f"{shop.get('shop')} {region} 没有可用仓库")
        warehouse_ids[str(shop["shop_id"])] = warehouse_id

    info.update({
        "title": _normalize_title(draft.get("title") or info.get("title") or ""),
        "notes": draft.get("notes") or info.get("notes") or "",
        "imgUrls": list(draft.get("imgUrls") or []),
        "weight": float(draft.get("weight") or 0),
        "packageLength": float(draft.get("packageLength") or 0),
        "packageWidth": float(draft.get("packageWidth") or 0),
        "packageHeight": float(draft.get("packageHeight") or 0),
        "cid": category_id,
        "site": region,
        "editModel": "site",
        "isCodOpen": "1" if cod_enabled else "0",
        "mainImgVideoUrl": "",
        "mainImgAppVideoId": "",
        "mainImgPlatformVideoId": "",
        "sizeChart": "",
        "sizeChartType": "",
        "productAttributes": [],
        "productCertifications": info.get("productCertifications") or [],
        "manufacturerIds": info.get("manufacturerIds") or [],
        "responsiblePersonIds": info.get("responsiblePersonIds") or [],
        "deliveryOptionSetType": "default",
        "deliveryOptionIds": [],
    })

    existing_shop_rows = {
        str(row.get("shopId") or ""): dict(row)
        for row in (info.get("collectBoxDetailShopList") or [])
        if str(row.get("shopId") or "").strip()
    }
    default_shop_row = next(iter(existing_shop_rows.values()), {})
    info["collectBoxDetailShopList"] = [
        {
            "shopId": shop_id,
            "site": region,
            "brandId": str((existing_shop_rows.get(shop_id) or default_shop_row).get("brandId") or "0"),
            "brandName": str((existing_shop_rows.get(shop_id) or default_shop_row).get("brandName") or "No Brand"),
            "deliveryOptionSetType": str((existing_shop_rows.get(shop_id) or default_shop_row).get("deliveryOptionSetType") or info["deliveryOptionSetType"]),
            "deliveryOptionIds": list((existing_shop_rows.get(shop_id) or default_shop_row).get("deliveryOptionIds") or info.get("deliveryOptionIds") or []),
            "manufacturerIds": list((existing_shop_rows.get(shop_id) or default_shop_row).get("manufacturerIds") or []),
            "responsiblePersonIds": list((existing_shop_rows.get(shop_id) or default_shop_row).get("responsiblePersonIds") or []),
            "sizeChartTemplateId": str((existing_shop_rows.get(shop_id) or default_shop_row).get("sizeChartTemplateId") or ""),
        }
        for shop_id in shop_ids
    ]

    props = info.get("skuPropertyList") or []
    if props:
        props[0]["attrName"] = "Color"
        for value in props[0].get("attrValueList") or []:
            value_id = str(value.get("attrValueId") or "")
            if value_id == "87333b5fe4":
                value["attrValue"] = "Ivory Red"
            elif value_id == "a8fefa8b1f":
                value["attrValue"] = "Ivory Pink"

    sku_map = info.get("skuMap") or {}
    sku_numbers = _sequential_sku_numbers(sku_map, draft.get("itemNum") or "")
    sku_stock_totals = _distribute_total(DEFAULT_LISTING_STOCK, len(sku_map))
    shop_stock_template = {
        sku_key: _distribute_total(sku_stock_totals[index], len(shop_ids))
        for index, sku_key in enumerate(sku_map)
    }
    for index, (sku_key, sku) in enumerate(sku_map.items()):
        per_shop_allocations = shop_stock_template[sku_key]
        warehouse_map = {
            shop_id: {warehouse_ids[shop_id]: str(per_shop_allocations[shop_index])}
            for shop_index, shop_id in enumerate(shop_ids)
        }
        sku.update({
            "price": list_price,
            "priceIncludeVat": list_price,
            "itemNum": sku_numbers[sku_key],
            "stock": sum(per_shop_allocations),
            "weight": float(draft.get("weight") or 0),
            "packageLength": float(draft.get("packageLength") or 0),
            "packageWidth": float(draft.get("packageWidth") or 0),
            "packageHeight": float(draft.get("packageHeight") or 0),
            "shopIdToWarehouseIdAndStockMap": warehouse_map,
        })

    _miaoshou_post_retry(post, save_path, {
        "ossMd5": oss_md5,
        "detailId": detail_id,
        "site": region,
        "siteCollectItemInfo": info,
    }, f"保存 {region} 站点草稿")

    verify = _miaoshou_post_retry(post, get_path, {"detailId": detail_id, "site": region}, f"验证 {region} 站点草稿")
    verified_data = verify.get("data") or {}
    verified = verified_data.get("siteCollectItemInfo") or {}
    verified_sku_map = verified.get("skuMap") or {}
    verified_skus = list(verified_sku_map.values())
    verified_shop_rows = verified.get("collectBoxDetailShopList") or []
    expected_shop_ids = sorted(shop_ids)
    checks = {
        "category": str(verified.get("cid") or "") == category_id,
        "title": _normalize_title(verified.get("title") or "") == info["title"],
        "images": list(verified.get("imgUrls") or []) == info["imgUrls"],
        "description_images": str(verified.get("notes") or "").count("<img") == len(info["imgUrls"]),
        "package": [
            float(verified.get("packageLength") or 0),
            float(verified.get("packageWidth") or 0),
            float(verified.get("packageHeight") or 0),
        ] == [info["packageLength"], info["packageWidth"], info["packageHeight"]],
        "cod": str(verified.get("isCodOpen") or "0") == info["isCodOpen"],
        "sku_price": bool(verified_skus) and all(float(sku.get("price") or 0) == list_price for sku in verified_skus),
        "seller_sku": bool(verified_skus) and all(
            str(sku.get("itemNum") or "") == sku_numbers.get(key)
            for key, sku in verified_sku_map.items()
        ),
        "warehouse_stock": bool(verified_skus) and all(
            sorted((sku.get("shopIdToWarehouseIdAndStockMap") or {}).keys()) == expected_shop_ids
            for sku in verified_skus
        ),
        "site_shop_config": sorted(str(row.get("shopId") or "") for row in verified_shop_rows) == expected_shop_ids,
        "english_variants": _english_variant_checks_pass(verified),
    }
    return {
        "target_ids": [target_id for target_id, _shop, _pricing in region_targets],
        "currency": primary_pricing.get("currency"),
        "list_price": primary_pricing.get("list_price"),
        "discount_price": primary_pricing.get("discount_price"),
        "profit_margin_pct": primary_pricing.get("profit_margin_on_sale_pct"),
        "shop_ids": expected_shop_ids,
        "warehouse_ids": warehouse_ids,
        "cod_enabled": cod_enabled,
        "mode": "site",
        "sku_item_nums": list(sku_numbers.values()),
        "verified_claim_shop_ids": [str(x) for x in (verified_data.get("claimToShopIds") or [])],
        "site_collect_shop_ids": [str(row.get("shopId") or "") for row in verified_shop_rows],
        "sku_scheme_version": 3,
        "checks": checks,
        "ready": all(checks.values()),
        "detail_id": detail_id,
        "shop_names": [str(shop.get("shop") or "") for _target_id, shop, _pricing in region_targets],
    }


def _prepare_web_group_draft(
    *,
    detail_id: int,
    group_targets: list[tuple[str, dict[str, Any], dict[str, Any]]],
    draft: dict[str, Any],
    cod_enabled: bool = False,
    get_collect=None,
    save_collect=None,
) -> dict[str, Any]:
    if get_collect is None or save_collect is None:
        from modules.miaoshou.client import (
            web_get_collect_item_info as _web_get_collect_item_info,
            web_save_shop_collect_item_info as _web_save_shop_collect_item_info,
        )

        get_collect = get_collect or _web_get_collect_item_info
        save_collect = save_collect or _web_save_shop_collect_item_info

    payload = get_collect(detail_id)
    transformed = _web_collect_payload_for_targets(
        payload,
        selected_targets=group_targets,
        draft=draft,
        cod_enabled=cod_enabled,
    )
    info = transformed["shopCollectItemInfo"]
    save_collect(info)
    verified_payload = get_collect(detail_id)
    verified = verified_payload.get("shopCollectItemInfo") or {}
    anchor_shop_id = transformed["anchor_shop_id"]
    verified_related = ((verified.get("shopIdAndReplicatedProductsMap") or {}).get(anchor_shop_id) or [])
    verified_regions = sorted(
        {
            str(verified.get("site") or "")
        }
        | {
            str(row.get("site") or "")
            for row in verified_related
            if str(row.get("site") or "").strip()
        }
    )
    selected_regions = sorted({
        str(shop.get("region") or "")
        for _target_id, shop, _pricing in group_targets
        if str(shop.get("region") or "").strip()
    })
    selected_related_regions = sorted(set(transformed.get("selected_related_regions") or []))
    configured_regions = sorted({transformed["anchor_region"], *selected_related_regions})
    checks = {
        "title": _normalize_title(verified.get("title") or "") == info["title"],
        "images": list(verified.get("imgUrls") or []) == info["imgUrls"],
        "description_images": str(verified.get("notes") or "").count("<img") == len(info["imgUrls"]),
        "package": [
            float(verified.get("packageLength") or 0),
            float(verified.get("packageWidth") or 0),
            float(verified.get("packageHeight") or 0),
        ] == [info["packageLength"], info["packageWidth"], info["packageHeight"]],
        "cod": str(verified.get("isCodOpen") or "0") == info["isCodOpen"],
        "regions_configured": verified_regions == configured_regions,
    }
    return {
        "detail_id": int(detail_id),
        "anchor_shop_id": anchor_shop_id,
        "anchor_region": transformed["anchor_region"],
        "target_ids": [target_id for target_id, _shop, _pricing in group_targets],
        "selected_regions": selected_regions,
        "configured_regions": configured_regions,
        "missing_regions": [region for region in selected_regions if region not in configured_regions],
        "sku_item_nums": list(transformed.get("sku_item_nums") or []),
        "mode": "web_group",
        "checks": checks,
        "ready": all(checks.values()) and not [region for region in selected_regions if region not in configured_regions],
    }


def prepare_miaoshou_site_drafts(offer_id_or_url: str, *, post=None) -> dict[str, Any]:
    """Write verified shop drafts for claimed shops while grouping claims by publish group."""
    offer_id = resolve_offer_key(offer_id_or_url)
    output_path = STATE_DIR / f"{offer_id}_site_drafts.json"
    lock = _site_draft_lock(offer_id)
    if not lock.acquire(blocking=False):
        current = _load_json(output_path) or {
            "ok": True,
            "offer_id": offer_id,
            "sites": {},
            "blocked_sites": {},
            "published": False,
        }
        current["in_progress"] = True
        current["updated_at"] = _now()
        return current

    run_id = f"{int(time.time() * 1000)}-{threading.get_ident()}"
    try:
        if post is None:
            from modules.miaoshou.client import post_open

            post = post_open

        claim = _load_json(STATE_DIR / f"{offer_id}_tiktok_claim.json") or {}
        if not claim.get("claimed") or not (claim.get("shops") or claim.get("tiktok_detail_id")):
            raise RuntimeError("Product has not been claimed to TikTok yet")
        source_item_id = str(claim.get("source_item_id") or offer_id)
        draft_state = _load_json(STATE_DIR / f"{offer_id}_miaoshou_draft.json") or {}
        draft = draft_state.get("draft") or {}
        if not draft_state.get("second_review_approved"):
            raise RuntimeError("Miaoshou second review is not approved yet")

        preview = build_preview(offer_id)
        sea_rows = preview.get("pricing", {}).get("sea") or []
        price_by_region = {row["region"]: row for row in sea_rows}
        price_by_target = {row.get("id"): row for row in sea_rows if row.get("id")}
        price_by_region["MX"] = preview.get("pricing", {}).get("mx") or {}
        price_by_region["GB"] = preview.get("pricing", {}).get("uk") or {}

        shops = claim.get("shops") or {}
        grouped: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        detail_grouped: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for target_id, shop in shops.items():
            region = str(shop.get("region") or "").strip()
            detail_group = str(shop.get("detail_group") or _detail_group_for_target(shop))
            grouped.setdefault(region, []).append((target_id, shop))
            detail_grouped.setdefault(detail_group, []).append((target_id, shop))

        detail_id = int(claim.get("tiktok_detail_id") or 0)
        category_id = "853256"
        result = _load_json(output_path) or {
            "ok": True,
            "offer_id": offer_id,
            "tiktok_detail_id": detail_id,
            "sites": {},
            "publish_groups": {},
            "blocked_sites": claim.get("blocked_sites") or {},
            "published": False,
        }
        result.update({
            "ok": True,
            "offer_id": offer_id,
            "tiktok_detail_id": detail_id,
            "blocked_sites": claim.get("blocked_sites") or {},
            "published": False,
            "in_progress": True,
            "current_run_id": run_id,
            "last_error": "",
            "started_at": _now(),
            "updated_at": _now(),
        })
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        pending_grouped: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        prepared_targets: dict[str, dict[str, Any]] = {}
        publish_group_claims: dict[str, dict[str, Any]] = {}

        for region, region_shops in grouped.items():
            pending_grouped[region] = region_shops
            pricing = price_by_region.get(region) or {}
            for target_id, shop in region_shops:
                publish_group = _publish_group_for_target(shop)
                detail_group = str(shop.get("detail_group") or _detail_group_for_target(shop))
                group_claim = publish_group_claims.get(detail_group)
                if not group_claim:
                    publish_targets = detail_grouped.get(detail_group) or [(target_id, shop)]
                    primary_target_id, primary_shop = publish_targets[0]
                    primary_region = str(primary_shop.get("region") or region)
                    primary_pricing = price_by_target.get(primary_target_id) or price_by_region.get(primary_region) or pricing
                    if not primary_pricing.get("list_price"):
                        raise RuntimeError(f"{primary_target_id} missing reviewed list price")
                    group_detail_id = int((claim.get("detail_group_detail_ids") or claim.get("publish_group_detail_ids") or {}).get(detail_group) or 0)
                    detail_rows: list[dict[str, Any]] = []
                    if not group_detail_id:
                        group_detail_id, detail_rows = _resolve_shop_detail_id(
                            post,
                            common_detail_id=offer_id,
                            source_item_id=source_item_id,
                            shop_id=str(primary_shop["shop_id"]),
                            fallback_detail_id=detail_id,
                            retry_claim=True,
                        )
                    if not group_detail_id:
                        raise RuntimeError(f"{primary_target_id} missing TikTok detailId")
                    claim["detail_rows"] = detail_rows
                    preferred_group_shop_ids = _claim_all_shop_ids([
                        (group_target_id, group_shop, str(group_shop["shop_id"]))
                        for group_target_id, group_shop in publish_targets
                    ])
                    fallback_group_shop_ids = _claim_anchor_shop_ids([
                        (group_target_id, group_shop, str(group_shop["shop_id"]))
                        for group_target_id, group_shop in publish_targets
                    ])
                    group_shop_ids = _safe_claim_detail_to_shops(
                        post,
                        detail_id=int(group_detail_id),
                        preferred_shop_ids=preferred_group_shop_ids,
                        fallback_shop_ids=fallback_group_shop_ids,
                        action=f"sync {detail_group} detail-group shops",
                    )
                    time.sleep(0.8)
                    group_claim = {
                        "detail_id": int(group_detail_id),
                        "shop_ids": group_shop_ids,
                        "targets": [group_target_id for group_target_id, _group_shop in publish_targets],
                        "detail_group": detail_group,
                        "publish_group": publish_group,
                    }
                    publish_group_claims[detail_group] = group_claim

                shop_pricing = price_by_target.get(target_id) or pricing
                if not shop_pricing.get("list_price"):
                    raise RuntimeError(f"{target_id} missing reviewed list price")
                shop["detail_id"] = int(group_claim["detail_id"])
                prepared_targets[target_id] = {
                    "region": region,
                    "publish_group": publish_group,
                    "detail_group": detail_group,
                    "shop": shop,
                    "pricing": shop_pricing,
                    "detail_id": int(group_claim["detail_id"]),
                }

        result["publish_groups"] = {
            group: {
                "detail_id": info.get("detail_id"),
                "shop_ids": list(info.get("shop_ids") or []),
                "target_ids": list(info.get("targets") or []),
                "publish_group": info.get("publish_group"),
            }
            for group, info in publish_group_claims.items()
        }
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        for region, region_shops in pending_grouped.items():
            existing_site = (result.get("sites") or {}).get(region, {})
            expected_state = _expected_region_site_state(region, region_shops, prepared_targets)
            if _site_state_matches_expected(existing_site, expected_state):
                continue
            pricing = price_by_region.get(region) or {}
            if not pricing.get("list_price"):
                raise RuntimeError(f"{region} missing reviewed list price")

            if region in SEA_REGION_RULES:
                group_results: list[dict[str, Any]] = []
                merged_warehouse_ids: dict[str, str] = {}
                merged_shop_ids: list[str] = []
                region_groups: dict[tuple[int, str], list[tuple[str, dict[str, Any], dict[str, Any]]]] = {}

                for target_id, shop in region_shops:
                    prepared_target = prepared_targets[target_id]
                    region_groups.setdefault(
                        (int(prepared_target["detail_id"]), str(prepared_target["detail_group"])),
                        [],
                    ).append((target_id, shop, prepared_target["pricing"]))

                for (group_detail_id, detail_group), grouped_targets in region_groups.items():
                    group_result = _prepare_site_mode_draft(
                        post,
                        detail_id=group_detail_id,
                        region=region,
                        region_targets=grouped_targets,
                        draft=draft,
                        category_id=category_id,
                        cod_enabled=region in SEA_REGION_RULES,
                    )
                    group_result["detail_group"] = detail_group
                    group_result["publish_group"] = publish_group_claims.get(detail_group, {}).get("publish_group")
                    group_results.append(group_result)
                    merged_shop_ids.extend(group_result.get("shop_ids") or [])
                    merged_warehouse_ids.update(group_result.get("warehouse_ids") or {})

                all_check_keys = sorted({key for row in group_results for key in (row.get("checks") or {}).keys()})
                aggregate_checks = {
                    key: all((row.get("checks") or {}).get(key, False) for row in group_results)
                    for key in all_check_keys
                }
                list_prices = {row.get("list_price") for row in group_results}
                discount_prices = {row.get("discount_price") for row in group_results}
                margins = {row.get("profit_margin_pct") for row in group_results}
                result["sites"][region] = {
                    "currency": pricing.get("currency") or (group_results[0].get("currency") if group_results else None),
                    "list_price": next(iter(list_prices)) if len(list_prices) == 1 else None,
                    "discount_price": next(iter(discount_prices)) if len(discount_prices) == 1 else None,
                    "profit_margin_pct": next(iter(margins)) if len(margins) == 1 else None,
                    "shop_ids": sorted(set(merged_shop_ids)),
                    "warehouse_ids": merged_warehouse_ids,
                    "cod_enabled": all(bool(row.get("cod_enabled")) for row in group_results),
                    "mode": "site",
                    "sku_item_nums": group_results[0].get("sku_item_nums") if group_results else [],
                    "sku_scheme_version": 3,
                    "checks": aggregate_checks,
                    "shop_results": group_results,
                    "mixed_pricing": len(list_prices) > 1,
                    "ready": all(bool(row.get("ready")) for row in group_results),
                    "publish_group": [row.get("publish_group") for row in group_results],
                    "detail_group": [row.get("detail_group") for row in group_results],
                    "detail_ids": [row.get("detail_id") for row in group_results],
                    "site_collect_shop_ids": sorted({
                        shop_id
                        for row in group_results
                        for shop_id in ((row.get("site_collect_shop_ids") or row.get("verified_claim_shop_ids") or []))
                        if shop_id
                    }),
                }
                result.update({
                    "ok": True,
                    "ready": all(item.get("ready") for item in result["sites"].values()),
                    "published": False,
                    "updated_at": _now(),
                })
                output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                time.sleep(0.8)
                continue

            if region in {"MX", "GB"}:
                shop_results: list[dict[str, Any]] = []
                merged_warehouse_ids: dict[str, str] = {}
                merged_shop_ids: list[str] = []
                for target_id, shop in region_shops:
                    prepared_target = prepared_targets[target_id]
                    shop_result = _prepare_shop_mode_draft(
                        post,
                        detail_id=int(prepared_target["detail_id"]),
                        region=region,
                        shop=shop,
                        pricing=prepared_target["pricing"],
                        draft=draft,
                        category_id=category_id,
                        cod_enabled=False,
                        claim_shop_ids=[str(shop["shop_id"])],
                    )
                    shop_result["target_id"] = target_id
                    shop_result["shop_name"] = shop.get("shop")
                    shop_result["detail_id"] = int(prepared_target["detail_id"])
                    shop_result["detail_group"] = prepared_target.get("detail_group")
                    shop_result["publish_group"] = prepared_target.get("publish_group")
                    shop_results.append(shop_result)
                    merged_shop_ids.extend(shop_result.get("shop_ids") or [])
                    merged_warehouse_ids.update(shop_result.get("warehouse_ids") or {})

                all_check_keys = sorted({key for row in shop_results for key in (row.get("checks") or {}).keys()})
                aggregate_checks = {
                    key: all((row.get("checks") or {}).get(key, False) for row in shop_results)
                    for key in all_check_keys
                }
                result["sites"][region] = {
                    "currency": pricing.get("currency") or (shop_results[0].get("currency") if shop_results else None),
                    "list_price": shop_results[0].get("list_price") if shop_results else None,
                    "discount_price": shop_results[0].get("discount_price") if shop_results else None,
                    "profit_margin_pct": shop_results[0].get("profit_margin_pct") if shop_results else None,
                    "shop_ids": sorted(set(merged_shop_ids)),
                    "warehouse_ids": merged_warehouse_ids,
                    "cod_enabled": False,
                    "mode": "shop",
                    "sku_item_nums": shop_results[0].get("sku_item_nums") if shop_results else [],
                    "sku_scheme_version": 3,
                    "checks": aggregate_checks,
                    "shop_results": shop_results,
                    "mixed_pricing": False,
                    "ready": all(bool(row.get("ready")) for row in shop_results),
                    "publish_group": [row.get("publish_group") for row in shop_results],
                    "detail_group": [row.get("detail_group") for row in shop_results],
                    "detail_ids": [row.get("detail_id") for row in shop_results],
                    "site_collect_shop_ids": sorted({
                        shop_id
                        for row in shop_results
                        for shop_id in (row.get("verified_claim_shop_ids") or [])
                        if shop_id
                    }),
                }
                result.update({
                    "ok": True,
                    "ready": all(item.get("ready") for item in result["sites"].values()),
                    "published": False,
                    "updated_at": _now(),
                })
                output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                time.sleep(0.8)
                continue

            raise RuntimeError(f"Unsupported region {region}")

        result["in_progress"] = False
        result["updated_at"] = _now()
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    except Exception as exc:
        failed = _load_json(output_path) or {"ok": False, "offer_id": offer_id, "sites": {}}
        failed.update({
            "ok": False,
            "offer_id": offer_id,
            "in_progress": False,
            "current_run_id": run_id,
            "last_error": str(exc),
            "updated_at": _now(),
        })
        output_path.write_text(json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    finally:
        lock.release()
