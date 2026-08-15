"""New product listing workbench.
This module is intentionally light on model usage. It builds a structured
first-review payload from local scrape/preview files and only records requests
for expensive image/API work.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import html
import math
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from core.config import ROOT
from domains.content_operations.content_package_adapter import (
    SOURCE_ONLY_FINAL_APPROVAL_SCHEMA,
    source_only_final_approval_digest,
    source_only_final_approval_valid,
    source_only_review_signature,
)

from modules.sourcing.pipeline import load_scrape

WORKSPACE_ROOT = ROOT.parent.parent
OUTPUTS_DIR = WORKSPACE_ROOT / "outputs"
STATE_DIR = ROOT / "data" / "new_product_workbench"
IMAGE_SUITE_OUTPUTS_DIR = ROOT / "outputs" / "image_suite_from_miaoshou"
IMAGE_LOCALIZATION_DIR = ROOT / "data" / "image_localization"
LOCALIZED_IMAGE_PACKS_DIR = ROOT / "data" / "localized_image_packs"
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
MIN_ESTIMATED_PROFIT_CNY = 5.0
TIKTOK_CATEGORY_BY_PRODUCT_CATEGORY = {
    "贴饰 > 墙贴": "600338",
    "贴饰>墙贴": "600338",
    "墙贴": "600338",
    "wall sticker": "600338",
    "wall stickers": "600338",
}
_SITE_DRAFT_LOCKS: dict[str, threading.Lock] = {}
_TIKTOK_CLAIM_LOCKS: dict[str, threading.Lock] = {}
_IMAGE_GENERATION_LOCKS: dict[str, threading.Lock] = {}
_IMAGE_GENERATION_QUEUE_LOCKS: dict[str, threading.Lock] = {}
_MIAOSHOU_IMAGE_SYNC_LOCKS: dict[str, threading.Lock] = {}
_STATE_WRITE_LOCKS: dict[str, threading.RLock] = {}
_APPROVAL_BOUND_REVIEW_FIELDS = frozenset(
    {
        "title",
        "seller_sku",
        "category",
        "cost_cny",
        "weight_kg",
        "package_cm",
        "selected_sites",
        "selected_sku_keys",
        "sku_label_overrides",
        "support_cod",
        "fx_rates",
    }
)
EXPERIENCE_RECIPE_REVIEW_MODE = "experience_recipe_auto_v1"


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
        extra_rate=0.06,
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
        seller_tax_rate=0.15,
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


def _state_write_lock(offer_id: str) -> threading.RLock:
    lock = _STATE_WRITE_LOCKS.get(offer_id)
    if lock is None:
        lock = threading.RLock()
        _STATE_WRITE_LOCKS[offer_id] = lock
    return lock


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Replace one JSON file atomically so restarts cannot leave a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


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


def _image_generation_lock(offer_id: str) -> threading.Lock:
    lock = _IMAGE_GENERATION_LOCKS.get(offer_id)
    if lock is None:
        lock = threading.Lock()
        _IMAGE_GENERATION_LOCKS[offer_id] = lock
    return lock


def _image_generation_queue_lock(offer_id: str) -> threading.Lock:
    lock = _IMAGE_GENERATION_QUEUE_LOCKS.get(offer_id)
    if lock is None:
        lock = threading.Lock()
        _IMAGE_GENERATION_QUEUE_LOCKS[offer_id] = lock
    return lock


def _miaoshou_image_sync_lock(offer_id: str) -> threading.Lock:
    with _state_write_lock(offer_id):
        lock = _MIAOSHOU_IMAGE_SYNC_LOCKS.get(offer_id)
        if lock is None:
            lock = threading.Lock()
            _MIAOSHOU_IMAGE_SYNC_LOCKS[offer_id] = lock
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


def _canonical_variant_manifest(info: dict[str, Any]) -> tuple[tuple[Any, ...], ...]:
    """Return the exact English variant contract sent to and read from Miaoshou."""

    props = info.get("skuPropertyList")
    if props in (None, []):
        return ()
    if not isinstance(props, list):
        raise ValueError("TikTok specification properties must be a list")
    manifest: list[tuple[Any, ...]] = []
    for property_index, prop in enumerate(props, start=1):
        if not isinstance(prop, dict):
            raise ValueError(
                f"TikTok specification property {property_index} is malformed"
            )
        attr_name = " ".join(str(prop.get("attrName") or "").split())
        if not _is_english_variant_value(attr_name):
            raise ValueError(
                f"TikTok specification property {property_index} has no "
                "approved English name"
            )
        values = prop.get("attrValueList")
        if not isinstance(values, list):
            raise ValueError(
                f"TikTok specification property {property_index} values are malformed"
            )
        canonical_values: list[tuple[str, str]] = []
        for value_index, value in enumerate(values, start=1):
            if not isinstance(value, dict):
                raise ValueError(
                    f"TikTok specification property {property_index} value "
                    f"{value_index} is malformed"
                )
            value_id_raw = value.get("attrValueId")
            if isinstance(value_id_raw, bool):
                value_id = ""
            else:
                value_id = str(value_id_raw or "").strip()
            attr_value = " ".join(str(value.get("attrValue") or "").split())
            if not value_id:
                raise ValueError(
                    f"TikTok specification property {property_index} value "
                    f"{value_index} has no stable identity"
                )
            if not _is_english_variant_value(attr_value):
                raise ValueError(
                    f"TikTok specification property {property_index} value "
                    f"{value_index} has no approved English mapping"
                )
            canonical_values.append((value_id, attr_value))
        manifest.append((attr_name, tuple(canonical_values)))
    return tuple(manifest)


def _english_variant_checks_pass(
    verified: dict[str, Any],
    expected: dict[str, Any] | None = None,
) -> bool:
    try:
        actual_manifest = _canonical_variant_manifest(verified)
        expected_manifest = (
            _canonical_variant_manifest(expected)
            if isinstance(expected, dict)
            else actual_manifest
        )
    except (TypeError, ValueError):
        return False
    return actual_manifest == expected_manifest


def _audited_english_variant_value(text: str) -> str:
    """Translate only recognized size facts; unknown variants remain blocked."""

    value = str(text or "").strip()
    exact_labels = {
        "图片色": "As Shown",
    }
    if value in exact_labels:
        return exact_labels[value]
    if _is_english_variant_value(value):
        return value
    size_name = ""
    for marker, translated in (
        ("大号", "Large"),
        ("中号", "Medium"),
        ("小号", "Small"),
    ):
        if marker in value:
            size_name = translated
            break
    dimensions = re.search(
        r"(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+(?:\.\d+)?)\s*cm",
        value,
        re.IGNORECASE,
    )
    if not dimensions:
        return value
    width, height = dimensions.groups()
    dimension_label = f"{width} x {height} cm"
    return (
        f"{size_name} ({dimension_label})"
        if size_name
        else dimension_label
    )


def _apply_audited_english_variant_labels(
    info: dict[str, Any],
    label_overrides: dict[str, str] | None = None,
) -> None:
    props = info.get("skuPropertyList") or []
    if not props:
        if label_overrides:
            raise RuntimeError(
                "Approved specification names cannot be mapped to this channel draft"
            )
        return
    known_values = {
        "87333b5fe4": "Ivory Red",
        "a8fefa8b1f": "Ivory Pink",
    }
    original_variant_values = {
        id(value): str(value.get("attrValue") or "").strip()
        for prop in props
        if isinstance(prop, dict)
        for value in (prop.get("attrValueList") or [])
        if isinstance(value, dict)
    }
    for index, prop in enumerate(props):
        values = [
            value
            for value in (prop.get("attrValueList") or [])
            if isinstance(value, dict)
        ]
        has_dimensions = any(
            re.search(
                r"\d+(?:\.\d+)?\s*[xX×]\s*\d+(?:\.\d+)?\s*cm",
                str(row.get("attrValue") or ""),
                re.I,
            )
            for row in values
        )
        prop["attrName"] = (
            "Size" if has_dimensions else ("Color" if index == 0 else "Specification")
        )
        for value in values:
            value_id = str(value.get("attrValueId") or "")
            value["attrValue"] = known_values.get(
                value_id,
                _audited_english_variant_value(value.get("attrValue") or ""),
            )

    clean_overrides = {
        str(key).strip(): " ".join(str(label).split())
        for key, label in (label_overrides or {}).items()
        if str(key).strip() and " ".join(str(label).split())
    }
    if not clean_overrides:
        try:
            _canonical_variant_manifest(info)
        except ValueError as error:
            raise RuntimeError(str(error)) from error
        return
    target_prop = props[-1]
    target_values = [
        value
        for value in (target_prop.get("attrValueList") or [])
        if isinstance(value, dict)
    ]
    if not target_values:
        raise RuntimeError(
            "Approved specification names cannot be mapped to the final sale property"
        )
    by_source_component: dict[str, str] = {}
    for source_key, label in clean_overrides.items():
        components = [
            part.strip()
            for part in source_key.strip(";").split(";")
            if part.strip()
        ]
        if components:
            by_source_component[components[-1].casefold()] = label
    applied_labels: set[str] = set()
    for value in target_values:
        current_value = str(value.get("attrValue") or "").strip()
        original_value = original_variant_values.get(id(value), "")
        value_id = str(value.get("attrValueId") or "").strip()
        approved_label = (
            by_source_component.get(original_value.casefold())
            or
            by_source_component.get(current_value.casefold())
            or by_source_component.get(value_id.casefold())
        )
        if approved_label:
            value["attrValue"] = approved_label
            applied_labels.add(approved_label)
    if (
        len(clean_overrides) == 1
        and len(target_values) == 1
        and not applied_labels
    ):
        approved_label = next(iter(clean_overrides.values()))
        target_values[0]["attrValue"] = approved_label
        applied_labels.add(approved_label)
    missing_labels = set(clean_overrides.values()) - applied_labels
    if missing_labels:
        raise RuntimeError(
            "Approved specification names could not be mapped: "
            + ", ".join(sorted(missing_labels))
        )
    target_prop["attrName"] = "Specification"
    try:
        _canonical_variant_manifest(info)
    except ValueError as error:
        raise RuntimeError(str(error)) from error


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
    state = _load_json(_state_path(offer_id)) or {}
    state["_revision"] = max(0, int(state.get("_revision") or 0))
    return state


_CONTENT_REVIEW_FIELDS = (
    "image_actions",
    "generated_image_actions",
    "image_order",
    "overseas_image_candidates",
    "image_generation_requests",
    "video_action",
    "video_url",
)


def _mirror_content_state_to_collect_box_owner(
    offer_id: str,
    state: dict[str, Any],
) -> None:
    """Keep duplicate aliases from splitting one Miaoshou product's content state.

    A skipped duplicate has its own requested offer ID but writes to the resolved
    successful common-collect-box ID.  Content decisions and the verified write
    receipt therefore belong to that resolved owner.  Commercial/product review
    fields remain local and are deliberately not copied.
    """

    content = state.get("content_package")
    if not isinstance(content, dict) or not content:
        return
    owner_id = str(content.get("collect_box_id") or "").strip()
    if not owner_id.isdigit() or owner_id == offer_id:
        return

    with _state_write_lock(owner_id):
        owner = _load_json(_state_path(owner_id)) or {}
        owner_revision = max(0, int(owner.get("_revision") or 0))
        owner_review = (
            owner.get("review")
            if isinstance(owner.get("review"), dict)
            else {}
        )
        alias_review = (
            state.get("review")
            if isinstance(state.get("review"), dict)
            else {}
        )
        for field in _CONTENT_REVIEW_FIELDS:
            if field in alias_review:
                owner_review[field] = deepcopy(alias_review[field])
        owner["review"] = owner_review
        owner["content_package"] = deepcopy(content)
        owner["content_package"]["collect_box_id"] = owner_id
        owner["content_state_alias"] = {
            "requested_offer_id": offer_id,
            "resolved_collect_box_id": owner_id,
            "mirrored_at": _now(),
        }
        owner["offer_id"] = owner_id
        owner["updated_at"] = _now()
        owner["_revision"] = owner_revision + 1
        _write_json_atomic(_state_path(owner_id), owner)


def save_state(offer_id: str, state: dict[str, Any]) -> dict[str, Any]:
    with _state_write_lock(offer_id):
        current = _load_json(_state_path(offer_id)) or {}
        current_revision = max(0, int(current.get("_revision") or 0))
        expected_revision = max(0, int(state.get("_revision") if "_revision" in state else current_revision))
        if expected_revision != current_revision:
            raise RuntimeError("商品状态已被另一个操作更新，请刷新页面后重试")
        state["offer_id"] = offer_id
        state["updated_at"] = _now()
        state["_revision"] = current_revision + 1
        _write_json_atomic(_state_path(offer_id), state)
    _mirror_content_state_to_collect_box_owner(offer_id, state)
    return state


def _content_collect_box_id(offer_id: str, state: dict[str, Any], source: dict[str, Any] | None = None) -> str:
    """Find the collect-box ID that owns this product's image review package."""
    precollect = (source or {}).get("precollect") or {}
    if precollect.get("resolved_duplicate") is True:
        resolved = str(precollect.get("resolved_common_collect_id") or "").strip()
        if resolved.isdigit():
            return resolved
    saved = str((state.get("content_package") or {}).get("collect_box_id") or "").strip()
    if saved.isdigit():
        return saved
    for row in precollect.get("records") or []:
        candidate = str((row or {}).get("common_collect_id") or "").strip()
        if candidate.isdigit():
            return candidate
    return offer_id if offer_id.isdigit() else ""


def _content_package_dir(collect_box_id: str) -> Path | None:
    clean = str(collect_box_id or "").strip()
    if not clean.isdigit():
        return None
    path = IMAGE_SUITE_OUTPUTS_DIR / clean
    return path if path.is_dir() else None


def _image_localization_store():
    from modules.sourcing.image_localization import ImageLocalizationStore

    return ImageLocalizationStore(IMAGE_LOCALIZATION_DIR)


def _localized_image_pack_store():
    from modules.sourcing.localized_image_packs import LocalizedImagePackStore

    return LocalizedImagePackStore(LOCALIZED_IMAGE_PACKS_DIR)


def localized_image_project_summary(offer_id_or_url: str) -> dict[str, Any]:
    """Read the independent locale-image project without touching Product Center."""

    offer_id = resolve_offer_key(offer_id_or_url)
    project = _localized_image_pack_store().load(offer_id)
    for pack in (project.get("packs") or {}).values():
        if not isinstance(pack, dict):
            continue
        for image in pack.get("images") or []:
            if not isinstance(image, dict):
                continue
            preview = image.get("preview")
            artifact_id = str((preview or {}).get("artifact_id") or "")
            if isinstance(preview, dict) and artifact_id:
                preview["local_url"] = (
                    "/api/product-flow/content-package/localized-images/artifact"
                    f"?offer_id={urllib.parse.quote(offer_id)}"
                    f"&artifact_id={urllib.parse.quote(artifact_id)}"
                )
    return {
        "schema_version": "localized-image-project-summary/v1",
        "offer_id": offer_id,
        "initialized": bool(project),
        "project": project,
        "external_writes": 0,
        "product_center_mutated": False,
    }


def initialize_localized_image_project(
    offer_id_or_url: str, *, release_store=None
) -> dict[str, Any]:
    """Import the immutable approved master into an isolated locale project."""

    offer_id = resolve_offer_key(offer_id_or_url)
    if release_store is None:
        from shared_platform.release_store import ReleaseStore

        release_store = ReleaseStore(ROOT / "data" / "orbit_platform.db")
    plan = release_store.active_plan_for_product(offer_id)
    if (
        not isinstance(plan, dict)
        or plan.get("status") != "APPROVED"
        or str(plan.get("product_id") or "") != offer_id
    ):
        raise ValueError("an active approved ReleasePlan is required")
    plan_id = str(plan.get("plan_id") or "").strip()
    snapshot = release_store.approved_publication_snapshot(
        offer_id=offer_id, plan_id=plan_id
    )
    if not isinstance(snapshot, dict):
        raise ValueError("the approved ReleasePlan has no durable v4 snapshot")
    project = _localized_image_pack_store().initialize_from_approved_snapshot(
        snapshot
    )
    return {
        "schema_version": "localized-image-project-summary/v1",
        "offer_id": offer_id,
        "initialized": True,
        "project": project,
        "external_writes": 0,
        "product_center_mutated": False,
    }


def _localized_project_result(offer_id: str) -> dict[str, Any]:
    return localized_image_project_summary(offer_id)


def scan_localized_image_text(
    offer_id_or_url: str,
    *,
    expected_revision: object,
    source_url: str,
    source_bytes: bytes,
    ocr_engine=None,
) -> dict[str, Any]:
    """Run local RapidOCR for one approved image and seed translation drafts."""

    from modules.sourcing.localized_image_ocr import detect_english_text_regions

    offer_id = resolve_offer_key(offer_id_or_url)
    project = _localized_image_pack_store().load(offer_id)
    base_images = (project.get("packs") or {}).get("en-master", {}).get("images") or []
    matches = [
        row
        for row in base_images
        if isinstance(row, dict) and row.get("source_url") == source_url
    ]
    if len(matches) != 1:
        raise ValueError("localized image source is unavailable or ambiguous")
    regions = detect_english_text_regions(source_bytes, engine=ocr_engine)
    _localized_image_pack_store().save_text_inventory(
        offer_id,
        expected_revision=expected_revision,
        source_url=source_url,
        source_url_digest=matches[0].get("source_url_digest"),
        provider="rapidocr-local/v1",
        regions=regions,
    )
    return _localized_project_result(offer_id)


def save_localized_translation_draft(
    offer_id_or_url: str,
    *,
    expected_revision: object,
    locale: str,
    source_url: str,
    translations: list[dict[str, Any]],
) -> dict[str, Any]:
    offer_id = resolve_offer_key(offer_id_or_url)
    _localized_image_pack_store().save_translation_draft(
        offer_id,
        expected_revision=expected_revision,
        locale=locale,
        source_url=source_url,
        translations=translations,
    )
    return _localized_project_result(offer_id)


def auto_translate_localized_images(
    offer_id_or_url: str,
    *,
    expected_revision: object,
    source_bytes_by_url: dict[str, bytes],
    model_call=None,
) -> dict[str, Any]:
    """Automatically translate and render all locale packs without platform writes."""

    from modules.sourcing.localized_image_auto_translation import (
        translate_image_regions,
    )
    from modules.sourcing.localized_image_render import (
        RENDERER,
        render_translation_preview,
    )

    offer_id = resolve_offer_key(offer_id_or_url)
    store = _localized_image_pack_store()
    project = store.load(offer_id)
    if not project:
        raise ValueError("localized image project is missing")
    try:
        expected = int(expected_revision)
    except (TypeError, ValueError) as error:
        raise ValueError("localized image revision is invalid") from error
    if expected != int(project.get("revision") or 0):
        raise ValueError("localized image revision has changed")
    automatic = project.get("automatic_translation") or {}
    if (
        automatic.get("status") == "AUTO_PREVIEW_READY"
        and automatic.get("renderer") == RENDERER
    ):
        return _localized_project_result(offer_id)
    source_urls = list((project.get("base_package") or {}).get("ordered_image_urls") or [])
    inventory = (project.get("text_inventory") or {}).get("images") or {}
    if set(inventory) != set(source_urls):
        raise ValueError("all approved images must be locally scanned before translation")
    if set(source_bytes_by_url) != set(source_urls):
        raise ValueError("approved source image bytes are incomplete")

    items: list[dict[str, Any]] = []
    for source_url in source_urls:
        row = inventory.get(source_url)
        if not isinstance(row, dict) or row.get("status") != "SCANNED":
            raise ValueError("localized image text inventory is incomplete")
        regions = row.get("regions") or []
        reusable: dict[str, list[dict[str, Any]]] = {}
        reusable_receipt: dict[str, Any] | None = None
        for locale in ("ms-MY", "th-TH", "vi-VN", "ru-RU", "es-MX"):
            image_matches = [
                image
                for image in (
                    (project.get("packs") or {}).get(locale, {}).get("images") or []
                )
                if isinstance(image, dict) and image.get("source_url") == source_url
            ]
            if len(image_matches) != 1:
                reusable = {}
                break
            image = image_matches[0]
            rows = image.get("translations") or []
            if len(rows) != len(regions) or any(
                not str(row.get("translated_text") or "").strip()
                for row in rows
                if isinstance(row, dict)
            ):
                reusable = {}
                break
            reusable[locale] = [
                {
                    "region_id": row.get("region_id"),
                    "source_text": row.get("source_text"),
                    "translated_text": row.get("translated_text"),
                }
                for row in rows
                if isinstance(row, dict)
            ]
            reusable_receipt = image.get("automatic_translation_receipt")
        if len(reusable) == 5 and isinstance(reusable_receipt, dict):
            translated = {
                "translations": reusable,
                "receipt": reusable_receipt,
            }
        elif model_call is None:
            translated = translate_image_regions(regions)
        else:
            translated = translate_image_regions(regions, model_call=model_call)
        previews = {
            locale: render_translation_preview(
                source_bytes_by_url[source_url],
                regions=regions,
                translations=translations,
                locale=locale,
            )
            for locale, translations in translated["translations"].items()
            if regions
        }
        items.append(
            {
                "source_url": source_url,
                "translations": translated["translations"],
                "previews": previews,
                "receipt": translated["receipt"],
            }
        )
    store.save_automatic_bundle(
        offer_id,
        expected_revision=expected,
        items=items,
    )
    return _localized_project_result(offer_id)


def create_localized_translation_preview(
    offer_id_or_url: str,
    *,
    expected_revision: object,
    locale: str,
    source_url: str,
    source_bytes: bytes,
) -> dict[str, Any]:
    from modules.sourcing.localized_image_render import (
        RENDERER,
        render_translation_preview,
    )

    offer_id = resolve_offer_key(offer_id_or_url)
    store = _localized_image_pack_store()
    project = store.load(offer_id)
    inventory = (project.get("text_inventory") or {}).get("images") or {}
    text_row = inventory.get(source_url)
    if not isinstance(text_row, dict):
        raise ValueError("scan the approved image text before previewing translations")
    pack = (project.get("packs") or {}).get(locale)
    matches = [
        row
        for row in ((pack or {}).get("images") or [])
        if isinstance(row, dict) and row.get("source_url") == source_url
    ]
    if len(matches) != 1:
        raise ValueError("localized image source is unavailable or ambiguous")
    artifact = render_translation_preview(
        source_bytes,
        regions=text_row.get("regions") or [],
        translations=matches[0].get("translations") or [],
        locale=locale,
    )
    store.save_preview_artifact(
        offer_id,
        expected_revision=expected_revision,
        locale=locale,
        source_url=source_url,
        artifact_bytes=artifact,
        renderer=RENDERER,
    )
    return _localized_project_result(offer_id)


def localized_image_preview_artifact(
    offer_id_or_url: str, artifact_id: str
) -> Path:
    offer_id = resolve_offer_key(offer_id_or_url)
    return _localized_image_pack_store().preview_artifact_path(offer_id, artifact_id)


def _image_localization_source_rows(offer_id: str) -> list[dict[str, str]]:
    """Derive image identities from the current authoritative source snapshot."""
    state = load_state(offer_id)
    source = _source_summary(offer_id)
    collect_box_id = _content_collect_box_id(offer_id, state, source)
    package_dir = _content_package_dir(collect_box_id)
    review_package = _load_json(package_dir / "review_package.json") if package_dir else {}
    collect_box = (
        review_package.get("collect_box")
        if isinstance(review_package.get("collect_box"), dict)
        else {}
    )
    urls = _identity_reference_image_urls(source, collect_box)
    review = state.get("review") if isinstance(state.get("review"), dict) else {}
    reviewed_actions = {
        str(row.get("output_url") or row.get("url") or "").strip(): str(
            row.get("action") or ""
        ).strip()
        for row in (review.get("image_actions") or [])
        if isinstance(row, dict)
        and str(row.get("output_url") or row.get("url") or "").strip()
    }
    if reviewed_actions:
        urls = [url for url in urls if reviewed_actions.get(url) == "keep"]
    source_kinds = {
        str(row.get("url") or "").strip(): str(row.get("kind") or "main")
        for row in (source.get("images") or [])
        if isinstance(row, dict) and str(row.get("url") or "").strip()
    }
    return [
        {"url": url, "kind": source_kinds.get(url, "main")}
        for url in urls
    ]


def image_localization_summary(
    offer_id_or_url: str, *, resolved_offer_id: bool = False
) -> dict[str, Any]:
    from modules.sourcing.image_localization import image_localization_feature_flags

    offer_id = (
        str(offer_id_or_url)
        if resolved_offer_id
        else resolve_offer_key(offer_id_or_url)
    )
    features = image_localization_feature_flags()
    manifest = _image_localization_store().load(offer_id)
    for asset in manifest.get("assets") or []:
        clean = asset.get("clean_master") if isinstance(asset.get("clean_master"), dict) else {}
        artifact_id = str(clean.get("artifact_id") or "")
        if artifact_id:
            clean["local_url"] = (
                "/api/product-flow/content-package/image-localization/artifact"
                f"?offer_id={urllib.parse.quote(offer_id)}"
                f"&artifact_id={urllib.parse.quote(artifact_id)}"
            )
    return {
        "enabled": bool(features["manifest_enabled"]),
        "features": features,
        "manifest": manifest,
        "initialized": bool(manifest),
        "blockers": (
            []
            if features["manifest_enabled"]
            else ["image localization manifest is disabled"]
        ),
    }


def initialize_image_localization(offer_id_or_url: str) -> dict[str, Any]:
    offer_id = resolve_offer_key(offer_id_or_url)
    sources = _image_localization_source_rows(offer_id)
    manifest = _image_localization_store().initialize(offer_id, sources)
    return {
        **image_localization_summary(offer_id, resolved_offer_id=True),
        "manifest": manifest,
    }


def save_image_localization_regions(
    offer_id_or_url: str,
    *,
    expected_revision: object,
    asset_id: object,
    regions: list[dict[str, Any]],
) -> dict[str, Any]:
    offer_id = resolve_offer_key(offer_id_or_url)
    manifest = _image_localization_store().save_regions(
        offer_id,
        expected_revision=expected_revision,
        asset_id=asset_id,
        regions=regions,
    )
    return {
        **image_localization_summary(offer_id, resolved_offer_id=True),
        "manifest": manifest,
    }


def create_image_clean_master(
    offer_id_or_url: str,
    *,
    expected_revision: object,
    asset_id: object,
    source_bytes: bytes,
    method: str = "local_region_fill/v1",
) -> dict[str, Any]:
    offer_id = resolve_offer_key(offer_id_or_url)
    if not source_bytes:
        raise ValueError("source image bytes are required")
    temp_dir = IMAGE_LOCALIZATION_DIR / offer_id / ".incoming"
    temp_dir.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="source-", suffix=".image", dir=temp_dir)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(source_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        manifest = _image_localization_store().create_clean_master(
            offer_id,
            expected_revision=expected_revision,
            asset_id=asset_id,
            source_path=Path(temp_name),
            method=method,
        )
    finally:
        Path(temp_name).unlink(missing_ok=True)
    return {
        **image_localization_summary(offer_id, resolved_offer_id=True),
        "manifest": manifest,
    }


def image_localization_artifact(offer_id_or_url: str, artifact_id: str) -> Path:
    offer_id = resolve_offer_key(offer_id_or_url)
    return _image_localization_store().artifact_path(offer_id, artifact_id)


def _identity_reference_image_urls(
    source: dict[str, Any],
    collect_box: dict[str, Any],
) -> list[str]:
    """Return the image identities exposed by the current source review."""

    source_urls = _dedupe_urls(
        [
            str(row.get("url") or "").strip()
            for row in (source.get("images") or [])
            if isinstance(row, dict) and isinstance(row.get("url"), str)
        ]
    )
    if source_urls:
        return source_urls
    return _dedupe_urls(
        [
            str(url).strip()
            for url in (collect_box.get("image_urls") or [])
            if isinstance(url, str)
        ]
    )


def _content_artifacts(package_dir: Path | None, decisions: dict[str, Any]) -> list[dict[str, Any]]:
    if package_dir is None:
        return []
    rows: list[dict[str, Any]] = []
    audit_paths = []
    legacy = package_dir / "generation_audit.json"
    if legacy.is_file():
        audit_paths.append(legacy)
    audit_paths.extend(sorted(package_dir.glob("generation_audit_*.json")))
    for path in audit_paths:
        audit = _load_json(path) or {}
        artifact_id = "wb1" if path.name == "generation_audit.json" else path.stem.removeprefix("generation_audit_")
        image_file = package_dir / "generated" / f"{artifact_id}.png"
        decision = decisions.get(artifact_id) or {}
        rows.append({
            "id": artifact_id,
            "shot_id": str(audit.get("shot_id") or artifact_id.split("_", 1)[0]),
            "task_id": str(audit.get("task_id") or ""),
            "technical_complete": bool(audit.get("download_verified")) and image_file.is_file(),
            "has_local_image": image_file.is_file(),
            "decision": str(decision.get("decision") or "pending"),
            "note": str(decision.get("note") or ""),
            "reviewed_at": str(decision.get("reviewed_at") or ""),
        })
    return rows


def _generated_review_images(offer_id: str, saved: dict[str, Any], package_dir: Path | None) -> list[dict[str, Any]]:
    """Expose verified generated images in Treasury's main image-review area.

    These cards intentionally do not inherit the old content-package approval
    decision. A generated image is visible after technical verification, while
    the operator independently decides whether that individual image belongs in
    Miaoshou's image list.
    """
    if package_dir is None:
        return []
    decisions = saved.get("generated_image_miaoshou_decisions")
    decisions = decisions if isinstance(decisions, dict) else {}
    legacy_write = saved.get("miaoshou_generated_images_write")
    legacy_write = legacy_write if isinstance(legacy_write, dict) else {}
    legacy_synced_urls = {
        str(url).strip()
        for url in (legacy_write.get("generated_image_urls") or [])
        if str(url).strip()
    }
    rows_by_shot: dict[str, dict[str, Any]] = {}
    version_counts: dict[str, int] = {}
    for audit_path in sorted(package_dir.glob("generation_audit_*.json")):
        artifact_id = audit_path.stem.removeprefix("generation_audit_")
        audit = _load_json(audit_path) or {}
        image_file = package_dir / "generated" / f"{artifact_id}.png"
        data = ((audit.get("final_response") or {}).get("result") or {}).get("data") or []
        remote_url = str((data[0] or {}).get("url") or "") if data and isinstance(data[0], dict) else ""
        if not bool(audit.get("download_verified")) or not image_file.is_file() or not remote_url.startswith("https://"):
            continue
        shot_id = str(audit.get("shot_id") or artifact_id.split("_", 1)[0])
        version_counts[shot_id] = version_counts.get(shot_id, 0) + 1
        saved_decision = decisions.get(artifact_id) if isinstance(decisions.get(artifact_id), dict) else {}
        if not saved_decision and remote_url in legacy_synced_urls:
            saved_decision = {"action": "keep", "status": "synced", "synced_at": str(legacy_write.get("finished_at") or "")}
        row = {
            "artifact_id": artifact_id,
            "shot_id": shot_id,
            "task_id": str(audit.get("task_id") or ""),
            "url": remote_url,
            "local_url": f"/api/new-product/content-image?offer_id={offer_id}&artifact_id={artifact_id}",
            "title": str(audit.get("shot_title") or artifact_id),
            "miaoshou_action": str(saved_decision.get("action") or "review"),
            "miaoshou_sync_status": str(saved_decision.get("status") or "not_synced"),
            "miaoshou_sync_at": str(saved_decision.get("synced_at") or ""),
            "miaoshou_error": str(saved_decision.get("error") or ""),
            "created_at": str(audit.get("created_at") or ""),
            "_mtime": audit_path.stat().st_mtime,
        }
        previous = rows_by_shot.get(shot_id)
        if previous is None or float(row["_mtime"]) >= float(previous["_mtime"]):
            rows_by_shot[shot_id] = row
    rows = []
    for row in rows_by_shot.values():
        row["version_count"] = version_counts.get(str(row.get("shot_id") or ""), 1)
        row.pop("_mtime", None)
        rows.append(row)
    return sorted(rows, key=lambda row: (str(row.get("shot_id") or ""), str(row.get("artifact_id") or "")))


CONTENT_STRATEGIES = {"source_only", "ai_assisted"}


def _content_strategy(content: dict[str, Any]) -> str:
    value = str(content.get("content_strategy") or "ai_assisted").strip()
    return value if value in CONTENT_STRATEGIES else "ai_assisted"


def _source_only_selection(review: dict[str, Any]) -> dict[str, Any]:
    rows = review.get("image_actions") if isinstance(review.get("image_actions"), list) else []
    kept_urls: list[str] = []
    blockers: list[str] = []
    if not rows:
        blockers.append("来源图必须逐张明确保留或移除")
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            blockers.append(f"来源图 {index} 必须明确保留或移除")
            continue
        action = str(row.get("action") or "review")
        url = str(row.get("output_url") or row.get("url") or "").strip()
        if action == "keep":
            if not url.startswith("https://"):
                blockers.append(f"来源图 {index} 已保留，但不是 HTTPS 图片")
            elif url not in kept_urls:
                kept_urls.append(url)
        elif action != "remove":
            blockers.append(f"来源图 {index} 必须明确保留或移除")
    if not kept_urls:
        blockers.append("来源方案至少需要保留 1 张 HTTPS 图片")

    raw_order = review.get("image_order")
    ordered_urls = [
        str(url).strip()
        for url in raw_order
        if str(url).strip()
    ] if isinstance(raw_order, list) else []
    if not ordered_urls:
        blockers.append("来源方案必须保存最终图片顺序")
    if len(ordered_urls) != len(set(ordered_urls)):
        blockers.append("最终图片顺序不能包含重复 URL")
    unknown = [url for url in ordered_urls if url not in kept_urls]
    if unknown:
        blockers.append("最终图片顺序只能包含已保留的 HTTPS 来源图")
    missing = [url for url in kept_urls if url not in ordered_urls]
    if missing:
        blockers.append("每张已保留来源图都必须进入最终图片顺序")
    return {
        "ready": bool(kept_urls) and not blockers,
        "kept_urls": kept_urls,
        "ordered_urls": ordered_urls,
        "blockers": blockers,
    }


def _source_only_review_signature(
    image_actions: list[dict[str, Any]], image_order: list[str]
) -> str:
    return source_only_review_signature(image_actions, image_order)


def _require_ai_assisted(content: dict[str, Any], action: str) -> None:
    if _content_strategy(content) != "ai_assisted":
        raise ValueError(
            f"{action} is disabled while content_strategy is source_only"
        )


def _enable_experience_recipe_review(content: dict[str, Any]) -> None:
    """Adopt storyboard plans as an operational recipe, not a human gate."""

    if _content_strategy(content) != "ai_assisted":
        return
    content["planning_review_mode"] = EXPERIENCE_RECIPE_REVIEW_MODE
    content["planning_scope_source"] = "content_operations_experience_recipe"


def _adopt_current_storyboard_recipe(
    content: dict[str, Any],
    review_package: dict[str, Any],
) -> None:
    """Auto-adopt only a current AI plan; generated assets remain unapproved."""

    if (
        _content_strategy(content) != "ai_assisted"
        or str(content.get("planning_review_mode") or "")
        != EXPERIENCE_RECIPE_REVIEW_MODE
    ):
        return
    if not bool(content.get("fact_card_approved")):
        content["suite_approved"] = False
        content["storyboard_reviews"] = {}
        content.pop("storyboard_recipe_adopted_at", None)
        content.pop("storyboard_recipe_signature", None)
        return
    proposal = (
        review_package.get("model_proposal")
        if isinstance(review_package.get("model_proposal"), dict)
        else {}
    )
    current_signature = _planning_recipe_signature(content)
    proposal_valid = bool(
        proposal
        and str(proposal.get("planning_source") or "") == "ai"
        and str(proposal.get("planning_signature") or "") == current_signature
    )
    plan = (
        review_package.get("plan")
        if isinstance(review_package.get("plan"), dict)
        else {}
    )
    suite = plan.get("suite") if isinstance(plan.get("suite"), dict) else {}
    selected_ids = [
        str(item.get("id") or "").strip()
        for item in (suite.get("items") or [])
        if isinstance(item, dict)
        and bool(item.get("selected", True))
        and str(item.get("id") or "").strip()
    ]
    adopted = bool(proposal_valid and selected_ids)
    content["suite_approved"] = adopted
    content["storyboard_reviews"] = {
        shot_id: {
            "decision": "auto_adopted",
            "note": "Automatically adopted from the current experience recipe.",
            "reviewed_at": _now(),
            "review_source": EXPERIENCE_RECIPE_REVIEW_MODE,
        }
        for shot_id in dict.fromkeys(selected_ids)
    } if adopted else {}
    if adopted:
        content["storyboard_recipe_adopted_at"] = _now()
        content["storyboard_recipe_signature"] = current_signature
    else:
        content.pop("storyboard_recipe_adopted_at", None)
        content.pop("storyboard_recipe_signature", None)


def _content_stage(
    content: dict[str, Any],
    artifacts: list[dict[str, Any]],
    *,
    ai_plan_valid: bool = False,
    source_only_ready: bool = False,
    completed_ai_suite: bool = False,
) -> str:
    if not content.get("fact_card_approved"):
        return "待审核事实卡"
    if not content.get("planning_scope_approved"):
        return (
            "待确认来源素材范围"
            if _content_strategy(content) == "source_only"
            else "待确认本地生图约束"
        )
    if _content_strategy(content) == "source_only":
        return "来源素材内容已完成" if source_only_ready else "待完成来源图审核与排序"
    if completed_ai_suite:
        write = content.get("miaoshou_ordered_images_write") or {}
        return (
            "内容素材已审核并完成妙手回读验证"
            if str(write.get("status") or "") == "verified"
            else "内容素材可进入妙手写回审核"
        )
    if not ai_plan_valid:
        return "待 AI 生成分镜"
    if not content.get("suite_approved"):
        return "待审核 AI 分镜"
    if artifacts and not any(row.get("decision") == "approved" for row in artifacts):
        return "待审核生成素材"
    if any(row.get("decision") == "approved" for row in artifacts):
        return "内容素材可进入妙手写回审核"
    return "等待生成或导入素材"


def _contains_cjk(value: str) -> bool:
    return bool(re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", value or ""))


def _english_title_ready(value: str) -> bool:
    title = str(value or "").strip()
    return bool(title and re.search(r"[A-Za-z]", title) and not _contains_cjk(title))


def _requested_image_count(content: dict[str, Any]) -> int:
    if _content_strategy(content) == "source_only":
        return 0
    customization = content.get("suite_customization") if isinstance(content.get("suite_customization"), dict) else {}
    counts = customization.get("type_counts") if isinstance(customization.get("type_counts"), dict) else {}
    if counts:
        return sum(max(0, int(value or 0)) for value in counts.values())
    return sum(1 for row in ((content.get("suite") or {}).get("items") or []) if row.get("selected"))


def _completed_ai_suite_evidence(
    review: dict[str, Any],
    content: dict[str, Any],
    generated: list[dict[str, Any]],
) -> bool:
    """Recognize completed legacy suites without weakening paid-generation gates."""
    generation = (
        content.get("remaining_images_generation")
        if isinstance(content.get("remaining_images_generation"), dict)
        else {}
    )
    source_actions = [
        row for row in (review.get("image_actions") or []) if isinstance(row, dict)
    ]
    pending_source = any(
        str(row.get("action") or "review") not in {"keep", "remove"}
        for row in source_actions
    )
    pending_generated = any(
        str(row.get("miaoshou_action") or "review") not in {"keep", "remove"}
        for row in generated
    )
    kept_count = sum(
        str(row.get("action") or "") == "keep" for row in source_actions
    ) + sum(
        str(row.get("miaoshou_action") or "") == "keep" for row in generated
    )
    return bool(
        content.get("suite_approved")
        and str(generation.get("status") or "")
        in {"completed_waiting_human_review", "completed_with_errors"}
        and generated
        and not pending_source
        and not pending_generated
        and kept_count >= 3
        and [
            value
            for value in (review.get("image_order") or [])
            if str(value or "").strip()
        ]
    )


def _ai_assisted_final_review_ready(
    review: dict[str, Any],
    content: dict[str, Any],
    generated: list[dict[str, Any]],
) -> bool:
    """Return whether the exact current AI-assisted final set is review-complete.

    This deliberately ignores the earlier storyboard-adoption flag.  Once every
    current generated artifact and every source image has an explicit final
    decision, and the retained URLs have one exact saved order, the user can
    explicitly approve that final set without regenerating a proposal.
    """

    if not (
        _content_strategy(content) == "ai_assisted"
        and content.get("fact_card_approved")
        and content.get("planning_scope_approved")
        and generated
    ):
        return False
    source_actions = [
        row for row in (review.get("image_actions") or []) if isinstance(row, dict)
    ]
    if not source_actions or any(
        str(row.get("action") or "review") not in {"keep", "remove"}
        for row in source_actions
    ):
        return False
    if any(
        str(row.get("miaoshou_action") or "review") not in {"keep", "remove"}
        or str(row.get("miaoshou_sync_status") or "") != "reviewed_locally"
        for row in generated
    ):
        return False
    asset_decisions = (
        content.get("asset_decisions")
        if isinstance(content.get("asset_decisions"), dict)
        else {}
    )
    for row in generated:
        artifact_id = str(row.get("artifact_id") or "").strip()
        expected = (
            "approved"
            if str(row.get("miaoshou_action") or "") == "keep"
            else "rejected"
        )
        decision = asset_decisions.get(artifact_id)
        if not isinstance(decision, dict) or decision.get("decision") != expected:
            return False
    retained_urls = [
        str(row.get("url") or row.get("output_url") or "").strip()
        for row in source_actions
        if str(row.get("action") or "") == "keep"
    ] + [
        str(row.get("url") or "").strip()
        for row in generated
        if str(row.get("miaoshou_action") or "") == "keep"
    ]
    order = [
        str(value or "").strip()
        for value in (review.get("image_order") or [])
        if str(value or "").strip()
    ]
    return bool(
        len(retained_urls) >= 3
        and all(retained_urls)
        and len(retained_urls) == len(set(retained_urls))
        and len(order) == len(set(order))
        and set(order) == set(retained_urls)
        and str(review.get("video_action") or "none")
        in {"keep", "remove", "none"}
    )


def _verified_miaoshou_final_images(
    review: Mapping[str, Any], content: Mapping[str, Any]
) -> list[str]:
    """Return the current authoritative final image order, or fail closed."""
    write = content.get("miaoshou_ordered_images_write")
    if not isinstance(write, Mapping) or str(write.get("status") or "") != "verified":
        raise ValueError("Miaoshou final image read-back is unavailable")
    checks = write.get("checks") if isinstance(write.get("checks"), Mapping) else {}
    if not (checks.get("main_images_exact_order") and checks.get("detail_images_exact_order")):
        raise ValueError("Miaoshou final image read-back is not exact")
    urls = [str(url).strip() for url in (write.get("ordered_image_urls") or []) if str(url).strip()]
    order = [str(url).strip() for url in (review.get("image_order") or []) if str(url).strip()]
    if not urls or urls != order or len(urls) != int(write.get("written_image_count") or 0):
        raise ValueError("Miaoshou final image order drifted")
    if int(write.get("suite_revision") or 0) != max(1, int(content.get("suite_revision") or 1)):
        raise ValueError("Miaoshou final image revision drifted")
    if str(write.get("recipe_signature") or "") != _content_recipe_signature(dict(content)):
        raise ValueError("Miaoshou final image identity drifted")
    return urls


def _ai_assisted_final_approval_valid(
    review: dict[str, Any],
    content: dict[str, Any],
) -> bool:
    """Validate that final approval still binds the exact current review set."""

    approval = (
        content.get("final_content_approval")
        if isinstance(content.get("final_content_approval"), dict)
        else {}
    )
    if not (
        _content_strategy(content) == "ai_assisted"
        and content.get("suite_approved") is True
        and approval.get("schema_version")
        == "ai-assisted-final-content-approval/v1"
        and approval.get("status") == "approved"
        and approval.get("approved_by") == "Kyle"
        and str(approval.get("approved_at") or "").strip()
    ):
        return False
    try:
        miaoshou_ordered_image_urls = _verified_miaoshou_final_images(review, content)
    except ValueError:
        return False
    expected_payload = {
        "schema_version": "ai-assisted-final-content-approval/v1",
        "status": "approved",
        "approved_by": "Kyle",
        "image_order": list(review.get("image_order") or []),
        "miaoshou_ordered_image_urls": miaoshou_ordered_image_urls,
        "video_action": str(review.get("video_action") or "none"),
        "asset_decisions": content.get("asset_decisions") or {},
        "generated_image_decisions": (
            content.get("generated_image_miaoshou_decisions") or {}
        ),
    }
    expected_digest = hashlib.sha256(
        json.dumps(
            expected_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return bool(
        all(approval.get(key) == value for key, value in expected_payload.items())
        and approval.get("approval_digest") == expected_digest
    )


def _product_workflow_summary(
    *,
    source: dict[str, Any],
    review: dict[str, Any],
    content: dict[str, Any],
    miaoshou_draft: dict[str, Any],
    tiktok_claim: dict[str, Any],
    site_drafts: dict[str, Any],
) -> dict[str, Any]:
    """Build one canonical product-stage view shared by the API and workbench UI."""
    strategy = _content_strategy(content)
    source_only = strategy == "source_only"
    source_only_selection = _source_only_selection(review)
    source_only_final_approved = (
        source_only and source_only_final_approval_valid(content, review)
    )
    ai_final_approved = bool(
        not source_only
        and (
            content.get("final_content_approval_valid") is True
            or _ai_assisted_final_approval_valid(review, content)
        )
    )
    requested_images = _requested_image_count(content)
    generated = list(content.get("generated_review_images") or [])
    generation = content.get("remaining_images_generation") or {}
    generation_done = source_only or ai_final_approved or requested_images == 0 or (
        str(generation.get("status") or "") in {"completed_waiting_human_review", "completed_with_errors"}
        and len(generated) >= requested_images
    )
    source_actions = list(review.get("image_actions") or [])
    kept_source = [row for row in source_actions if str(row.get("action") or "review") == "keep"]
    kept_generated = [] if source_only else [
        row for row in generated
        if str(row.get("miaoshou_action") or "review") == "keep"
    ]
    pending_source = [row for row in source_actions if str(row.get("action") or "review") not in {"keep", "remove"}]
    pending_generated = [] if source_only else [
        row for row in generated
        if str(row.get("miaoshou_action") or "review") not in {"keep", "remove"}
    ]
    ai_plan_valid = bool((content.get("model_proposal") or {}).get("valid"))
    completed_ai_suite = bool(
        not source_only
        and _completed_ai_suite_evidence(review, content, generated)
    )
    if source_only:
        content_ready = bool(
            source_only_selection["ready"] and source_only_final_approved
        )
        image_review_ready = bool(source_only_selection["ready"])
    else:
        content_ready = bool(
            content.get("package_found")
            and content.get("fact_card_approved")
            and content.get("planning_scope_approved")
            and (ai_plan_valid or completed_ai_suite or ai_final_approved)
            and content.get("suite_approved")
        )
        image_review_ready = bool(
            ai_final_approved
            or (
                generation_done
                and not pending_source
                and not pending_generated
                and len(kept_source) + len(kept_generated) >= 3
            )
        )
    commercial_blockers = []
    if not _english_title_ready(str(review.get("title") or "")):
        commercial_blockers.append("英文标题必须包含英文字母且不能含中文")
    if float(review.get("weight_kg") or 0) <= 0:
        commercial_blockers.append("请确认商品重量")
    package = list(review.get("package_cm") or [])
    if len(package) != 3 or any(float(value or 0) <= 0 for value in package):
        commercial_blockers.append("请确认完整包装尺寸")
    if not review.get("selected_sites"):
        commercial_blockers.append("请至少选择一个目标站点")
    if float(review.get("cost_cny") or source.get("cost_cny") or 0) <= 0:
        commercial_blockers.append("请确认来源成本")
    source_ready = bool(
        str(review.get("title") or source.get("title_source") or "").strip()
        and (review.get("image_actions") or source.get("images"))
        and float(review.get("cost_cny") or source.get("cost_cny") or 0) > 0
    )

    steps = [
        {"id": "source", "label": "来源与规格", "status": "done" if source_ready else "attention"},
        {"id": "content", "label": "内容与配方", "status": "done" if content_ready else "current"},
        {
            "id": "generation",
            "label": "AI 生图（来源方案跳过）" if source_only else "整套生图",
            "status": "done" if generation_done else ("current" if content_ready else "pending"),
        },
        {"id": "images", "label": "图片审核", "status": "done" if image_review_ready else ("current" if generation_done else "pending")},
        {"id": "commercial", "label": "价格与发布信息", "status": "done" if review.get("fields_locked") and not commercial_blockers else ("current" if image_review_ready else "pending")},
        {"id": "miaoshou", "label": "应用到妙手", "status": "done" if miaoshou_draft.get("written_to_miaoshou") and miaoshou_draft.get("verified") else ("current" if review.get("fields_locked") else "pending")},
        {"id": "channels", "label": "站点草稿", "status": "done" if site_drafts.get("ready") else ("current" if tiktok_claim.get("claimed") else "pending")},
    ]
    current = next((row for row in steps if row["status"] in {"attention", "current"}), steps[-1])
    blockers = []
    if current["id"] == "source":
        if not str(review.get("title") or source.get("title_source") or "").strip():
            blockers.append("缺少商品标题")
        if not (review.get("image_actions") or source.get("images")):
            blockers.append("缺少商品来源图片")
        if float(review.get("cost_cny") or source.get("cost_cny") or 0) <= 0:
            blockers.append("缺少来源成本")
    elif current["id"] == "content":
        if not source_only and not content.get("package_found"):
            blockers = ["先创建本地内容审核包"]
        elif source_only:
            blockers.extend(source_only_selection["blockers"])
            if (
                source_only_selection["ready"]
                and not source_only_final_approved
            ):
                blockers.append(
                    "点击“保存并批准最终内容”完成来源图、顺序和视频决定的最终确认"
                )
        else:
            if not content.get("fact_card_approved"):
                blockers.append("审核商品身份与事实卡")
            if not content.get("planning_scope_approved"):
                blockers.append("确认图片类型、数量与本地类目约束")
            if content.get("planning_scope_approved") and not ai_plan_valid:
                blockers.append("调用 AI 生成当前配方的具体分镜")
            if ai_plan_valid and not content.get("suite_approved"):
                blockers.append("逐张审核并批准 AI 分镜")
    elif current["id"] == "generation":
        blockers = [f"生成并核验本次计划的 {requested_images} 张图片"]
    elif current["id"] == "images":
        if pending_source or pending_generated:
            blockers.append(f"{len(pending_source) + len(pending_generated)} image(s) still require an explicit keep or remove decision")
        if len(kept_source) + len(kept_generated) < (1 if source_only else 3):
            blockers.append("最终至少保留 1 张来源图" if source_only else "最终至少保留 3 张图片")
    elif current["id"] == "commercial":
        blockers = commercial_blockers
    return {
        "schema_version": "1.0.0",
        "content_strategy": strategy,
        "current_stage": current["id"],
        "current_label": current["label"],
        "steps": steps,
        "blockers": blockers,
        "content_required": True,
        "content_ready": content_ready,
        "generation_ready": generation_done,
        "image_review_ready": image_review_ready,
        "commercial_ready": not commercial_blockers,
        "requested_image_count": requested_images,
        "generated_image_count": len(generated),
        "kept_source_image_count": len(kept_source),
        "kept_generated_image_count": len(kept_generated),
        "pending_source_image_count": len(pending_source),
        "pending_generated_image_count": len(pending_generated),
    }


def content_package_summary(
    offer_id_or_url: str, *, resolved_offer_id: bool = False
) -> dict[str, Any]:
    """Return local image-review metadata without calling models, ToAPI, or Miaoshou."""
    offer_id = str(offer_id_or_url) if resolved_offer_id else resolve_offer_key(offer_id_or_url)
    state = load_state(offer_id)
    source = _source_summary(offer_id)
    saved = dict(state.get("content_package") or {})
    strategy = _content_strategy(saved)
    state_review = state.get("review") if isinstance(state.get("review"), dict) else {}
    source_only_selection = _source_only_selection(state_review)
    source_only_final_approved = (
        strategy == "source_only"
        and source_only_final_approval_valid(saved, state_review)
    )
    collect_box_id = _content_collect_box_id(offer_id, state, source)
    package_dir = _content_package_dir(collect_box_id)
    decisions = saved.get("asset_decisions") if isinstance(saved.get("asset_decisions"), dict) else {}
    artifacts = _content_artifacts(package_dir, decisions)
    generated_review_images = _generated_review_images(offer_id, saved, package_dir)
    report_ready = bool(package_dir and (package_dir / "review_report.html").is_file())
    review_package = _load_json(package_dir / "review_package.json") if package_dir else {}
    if package_dir and _apply_manual_storyboard_edits(saved, review_package):
        _write_json_atomic(package_dir / "review_package.json", review_package)
    collect_box = review_package.get("collect_box") if isinstance(review_package.get("collect_box"), dict) else {}
    fact_card = review_package.get("fact_card") if isinstance(review_package.get("fact_card"), dict) else {}
    plan = review_package.get("plan") if isinstance(review_package.get("plan"), dict) else {}
    suite = plan.get("suite") if isinstance(plan.get("suite"), dict) else {}
    plan_meta = plan.get("_meta") if isinstance(plan.get("_meta"), dict) else {}
    from modules.sourcing.image_shot_prompts import english_dimension_label

    final_overlay_labels: dict[str, str] = {}
    for item in suite.get("items") or []:
        if not isinstance(item, dict) or str(item.get("type") or "") != "size_card":
            continue
        try:
            final_overlay_labels[str(item.get("id") or "")] = english_dimension_label(
                str(item.get("human_dimensions") or "")
            )
        except ValueError:
            final_overlay_labels[str(item.get("id") or "")] = ""
    model_proposal = review_package.get("model_proposal") if isinstance(review_package.get("model_proposal"), dict) else {}
    proposal_usage = model_proposal.get("usage") if isinstance(model_proposal.get("usage"), dict) else {}
    planning_signature = _planning_recipe_signature(saved)
    model_proposal_valid = bool(
        model_proposal
        and str(model_proposal.get("planning_source") or "") == "ai"
        and str(model_proposal.get("planning_signature") or "") == planning_signature
    )
    completed_ai_suite = bool(
        strategy == "ai_assisted"
        and _completed_ai_suite_evidence(
            state_review,
            saved,
            generated_review_images,
        )
    )
    final_content_approval_valid = bool(
        strategy == "ai_assisted"
        and _ai_assisted_final_approval_valid(state_review, saved)
    )
    final_content_approval_ready = bool(
        strategy == "ai_assisted"
        and _ai_assisted_final_review_ready(
            state_review,
            saved,
            generated_review_images,
        )
    )
    storyboard_reviews = (
        saved.get("storyboard_reviews")
        if isinstance(saved.get("storyboard_reviews"), dict)
        else {}
    )
    planning_review_mode = str(saved.get("planning_review_mode") or "manual_legacy")
    automatic_storyboard_recipe = (
        strategy == "ai_assisted"
        and planning_review_mode == EXPERIENCE_RECIPE_REVIEW_MODE
    )
    preflight = saved.get("first_image_preflight") if isinstance(saved.get("first_image_preflight"), dict) else {}
    first_generation = saved.get("first_image_generation") if isinstance(saved.get("first_image_generation"), dict) else {}
    remaining_preflight = saved.get("remaining_images_preflight") if isinstance(saved.get("remaining_images_preflight"), dict) else {}
    remaining_generation = saved.get("remaining_images_generation") if isinstance(saved.get("remaining_images_generation"), dict) else {}
    remaining_generation_status = str(remaining_generation.get("status") or "not_started")
    worker_pid = int(remaining_generation.get("worker_pid") or 0)
    if remaining_generation_status in {"queued", "running"} and worker_pid and worker_pid != os.getpid():
        remaining_generation_status = "interrupted_retry_available"
    elif remaining_generation_status == "running" and not _image_generation_lock(offer_id).locked():
        remaining_generation_status = "interrupted_retry_available"
    miaoshou_write = (
        saved.get("miaoshou_ordered_images_write")
        if isinstance(saved.get("miaoshou_ordered_images_write"), dict)
        else saved.get("miaoshou_generated_images_write")
        if isinstance(saved.get("miaoshou_generated_images_write"), dict)
        else {}
    )
    available_identity_images = _identity_reference_image_urls(
        source,
        collect_box,
    )
    default_primary = str(collect_box.get("primary_identity_image") or "").strip()
    saved_refs = saved.get("identity_reference_urls") if isinstance(saved.get("identity_reference_urls"), list) else []
    identity_reference_urls = [str(url) for url in saved_refs if str(url) in available_identity_images]
    if (
        not identity_reference_urls
        and default_primary in available_identity_images
    ):
        identity_reference_urls = [default_primary]
    saved_primary = str(saved.get("primary_identity_url") or "").strip()
    primary_identity_url = saved_primary if saved_primary in identity_reference_urls else (identity_reference_urls[0] if identity_reference_urls else "")
    return {
        "schema_version": "1.0.0",
        "revision": max(0, int(state.get("_revision") or 0)),
        "content_strategy": strategy,
        "collect_box_id": collect_box_id,
        "package_found": package_dir is not None,
        "report_ready": report_ready,
        "fact_card_approved": bool(saved.get("fact_card_approved")),
        "planning_scope_approved": bool(saved.get("planning_scope_approved")),
        "suite_approved": bool(saved.get("suite_approved")),
        "planning_review_mode": planning_review_mode,
        "storyboard_human_approval_required": not automatic_storyboard_recipe,
        "generated_asset_human_approval_required": True,
        "content_approved": bool(
            source_only_final_approved
            if strategy == "source_only"
            else (
                saved.get("fact_card_approved")
                and saved.get("planning_scope_approved")
                and (
                    package_dir is not None
                    and (
                        (model_proposal_valid and saved.get("suite_approved"))
                        or completed_ai_suite
                        or final_content_approval_valid
                    )
                )
            )
        ),
        "final_content_approval_ready": final_content_approval_ready,
        "final_content_approval_valid": final_content_approval_valid,
        "completed_ai_suite_evidence": completed_ai_suite,
        "source_only_ready": bool(
            strategy == "source_only" and source_only_selection["ready"]
        ),
        "source_only_final_approved": bool(source_only_final_approved),
        "source_only_final_approval_digest": (
            str(
                (
                    saved.get("source_only_final_approval")
                    if isinstance(
                        saved.get("source_only_final_approval"), dict
                    )
                    else {}
                ).get("approval_digest")
                or ""
            )
            if source_only_final_approved
            else ""
        ),
        "source_only_blockers": (
            list(source_only_selection["blockers"])
            if strategy == "source_only"
            else []
        ),
        "artifacts": artifacts,
        "generated_review_images": generated_review_images,
        "source_snapshot": {
            "title": str(collect_box.get("source_title") or ""),
            "item_num": str(collect_box.get("item_num") or ""),
            "primary_identity_image": primary_identity_url,
            "image_urls": available_identity_images,
            "identity_reference_urls": identity_reference_urls,
            "image_count": int(collect_box.get("image_count") or 0),
        },
        "fact_card": {
            "verified": fact_card.get("verified") if isinstance(fact_card.get("verified"), list) else [],
            "inferred": fact_card.get("inferred") if isinstance(fact_card.get("inferred"), list) else [],
            "unknown_or_forbidden": fact_card.get("unknown_or_forbidden") if isinstance(fact_card.get("unknown_or_forbidden"), list) else [],
        },
        "suite": {
            "summary": str(suite.get("summary") or ""),
            "category_profile": str(plan_meta.get("category_profile") or ""),
            "items": [
                {
                    "id": str(item.get("id") or ""),
                    "type": str(item.get("type") or ""),
                    "title": str(item.get("title") or ""),
                    "focus": str(item.get("focus") or ""),
                    "title_zh": str(item.get("operator_title_zh") or ""),
                    "focus_zh": str(item.get("focus_zh") or ""),
                    "ai_planned": bool(item.get("ai_planned")),
                    "selected": bool(item.get("selected")),
                    "review_decision": str(
                        (storyboard_reviews.get(str(item.get("id") or "")) or {}).get("decision")
                        or (
                            "auto_adopted"
                            if automatic_storyboard_recipe
                            and model_proposal_valid
                            and bool(item.get("selected"))
                            else "pending"
                        )
                    ),
                    "review_note": str(
                        (storyboard_reviews.get(str(item.get("id") or "")) or {}).get("note")
                        or ""
                    ),
                    "model_base_contains_text": False if str(item.get("type") or "") == "size_card" else None,
                    "final_overlay_label": final_overlay_labels.get(str(item.get("id") or ""), ""),
                    "final_delivery_contains_numbers": bool(
                        str(item.get("type") or "") == "size_card"
                        and final_overlay_labels.get(str(item.get("id") or ""), "")
                    ),
                }
                for item in (suite.get("items") or [])
                if isinstance(item, dict)
            ],
        },
        "suite_customization": saved.get("suite_customization") if isinstance(saved.get("suite_customization"), dict) else {},
        "storyboard_reviews": storyboard_reviews,
        "pending_regeneration_shot_ids": [
            str(shot_id)
            for shot_id in (saved.get("pending_regeneration_shot_ids") or [])
            if str(shot_id)
        ],
        "suite_revision": max(1, int(saved.get("suite_revision") or 1)),
        "recipe_changed_at": str(saved.get("recipe_changed_at") or ""),
        "model_proposal": {
            "available": bool(model_proposal),
            "valid": model_proposal_valid,
            "status": (
                "auto_adopted_experience_recipe"
                if model_proposal_valid and automatic_storyboard_recipe
                else "completed_waiting_human_review"
                if model_proposal_valid
                else ("stale_recipe_changed" if model_proposal else "not_requested")
            ),
            "planning_source": str(model_proposal.get("planning_source") or ""),
            "planning_signature": str(model_proposal.get("planning_signature") or ""),
            "model": str(model_proposal.get("model") or ""),
            "reference_count": int(model_proposal.get("reference_count") or 0),
            "created_at": str(model_proposal.get("created_at") or ""),
            "total_tokens": int(proposal_usage.get("total_tokens") or 0),
            "revision_target_ids": [
                str(shot_id)
                for shot_id in (model_proposal.get("revision_target_ids") or [])
                if str(shot_id)
            ],
            "unchanged_item_ids": [
                str(shot_id)
                for shot_id in (model_proposal.get("unchanged_item_ids") or [])
                if str(shot_id)
            ],
            "accepted_shot_count": sum(1 for item in (suite.get("items") or []) if isinstance(item, dict) and item.get("selected")),
            "policy_rejections": list((model_proposal.get("policy") or {}).get("rejected_item_ids") or []),
            "candidate_items": [
                {
                    "id": str(item.get("id") or ""),
                    "type": str(item.get("type") or ""),
                    "title": str(item.get("title") or ""),
                    "focus": str(item.get("focus") or ""),
                    "title_zh": str(item.get("title_zh") or ""),
                    "focus_zh": str(item.get("focus_zh") or ""),
                    "aspect_ratio": str(item.get("aspect_ratio") or ""),
                }
                for item in (model_proposal.get("candidate_items") or [])
                if isinstance(item, dict)
            ],
        },
        "first_image_preflight": {
            "ready": bool(preflight),
            "status": str(preflight.get("status") or ""),
            "shot_id": str(preflight.get("shot_id") or ""),
            "title": str(preflight.get("title") or ""),
            "focus": str(preflight.get("focus") or ""),
            "aspect_ratio": str(preflight.get("aspect_ratio") or ""),
        "reference_count": int(preflight.get("reference_count") or 0),
        "reference_urls": [
            str(url) for url in ((preflight.get("payload") or {}).get("reference_images") or [])
            if str(url).startswith("https://")
        ],
        "model": str(preflight.get("model") or ""),
        "prompt_preview": str(((preflight.get("payload") or {}).get("prompt") or ""))[:4000],
        },
        "first_image_generation": {
            "status": str(first_generation.get("status") or "not_started"),
            "artifact_id": str(first_generation.get("artifact_id") or ""),
            "task_id": str(first_generation.get("task_id") or ""),
            "result_summary": str(first_generation.get("result_summary") or ""),
            "error": str(first_generation.get("error") or ""),
        },
        "remaining_images_preflight": {
            "ready": bool(remaining_preflight),
            "status": str(remaining_preflight.get("status") or ""),
            "full_suite": bool(remaining_preflight.get("full_suite")),
            "targeted_regeneration": bool(
                remaining_preflight.get("targeted_regeneration")
            ),
            "prepared_at": str(remaining_preflight.get("prepared_at") or ""),
            "suite_revision": int(remaining_preflight.get("suite_revision") or 0),
            "total": len(remaining_preflight.get("shots") or []),
            "shots": [
                {
                    "id": str(row.get("id") or ""),
                    "title": str(row.get("title") or ""),
                    "focus": str(row.get("focus") or ""),
                    "aspect_ratio": str(row.get("aspect_ratio") or ""),
                    "reference_count": int(row.get("reference_count") or 0),
                    "model": str(row.get("model") or ""),
                    "prompt_preview": str(((row.get("payload") or {}).get("prompt") or ""))[:4000],
                    "reference_urls": [
                        str(url) for url in ((row.get("payload") or {}).get("reference_images") or [])
                        if str(url).startswith("https://")
                    ],
                }
                for row in (remaining_preflight.get("shots") or [])
                if isinstance(row, dict)
            ],
        },
        "remaining_images_generation": {
            "status": remaining_generation_status,
            "started_at": str(remaining_generation.get("started_at") or ""),
            "finished_at": str(remaining_generation.get("finished_at") or ""),
            "current_shot_id": str(remaining_generation.get("current_shot_id") or ""),
            "items": [
                {
                    "shot_id": str(row.get("shot_id") or ""),
                    "artifact_id": str(row.get("artifact_id") or ""),
                    "status": str(row.get("status") or ""),
                    "task_id": str(row.get("task_id") or ""),
                    "result_summary": str(row.get("result_summary") or ""),
                }
                for row in (remaining_generation.get("items") or [])
                if isinstance(row, dict)
            ],
            "error": str(remaining_generation.get("error") or ""),
        },
        "miaoshou_generated_images_write": {
            "status": str(miaoshou_write.get("status") or "not_started"),
            "phase": str(miaoshou_write.get("phase") or ""),
            "started_at": str(miaoshou_write.get("started_at") or ""),
            "written_image_count": int(miaoshou_write.get("written_image_count") or 0),
            "finished_at": str(miaoshou_write.get("finished_at") or ""),
            "checks": miaoshou_write.get("checks") if isinstance(miaoshou_write.get("checks"), dict) else {},
            "steps": [
                {
                    "id": str(row.get("id") or ""),
                    "label": str(row.get("label") or ""),
                    "status": str(row.get("status") or "pending"),
                    "detail": str(row.get("detail") or ""),
                }
                for row in (miaoshou_write.get("steps") or [])
                if isinstance(row, dict)
            ],
            "ordered_image_count": len(miaoshou_write.get("ordered_image_urls") or []),
            "collect_box_id": str(miaoshou_write.get("collect_box_id") or collect_box_id),
            "error": str(miaoshou_write.get("error") or ""),
        },
        "approved_asset_count": sum(1 for row in artifacts if row.get("decision") == "approved"),
        "image_localization": image_localization_summary(
            offer_id, resolved_offer_id=True
        ),
        "stage": _content_stage(
            saved,
            artifacts,
            ai_plan_valid=model_proposal_valid,
            source_only_ready=source_only_selection["ready"],
            completed_ai_suite=completed_ai_suite,
        ),
        "updated_at": saved.get("updated_at") or "",
        "note": str(saved.get("note") or ""),
    }


def sync_content_package(offer_id_or_url: str, *, collect_box_id: str = "") -> dict[str, Any]:
    """Attach an existing local image-review package to a Treasury product case."""
    offer_id = resolve_offer_key(offer_id_or_url)
    state = load_state(offer_id)
    clean_id = str(collect_box_id or "").strip()
    if clean_id and not clean_id.isdigit():
        raise ValueError("collect_box_id must contain digits only")
    content = state.setdefault("content_package", {})
    if clean_id:
        content["collect_box_id"] = clean_id
    else:
        content["collect_box_id"] = _content_collect_box_id(offer_id, state, _source_summary(offer_id))
    content["linked_at"] = _now()
    save_state(offer_id, state)
    return content_package_summary(offer_id)


def prepare_content_package(offer_id_or_url: str, *, collect_box_id: str = "") -> dict[str, Any]:
    """Create a local, review-only package from Miaoshou; never generate or write images."""
    offer_id = resolve_offer_key(offer_id_or_url)
    state = load_state(offer_id)
    source = _source_summary(offer_id)
    clean_id = str(collect_box_id or _content_collect_box_id(offer_id, state, source)).strip()
    if not clean_id.isdigit():
        raise ValueError("collect_box_id must contain digits only")

    from modules.sourcing.image_review_package import create_package_from_miaoshou

    result = create_package_from_miaoshou(int(clean_id), IMAGE_SUITE_OUTPUTS_DIR / clean_id)
    content = state.setdefault("content_package", {})
    current_source_urls = set(_identity_reference_image_urls(source, {}))
    review = state.get("review") if isinstance(state.get("review"), dict) else {}
    reviewed_actions = {
        str(row.get("url") or "").strip(): str(row.get("action") or "")
        for row in (review.get("image_actions") or [])
        if isinstance(row, dict) and isinstance(row.get("url"), str)
    }
    preserved_refs = [
        url
        for url in _dedupe_urls(
            content.get("identity_reference_urls")
            if isinstance(content.get("identity_reference_urls"), list)
            else []
        )
        if (
            url in current_source_urls
            and reviewed_actions.get(url, "keep") == "keep"
        )
    ]
    saved_primary = str(content.get("primary_identity_url") or "").strip()
    content.setdefault("content_strategy", "ai_assisted")
    content["collect_box_id"] = clean_id
    content["prepared_at"] = _now()
    content["prepare_mode"] = "review_only_no_model_or_generation_call"
    content["fact_card_approved"] = False
    content["planning_scope_approved"] = False
    _enable_experience_recipe_review(content)
    content["suite_approved"] = False
    content["storyboard_reviews"] = {}
    content.pop("asset_decisions", None)
    content["identity_reference_urls"] = preserved_refs
    content["primary_identity_url"] = (
        saved_primary
        if saved_primary in preserved_refs
        else (preserved_refs[0] if preserved_refs else "")
    )
    save_state(offer_id, state)
    return {"preparation": result, "content_package": content_package_summary(offer_id)}


def propose_content_package_with_vision(
    offer_id_or_url: str,
    *,
    reference_urls: list[str],
    storyboard_feedback: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run mandatory AI storyboard planning inside locally validated constraints."""
    offer_id = resolve_offer_key(offer_id_or_url)
    state = load_state(offer_id)
    content = state.setdefault("content_package", {})
    _require_ai_assisted(content, "AI storyboard planning")
    _enable_experience_recipe_review(content)
    if not content.get("fact_card_approved"):
        raise ValueError("approve and save the fact card before requesting AI storyboard planning")
    if not content.get("planning_scope_approved"):
        raise ValueError("confirm and save the local category rules and image counts before requesting AI storyboard planning")
    saved_refs = [
        str(url) for url in (content.get("identity_reference_urls") or [])
        if str(url).startswith("https://")
    ]
    if not saved_refs:
        raise ValueError("save at least one approved identity reference before requesting AI storyboard planning")
    package_dir = _content_package_dir(str(content.get("collect_box_id") or ""))
    if package_dir is None:
        raise ValueError("create a local content review package before requesting a vision proposal")
    from modules.sourcing.image_review_package import create_model_suite_proposal

    requested_refs = [str(url) for url in reference_urls if str(url) in saved_refs]
    refs = requested_refs or saved_refs
    planning_signature = _planning_recipe_signature(content)
    review_package = _load_json(package_dir / "review_package.json") or {}
    current_items = {
        str(item.get("id") or ""): item
        for item in (((review_package.get("plan") or {}).get("suite") or {}).get("items") or [])
        if isinstance(item, dict)
    }
    ai_feedback: dict[str, str] = {}
    locally_satisfied_feedback: dict[str, str] = {}
    overlay_only_pattern = re.compile(
        r"^(?:图像中|图片中|最终图中)?\s*(?:需要|要|请)?\s*"
        r"(?:出现|有|显示|添加|加上)?\s*(?:尺寸)?\s*"
        r"(?:数字|尺寸数字|英文尺寸|尺寸标注)\s*[。！!]?$"
    )
    for shot_id, raw_note in (storyboard_feedback or {}).items():
        note = str(raw_note or "").strip()[:1200]
        if not note:
            continue
        item = current_items.get(str(shot_id)) or {}
        if str(item.get("type") or "") == "size_card" and overlay_only_pattern.fullmatch(note):
            locally_satisfied_feedback[str(shot_id)] = note
        else:
            ai_feedback[str(shot_id)] = note
    if locally_satisfied_feedback and not ai_feedback:
        from modules.sourcing.image_shot_prompts import english_dimension_label

        dimensions = str(
            ((content.get("suite_customization") or {}).get("size_card") or {}).get("dimensions")
            or ""
        )
        label = english_dimension_label(dimensions)
        return {
            "proposal": {
                "vision_model_called": False,
                "paid_generation_called": False,
                "local_feedback_satisfied": locally_satisfied_feedback,
                "final_overlay_label": label,
                "message": (
                    "该要求无需重新调用 AI。最终尺寸图会在无文字底图生成后，"
                    f"由本地程序自动添加已确认数字：{label}。"
                ),
            },
            "content_package": content_package_summary(offer_id),
        }
    revision_target_ids = sorted(ai_feedback)
    result = create_model_suite_proposal(
        package_dir,
        refs,
        suite_request=content.get("suite_customization") if isinstance(content.get("suite_customization"), dict) else {},
        planning_signature=planning_signature,
        storyboard_feedback=ai_feedback,
        revision_target_ids=revision_target_ids,
    )
    _invalidate_paid_image_state(content)
    if revision_target_ids:
        content.pop("force_regenerate_all", None)
        content["pending_regeneration_shot_ids"] = revision_target_ids
    else:
        content.pop("pending_regeneration_shot_ids", None)
    review_package = _load_json(package_dir / "review_package.json") or {}
    _adopt_current_storyboard_recipe(content, review_package)
    content["vision_proposal_at"] = _now()
    content["vision_proposal_signature"] = planning_signature
    save_state(offer_id, state)
    return {"proposal": result, "content_package": content_package_summary(offer_id)}


def _apply_suite_customization(
    suite: dict[str, Any], customization: dict[str, Any] | None, *, profile_id: str = ""
) -> dict[str, Any]:
    """Apply explicit operator changes to a category's suggested image suite."""
    result = deepcopy(suite) if isinstance(suite, dict) else {"summary": "", "items": []}
    base_items = [dict(row) for row in result.get("items") or [] if isinstance(row, dict)]
    custom = customization if isinstance(customization, dict) else {}
    type_counts = custom.get("type_counts") if isinstance(custom.get("type_counts"), dict) else {}
    size_card = custom.get("size_card") if isinstance(custom.get("size_card"), dict) else {}
    type_specs = {
        "white_bg": ("wb", "Clean White Product Hero", "Show the exact approved product on a clean white background."),
        "scene": ("sc", "Source-Supported Lifestyle Scene", "Choose a distinct, believable use setting from the approved source material without inventing features."),
        "selling_point": ("sp", "Source-Supported Selling Point", "Highlight one visible, source-supported product characteristic without text or invented claims."),
        "macro_detail": ("dt", "Source-Supported Detail", "Show a close product detail only when it is visibly supported by the source references."),
        "size_card": ("sz", "Operator-Confirmed Size Card", "Create a clean technical base for a later deterministic English size overlay."),
    }
    scene_variants = {
        "wall_decal": [
            ("Entryway Wall Application", "Show the exact decal applied to a clean entryway wall at believable scale."),
            ("Bedroom Wall Application", "Show the exact decal on a calm bedroom wall with restrained, relevant props."),
            ("Living Space Wall Application", "Show the exact decal on a bright living-space wall with realistic placement."),
            ("Hallway Accent Application", "Show the exact decal on a simple hallway wall without changing its artwork or proportions."),
            ("Children's Room Application", "Show the exact decal in a tidy children's room only when the source artwork suits that setting."),
            ("Home Office Application", "Show the exact decal on a clean home-office wall with realistic scale and placement."),
        ],
        "product_sticker": [
            ("Primary Product Application", "Show the exact sticker applied to the most source-supported compatible product surface."),
            ("Alternate Product Application", "Show the exact sticker on a second believable compatible surface without inventing durability claims."),
            ("Close Lifestyle Application", "Show the exact sticker in a tighter real-use composition with its artwork unchanged."),
            ("Outdoor Use Context", "Show the exact sticker in a believable outdoor-use context only when supported by the source product."),
            ("Indoor Storage Context", "Show the exact sticker on a clean personal-item surface in a realistic indoor setting."),
            ("Gift Personalization Context", "Show the exact sticker as a decorative accent without implying unsupported customization."),
        ],
    }
    items: list[dict[str, Any]] = []
    for shot_type, (prefix, fallback_title, fallback_focus) in type_specs.items():
        originals = [row for row in base_items if str(row.get("type") or "") == shot_type and bool(row.get("selected", True))]
        raw_count = type_counts.get(shot_type)
        if isinstance(raw_count, int) and not isinstance(raw_count, bool):
            desired_count = max(0, min(raw_count, 6))
        else:
            # Compatibility with the previous scene-only editor.
            legacy_scene_count = custom.get("scene_count") if shot_type == "scene" else None
            desired_count = max(0, min(legacy_scene_count, 6)) if isinstance(legacy_scene_count, int) else len(originals)
        if shot_type == "size_card" and bool(size_card.get("enabled")) and desired_count == 0:
            desired_count = 1
        if shot_type == "size_card":
            desired_count = min(desired_count, 1)
        for index in range(desired_count):
            row = dict(originals[index]) if index < len(originals) else {
                "id": f"{prefix}_custom_{index + 1:02d}",
                "type": shot_type,
                "title": f"{fallback_title} {index + 1}" if desired_count > 1 else fallback_title,
                "focus": fallback_focus,
                "focus_zh": "由 AI 根据已确认的商品身份参考图选择合规的构图变体。",
                "aspect_ratio": "1:1",
                "operator_added": True,
            }
            row["selected"] = True
            if shot_type == "scene" and index < len(scene_variants.get(profile_id, [])):
                row["title"], row["focus"] = scene_variants[profile_id][index]
                row["focus_zh"] = "系统按类目规划的不同场景变体；AI 仅执行该构图，不得改变商品身份。"
            if shot_type == "size_card":
                row.update({
                    "human_dimensions": str(size_card.get("dimensions") or "").strip()[:240],
                    "human_dimensions_confirmed": bool(size_card.get("confirmed")),
                    "human_override": True,
                })
            items.append(row)
    result["items"] = items
    return result


def _safe_image_execution_plan(
    review_package: dict[str, Any],
    *,
    suite_customization: dict[str, Any] | None = None,
    required_planning_signature: str = "",
) -> dict[str, Any]:
    """Build paid prompts from a locally validated AI storyboard."""
    plan = deepcopy(review_package.get("plan")) if isinstance(review_package.get("plan"), dict) else {}
    proposal = review_package.get("model_proposal") if isinstance(review_package.get("model_proposal"), dict) else {}
    if str(proposal.get("planning_source") or "") != "ai":
        raise ValueError("generate an AI storyboard before preparing paid image generation")
    if (
        required_planning_signature
        and str(proposal.get("planning_signature") or "") != required_planning_signature
    ):
        raise ValueError("the AI storyboard is stale because references or image counts changed; generate it again")
    fact_card = review_package.get("fact_card") if isinstance(review_package.get("fact_card"), dict) else {}
    profile_id = str((plan.get("_meta") or {}).get("category_profile") or "")
    material = ""
    for row in fact_card.get("verified") or []:
        if isinstance(row, dict) and str(row.get("field") or "") == "材质":
            material = str(row.get("value") or "")
            break
    product_facts = {
        "wall_decal": ("Flat decorative wall decal", "decorative wall decal", "flat printed wall decal artwork"),
        "product_sticker": ("Flat decorative product sticker", "decorative product sticker", "flat printed sticker artwork"),
    }
    subject, category, structure = product_facts.get(profile_id, ("Decorative product", "decorative product", "source-supported product form"))
    from modules.sourcing.image_suite_plan import enforce_category_policy

    # Older partial-storyboard revisions kept the approved English composition
    # but discarded the model's operator translations for untouched shots.
    # Hydrate only those missing review labels; never replace approved title,
    # focus, selection, or geometry used by paid generation.
    candidate_items = {
        str(row.get("id") or ""): row
        for row in proposal.get("candidate_items") or []
        if isinstance(row, dict)
    }
    for item in ((plan.get("suite") or {}).get("items") or []):
        if not isinstance(item, dict):
            continue
        candidate = candidate_items.get(str(item.get("id") or "")) or {}
        if not str(item.get("operator_title_zh") or "").strip():
            item["operator_title_zh"] = str(
                candidate.get("title_zh") or item.get("title") or ""
            ).strip()
        if not str(item.get("operator_focus_zh") or "").strip():
            item["operator_focus_zh"] = str(
                candidate.get("focus_zh")
                or item.get("focus_zh")
                or item.get("focus")
                or ""
            ).strip()

    locked_plan = enforce_category_policy(
        plan,
        title=str((review_package.get("collect_box") or {}).get("source_title") or ""),
        category=str((plan.get("analysis") or {}).get("category") or ""),
        suite_request=suite_customization if isinstance(suite_customization, dict) else {},
    )
    return {
        "analysis": {
            "subject": subject,
            "category": category,
            "theme": "source-supported printed graphic",
            "structure": structure,
            "style_lock": "Use the approved source reference images to preserve the exact graphic, colors, cut edge, flat form, and proportions. Do not recreate, translate, or replace visible artwork.",
            "materials": [material] if material else [],
            "colors": [],
            "craft_details": [],
            "brand_dna": [],
        },
        "suite": locked_plan.get("suite") if isinstance(locked_plan.get("suite"), dict) else {},
        "_meta": {
            "title": str((review_package.get("collect_box") or {}).get("source_title") or ""),
            "image_url": str((review_package.get("collect_box") or {}).get("primary_identity_image") or ""),
            "category_profile": profile_id,
            "plan_model": str(proposal.get("model") or ""),
            "planning_source": "ai",
        },
    }


def save_manual_storyboard_edits(
    offer_id_or_url: str, *, expected_revision: int, edits: Mapping[str, Any]
) -> dict[str, Any]:
    """Save operator copy overrides for existing AI shots; no generation occurs."""
    offer_id = parse_offer_id(offer_id_or_url)
    state = load_state(offer_id)
    if type(expected_revision) is not int or expected_revision != state["_revision"]:
        raise ValueError("storyboard is stale; refresh before saving manual edits")
    if not isinstance(edits, Mapping):
        raise ValueError("storyboard edits must be an object")
    content = state.setdefault("content_package", {})
    package_dir = _content_package_dir(str(content.get("collect_box_id") or ""))
    if package_dir is None:
        raise ValueError("content review package is unavailable")
    review_package = _load_json(package_dir / "review_package.json")
    plan = review_package.get("plan") if isinstance(review_package.get("plan"), dict) else {}
    suite = plan.get("suite") if isinstance(plan.get("suite"), dict) else {}
    items = [row for row in suite.get("items") or [] if isinstance(row, dict)]
    allowed = {str(row.get("id") or ""): row for row in items if str(row.get("id") or "")}
    if not edits or set(edits) - set(allowed):
        raise ValueError("storyboard edit shot IDs are invalid")
    saved: dict[str, dict[str, str]] = {}
    for shot_id, raw in edits.items():
        if not isinstance(raw, Mapping) or set(raw) != {"title", "focus"}:
            raise ValueError("storyboard edits may contain only title and focus")
        title, focus = (str(raw[key] or "").strip() for key in ("title", "focus"))
        if not title or not focus or len(title) > 240 or len(focus) > 1200:
            raise ValueError("storyboard edit title or focus is invalid")
        row = allowed[shot_id]
        row["title"] = title
        row["focus"] = focus
        row["operator_title_zh"] = title
        row["operator_focus_zh"] = focus
        saved[shot_id] = {"title": title, "focus": focus}
    _write_json_atomic(package_dir / "review_package.json", review_package)
    content["manual_storyboard_edits"] = saved
    content["manual_storyboard_saved_at"] = _now()
    _invalidate_paid_image_state(content)
    content.pop("remaining_images_preflight", None)
    save_state(offer_id, state)
    return content_package_summary(offer_id, resolved_offer_id=True)


def _apply_manual_storyboard_edits(
    content: Mapping[str, Any], review_package: dict[str, Any]
) -> bool:
    """Reapply validated operator copy without changing storyboard structure."""
    edits = content.get("manual_storyboard_edits")
    if not isinstance(edits, Mapping):
        return False
    plan = review_package.get("plan") if isinstance(review_package.get("plan"), dict) else {}
    suite = plan.get("suite") if isinstance(plan.get("suite"), dict) else {}
    items = {
        str(row.get("id") or ""): row
        for row in suite.get("items") or []
        if isinstance(row, dict) and str(row.get("id") or "")
    }
    if not edits or set(edits) - set(items):
        raise ValueError("saved storyboard edits no longer match the current AI storyboard")
    changed = False
    for shot_id, raw in edits.items():
        if not isinstance(raw, Mapping):
            raise ValueError("saved storyboard edits are invalid")
        title, focus = (str(raw.get(key) or "").strip() for key in ("title", "focus"))
        if not title or not focus:
            raise ValueError("saved storyboard edits are invalid")
        row = items[shot_id]
        values = {
            "title": title,
            "focus": focus,
            "operator_title_zh": title,
            "operator_focus_zh": focus,
        }
        if any(row.get(key) != value for key, value in values.items()):
            row.update(values)
            changed = True
    return changed


def _planning_recipe_signature(content: dict[str, Any]) -> str:
    """Fields that require a fresh AI storyboard when changed."""
    recipe = {
        "content_strategy": _content_strategy(content),
        "identity_reference_urls": list(content.get("identity_reference_urls") or []),
        "primary_identity_url": str(content.get("primary_identity_url") or ""),
        "suite_customization": content.get("suite_customization") or {},
    }
    return json.dumps(recipe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content_recipe_signature(content: dict[str, Any]) -> str:
    """Stable signature for every field that changes a paid generation payload."""
    recipe = {
        "content_strategy": _content_strategy(content),
        "fact_card_approved": bool(content.get("fact_card_approved")),
        "planning_scope_approved": bool(content.get("planning_scope_approved")),
        "suite_approved": bool(content.get("suite_approved")),
        "identity_reference_urls": list(content.get("identity_reference_urls") or []),
        "primary_identity_url": str(content.get("primary_identity_url") or ""),
        "suite_customization": content.get("suite_customization") or {},
    }
    return json.dumps(recipe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _invalidate_paid_image_state(content: dict[str, Any]) -> None:
    generation = content.get("remaining_images_generation")
    if isinstance(generation, dict) and str(generation.get("status") or "") in {"queued", "running"}:
        raise ValueError("cannot change the image recipe while paid generation is running")
    if isinstance(generation, dict) and generation:
        history = content.setdefault("image_generation_history", [])
        if isinstance(history, list):
            history.append(deepcopy(generation))
            del history[:-20]
    content.pop("remaining_images_preflight", None)
    content.pop("remaining_images_generation", None)
    content.pop("first_image_preflight", None)
    content.pop("first_image_generation", None)
    content["force_regenerate_all"] = True


def prepare_first_image_generation(offer_id_or_url: str) -> dict[str, Any]:
    """Create a no-network, no-charge preflight for the first identity-check image."""
    offer_id = resolve_offer_key(offer_id_or_url)
    state = load_state(offer_id)
    content = state.setdefault("content_package", {})
    _require_ai_assisted(content, "image generation preflight")
    if not content.get("fact_card_approved") or not content.get("suite_approved"):
        raise ValueError("approve the fact card and suite scope before preparing image generation")
    package_dir = _content_package_dir(str(content.get("collect_box_id") or ""))
    if package_dir is None:
        raise ValueError("content review package not found")
    review_package = _load_json(package_dir / "review_package.json") or {}
    if _apply_manual_storyboard_edits(content, review_package):
        _write_json_atomic(package_dir / "review_package.json", review_package)
    # Model observations remain review-only. Paid execution receives only
    # source-supported facts plus the approved composition direction.
    execution_plan = _safe_image_execution_plan(
        review_package,
        suite_customization=content.get("suite_customization"),
        required_planning_signature=_planning_recipe_signature(content),
    )
    from modules.sourcing.image_shot_prompts import build_shot_prompts
    prompts = build_shot_prompts(execution_plan)
    shots = prompts.get("shots") if isinstance(prompts.get("shots"), list) else []
    shot = next((row for row in shots if isinstance(row, dict) and row.get("type") == "scene"), None)
    if shot is None:
        shot = next((row for row in shots if isinstance(row, dict)), None)
    if shot is None:
        raise ValueError("no selected image shot is available")
    refs = content.get("identity_reference_urls") if isinstance(content.get("identity_reference_urls"), list) else []
    refs = [str(url) for url in refs if str(url).startswith("https://")]
    if not refs:
        refs = [str(shot.get("reference_image_url") or "")]
    from modules.sourcing.toapis_client import build_generation_payload

    payload = build_generation_payload(
        prompt=str(shot.get("prompt") or ""), model="gpt-image-2", size=str(shot.get("aspect_ratio") or "1:1"),
        resolution="1k", reference_images=refs, n=1,
    )
    preflight = {
        "status": "ready_for_explicit_paid_confirmation",
        "prepared_at": _now(),
        "shot_id": str(shot.get("id") or ""),
        "title": str(shot.get("title") or ""),
        "focus": str(shot.get("focus") or ""),
        "aspect_ratio": str(shot.get("aspect_ratio") or ""),
        "reference_count": len(refs),
        "model": "gpt-image-2",
        "payload": payload,
    }
    content["first_image_preflight"] = preflight
    save_state(offer_id, state)
    return {"ok": True, "preflight": preflight, "content_package": content_package_summary(offer_id)}


def _run_first_image_generation(offer_id: str) -> None:
    """Execute the explicitly confirmed first paid image task in the background."""
    lock = _image_generation_lock(offer_id)
    with lock:
        state = load_state(offer_id)
        content = state.setdefault("content_package", {})
        preflight = content.get("first_image_preflight") if isinstance(content.get("first_image_preflight"), dict) else {}
        package_dir = _content_package_dir(str(content.get("collect_box_id") or ""))
        if package_dir is None or not preflight.get("payload"):
            return
        artifact_id = str((content.get("first_image_generation") or {}).get("artifact_id") or "sc1_first_preview")
        generation = content.setdefault("first_image_generation", {})
        generation.update({"status": "running", "artifact_id": artifact_id, "error": ""})
        generation["worker_pid"] = os.getpid()
        save_state(offer_id, state)
        try:
            runtime_path = package_dir / f"execution_{artifact_id}.json"
            _write_json_atomic(runtime_path, {
                "collect_box_id": str(content.get("collect_box_id") or ""),
                "shot": {
                    "id": str(preflight.get("shot_id") or "sc1"),
                    "title": str(preflight.get("title") or ""),
                    "focus": str(preflight.get("focus") or ""),
                    "aspect_ratio": str(preflight.get("aspect_ratio") or "1:1"),
                },
                "payload": preflight.get("payload") or {},
            })
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "generate_approved_image_shot.py"),
                    "--payload-file", str(runtime_path),
                    "--artifact-id", artifact_id,
                    "--execute-paid",
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=420,
            )
            audit = _load_json(package_dir / f"generation_audit_{artifact_id}.json") or {}
            verified = bool(audit.get("download_verified"))
            state = load_state(offer_id)
            content = state.setdefault("content_package", {})
            generation = content.setdefault("first_image_generation", {})
            generation.update({
                "status": "completed_waiting_human_review" if verified else "failed",
                "artifact_id": artifact_id,
                "task_id": str(audit.get("task_id") or ""),
                "result_summary": str(audit.get("result_summary") or (completed.stderr or "task did not produce a verified image"))[:1000],
            })
            save_state(offer_id, state)
        except Exception as exc:
            state = load_state(offer_id)
            content = state.setdefault("content_package", {})
            content.setdefault("first_image_generation", {}).update({
                "status": "failed",
                "artifact_id": artifact_id,
                "error": str(exc)[:1000],
            })
            save_state(offer_id, state)


def start_first_image_generation(offer_id_or_url: str) -> dict[str, Any]:
    """Queue exactly one user-confirmed paid first-image task."""
    offer_id = resolve_offer_key(offer_id_or_url)
    lock = _image_generation_lock(offer_id)
    if lock.locked():
        raise ValueError("an image generation task is already running for this product")
    state = load_state(offer_id)
    content = state.setdefault("content_package", {})
    _require_ai_assisted(content, "paid image generation")
    preflight = content.get("first_image_preflight") if isinstance(content.get("first_image_preflight"), dict) else {}
    if preflight.get("status") != "ready_for_explicit_paid_confirmation" or not preflight.get("payload"):
        raise ValueError("prepare the first image preflight before starting paid generation")
    existing = content.get("first_image_generation") if isinstance(content.get("first_image_generation"), dict) else {}
    if str(existing.get("status") or "") in {"queued", "running"}:
        raise ValueError("first image generation is already running")
    content["first_image_generation"] = {
        "status": "queued",
        "worker_pid": os.getpid(),
        "queued_at": _now(),
        "artifact_id": "sc1_first_preview",
        "task_id": "",
        "result_summary": "等待创建 ToAPI 图片任务。",
    }
    save_state(offer_id, state)
    threading.Thread(target=_run_first_image_generation, args=(offer_id,), daemon=True, name=f"first-image-{offer_id}").start()
    return {"ok": True, "content_package": content_package_summary(offer_id)}


def prepare_remaining_image_generations(
    offer_id_or_url: str, *, artifact_suffix: str = "", include_first: bool = False,
    force_shot_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Prepare a reviewed batch without creating paid tasks.

    ``include_first`` is the current workflow: all selected suite shots are
    prepared as one paid batch. The old first-image gate remains supported for
    historical cases but is no longer used by the Treasury UI.
    """
    offer_id = resolve_offer_key(offer_id_or_url)
    state = load_state(offer_id)
    content = state.setdefault("content_package", {})
    _require_ai_assisted(content, "image generation preflight")
    if not content.get("fact_card_approved") or not content.get("suite_approved"):
        raise ValueError("approve the fact card and suite scope before preparing remaining images")

    package_dir = _content_package_dir(str(content.get("collect_box_id") or ""))
    if package_dir is None:
        raise ValueError("content review package not found")
    decisions = content.get("asset_decisions") if isinstance(content.get("asset_decisions"), dict) else {}
    artifacts = _content_artifacts(package_dir, decisions)
    technically_complete_shots = {
        str(row.get("shot_id") or "")
        for row in artifacts
        if row.get("technical_complete")
    }
    approved_first_shots = {
        str(row.get("shot_id") or "")
        for row in artifacts
        if row.get("technical_complete") and row.get("decision") == "approved"
    }
    if not include_first and not approved_first_shots:
        raise ValueError("approve at least one generated identity-check image before preparing remaining shots")
    requested_force_shots = force_shot_ids
    if not requested_force_shots and (not include_first or technically_complete_shots):
        requested_force_shots = content.get("pending_regeneration_shot_ids") or []
    requested_force_shots = requested_force_shots or []
    forced_shots = {
        str(value).strip() for value in requested_force_shots
        if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(value).strip())
    }
    force_all = bool(content.pop("force_regenerate_all", False))

    review_package = _load_json(package_dir / "review_package.json") or {}
    if _apply_manual_storyboard_edits(content, review_package):
        _write_json_atomic(package_dir / "review_package.json", review_package)
    # The planning model is advisory. Paid prompts are rebuilt from source facts,
    # the approved suite, and the selected identity references only.
    execution_plan = _safe_image_execution_plan(
        review_package,
        suite_customization=content.get("suite_customization"),
        required_planning_signature=_planning_recipe_signature(content),
    )
    from modules.sourcing.image_shot_prompts import build_shot_prompts
    from modules.sourcing.toapis_client import build_generation_payload

    bundle = build_shot_prompts(execution_plan)
    refs = content.get("identity_reference_urls") if isinstance(content.get("identity_reference_urls"), list) else []
    refs = [str(url) for url in refs if str(url).startswith("https://")]
    if not refs:
        refs = [str(execution_plan["_meta"].get("image_url") or "")]
    refs = [url for url in refs if url.startswith("https://")]
    if not refs:
        raise ValueError("select at least one public HTTPS identity reference before preparing remaining shots")
    clean_suffix = str(artifact_suffix or "").strip()
    if clean_suffix and not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", clean_suffix):
        raise ValueError("artifact_suffix contains unsupported characters")

    pending_shots = []
    for shot in bundle.get("shots") or []:
        if not isinstance(shot, dict) or (not include_first and str(shot.get("id") or "") in approved_first_shots):
            continue
        shot_id = str(shot.get("id") or "")
        if not shot_id:
            continue
        if str(shot.get("type") or "") == "size_card":
            source_item = next(
                (row for row in ((execution_plan.get("suite") or {}).get("items") or []) if str(row.get("id") or "") == shot_id),
                {},
            )
            if not bool(source_item.get("human_dimensions_confirmed")) or not str(source_item.get("human_dimensions") or "").strip():
                raise ValueError("confirm the exact dimensions before generating an operator-requested size card")
        if forced_shots and shot_id not in forced_shots:
            continue
        if include_first and shot_id in technically_complete_shots and not force_all and shot_id not in forced_shots:
            continue
        payload = build_generation_payload(
            prompt=str(shot.get("prompt") or ""),
            model="gpt-image-2",
            size=str(shot.get("aspect_ratio") or "1:1"),
            resolution="1k",
            reference_images=refs,
            n=1,
        )
        revision = max(1, int(content.get("suite_revision") or 1))
        batch_token = str(int(time.time() * 1000))
        pending_shots.append({
            "id": shot_id,
            "artifact_id": f"{shot_id}_{clean_suffix}" if clean_suffix else f"{shot_id}_r{revision}_{batch_token}",
            "type": str(shot.get("type") or ""),
            "title": str(shot.get("title") or ""),
            "focus": str(shot.get("focus") or ""),
            "aspect_ratio": str(shot.get("aspect_ratio") or ""),
            "reference_count": len(refs),
            "model": "gpt-image-2",
            "payload": payload,
            "human_dimensions": str(source_item.get("human_dimensions") or "") if str(shot.get("type") or "") == "size_card" else "",
        })
    if not pending_shots:
        raise ValueError("all selected suite shots already have locally verified generated images")

    preflight = {
        "status": "ready_for_explicit_paid_confirmation",
        "prepared_at": _now(),
        "first_shot_ids": sorted(approved_first_shots),
        "full_suite": include_first,
        "targeted_regeneration": bool(forced_shots),
        "suite_revision": max(1, int(content.get("suite_revision") or 1)),
        "recipe_signature": _content_recipe_signature(content),
        "shots": pending_shots,
    }
    content["remaining_images_preflight"] = preflight
    save_state(offer_id, state)
    return {"ok": True, "preflight": preflight, "content_package": content_package_summary(offer_id)}


def prepare_suite_image_generations(
    offer_id_or_url: str, *, force_shot_ids: list[str] | None = None
) -> dict[str, Any]:
    """Prepare all currently selected shots for one explicit paid batch."""
    return prepare_remaining_image_generations(
        offer_id_or_url, include_first=True, force_shot_ids=force_shot_ids
    )


def _run_remaining_image_generation(offer_id: str) -> None:
    """Background worker. Each paid image uses the already-reviewed exact payload."""
    lock = _image_generation_lock(offer_id)
    with lock:
        state = load_state(offer_id)
        content = state.setdefault("content_package", {})
        preflight = content.get("remaining_images_preflight") if isinstance(content.get("remaining_images_preflight"), dict) else {}
        package_dir = _content_package_dir(str(content.get("collect_box_id") or ""))
        if package_dir is None or not preflight.get("shots"):
            return
        generation = content.setdefault("remaining_images_generation", {})
        generation["status"] = "running"
        generation["worker_pid"] = os.getpid()
        generation["started_at"] = generation.get("started_at") or _now()
        generation.setdefault("items", [])
        save_state(offer_id, state)

        all_verified = True
        try:
            for shot in preflight.get("shots") or []:
                if not isinstance(shot, dict):
                    continue
                shot_id = str(shot.get("id") or "")
                if not shot_id:
                    continue
                artifact_id = str(shot.get("artifact_id") or f"{shot_id}_remaining")
                state = load_state(offer_id)
                content = state.setdefault("content_package", {})
                generation = content.setdefault("remaining_images_generation", {})
                generation["status"] = "running"
                generation["current_shot_id"] = shot_id
                items = generation.setdefault("items", [])
                item = next((row for row in items if str(row.get("artifact_id") or "") == artifact_id), None)
                if item is None:
                    item = {"shot_id": shot_id, "artifact_id": artifact_id}
                    items.append(item)
                item.update({"status": "creating_task", "task_id": "", "result_summary": "正在创建 ToAPI 图片任务。"})
                save_state(offer_id, state)

                runtime_path = package_dir / f"execution_{artifact_id}.json"
                _write_json_atomic(runtime_path, {
                    "collect_box_id": str(content.get("collect_box_id") or ""),
                    "shot": {key: shot.get(key) for key in ("id", "type", "title", "focus", "aspect_ratio", "human_dimensions")},
                    "payload": shot.get("payload") or {},
                })
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "scripts" / "generate_approved_image_shot.py"),
                        "--payload-file", str(runtime_path),
                        "--artifact-id", artifact_id,
                        "--execute-paid",
                    ],
                    cwd=str(ROOT),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=420,
                )
                audit = _load_json(package_dir / f"generation_audit_{artifact_id}.json") or {}
                verified = bool(audit.get("download_verified"))
                all_verified = all_verified and verified
                state = load_state(offer_id)
                content = state.setdefault("content_package", {})
                generation = content.setdefault("remaining_images_generation", {})
                items = generation.setdefault("items", [])
                item = next((row for row in items if str(row.get("artifact_id") or "") == artifact_id), None)
                if item is None:
                    item = {"shot_id": shot_id, "artifact_id": artifact_id}
                    items.append(item)
                item.update({
                    "status": "completed_waiting_human_review" if verified else "failed",
                    "task_id": str(audit.get("task_id") or ""),
                    "result_summary": str(audit.get("result_summary") or (completed.stderr or "task did not produce a verified image"))[:1000],
                })
                save_state(offer_id, state)
        except Exception as exc:
            all_verified = False
            state = load_state(offer_id)
            content = state.setdefault("content_package", {})
            generation = content.setdefault("remaining_images_generation", {})
            generation["error"] = str(exc)[:1000]
            save_state(offer_id, state)
        finally:
            state = load_state(offer_id)
            content = state.setdefault("content_package", {})
            generation = content.setdefault("remaining_images_generation", {})
            generation["status"] = "completed_waiting_human_review" if all_verified else "completed_with_errors"
            generation["current_shot_id"] = ""
            generation["finished_at"] = _now()
            if all_verified:
                completed_shot_ids = {
                    str(row.get("id") or "")
                    for row in (preflight.get("shots") or [])
                    if isinstance(row, dict) and str(row.get("id") or "")
                }
                pending_shot_ids = [
                    str(shot_id)
                    for shot_id in (
                        content.get("pending_regeneration_shot_ids") or []
                    )
                    if str(shot_id) and str(shot_id) not in completed_shot_ids
                ]
                if pending_shot_ids:
                    content["pending_regeneration_shot_ids"] = pending_shot_ids
                else:
                    content.pop("pending_regeneration_shot_ids", None)
            save_state(offer_id, state)


def _start_remaining_image_generation_unlocked(
    offer_id_or_url: str,
    *,
    retry_failed_only: bool = False,
) -> dict[str, Any]:
    """Start the user-confirmed paid background batch for remaining image shots."""
    offer_id = resolve_offer_key(offer_id_or_url)
    lock = _image_generation_lock(offer_id)
    if lock.locked():
        raise ValueError("remaining image generation is already running")
    state = load_state(offer_id)
    content = state.setdefault("content_package", {})
    _require_ai_assisted(content, "paid image generation")
    preflight = content.get("remaining_images_preflight") if isinstance(content.get("remaining_images_preflight"), dict) else {}
    if preflight.get("status") != "ready_for_explicit_paid_confirmation" or not preflight.get("shots"):
        raise ValueError("prepare and review the remaining image preflight before starting paid generation")
    current_revision = max(1, int(content.get("suite_revision") or 1))
    if int(preflight.get("suite_revision") or 0) != current_revision:
        raise ValueError("the image recipe changed after this preflight; prepare a new preflight before paid generation")
    if str(preflight.get("recipe_signature") or "") != _content_recipe_signature(content):
        raise ValueError("the image references or recipe changed after this preflight; prepare a new preflight")
    existing = content.get("remaining_images_generation") if isinstance(content.get("remaining_images_generation"), dict) else {}
    if str(existing.get("status") or "") in {"queued", "running"}:
        worker_pid = int(existing.get("worker_pid") or 0)
        if worker_pid == os.getpid() and (str(existing.get("status")) == "queued" or lock.locked()):
            raise ValueError("remaining image generation is already running")
    selected_shots = [
        shot
        for shot in preflight.get("shots") or []
        if isinstance(shot, dict) and str(shot.get("id") or "")
    ]
    if retry_failed_only:
        failed_shot_ids = {
            str(row.get("shot_id") or "")
            for row in (existing.get("items") or [])
            if isinstance(row, dict) and str(row.get("status") or "") == "failed"
        }
        selected_shots = [
            shot for shot in selected_shots if str(shot.get("id") or "") in failed_shot_ids
        ]
        if not selected_shots:
            raise ValueError("there are no failed image tasks to retry")
    elif str(existing.get("status") or "") in {
        "completed_waiting_human_review",
        "completed_with_errors",
    }:
        existing_artifacts = {
            str(row.get("artifact_id") or "")
            for row in (existing.get("items") or [])
            if isinstance(row, dict) and str(row.get("artifact_id") or "")
        }
        selected_artifacts = {
            str(row.get("artifact_id") or "")
            for row in selected_shots
            if str(row.get("artifact_id") or "")
        }
        if existing_artifacts and existing_artifacts == selected_artifacts:
            raise ValueError(
                "this paid-generation preflight has already been consumed; "
                "prepare an explicit regeneration preflight before creating new tasks"
            )
    if existing:
        history = content.setdefault("image_generation_history", [])
        if isinstance(history, list):
            history.append(deepcopy(existing))
            del history[:-20]
    generation = {
        "status": "queued",
        "worker_pid": os.getpid(),
        "queued_at": _now(),
        "preflight_prepared_at": str(preflight.get("prepared_at") or ""),
        "current_shot_id": "",
        "retry_failed_only": bool(retry_failed_only),
        "items": [
            {"shot_id": str(shot.get("id") or ""), "artifact_id": str(shot.get("artifact_id") or f"{shot.get('id')}_remaining"), "status": "queued", "task_id": "", "result_summary": "等待创建任务。"}
            for shot in selected_shots
        ],
    }
    content["remaining_images_generation"] = generation
    save_state(offer_id, state)
    threading.Thread(target=_run_remaining_image_generation, args=(offer_id,), daemon=True, name=f"remaining-images-{offer_id}").start()
    return {"ok": True, "content_package": content_package_summary(offer_id)}


def start_remaining_image_generation(
    offer_id_or_url: str,
    *,
    retry_failed_only: bool = False,
) -> dict[str, Any]:
    """Atomically validate and queue one explicitly confirmed paid image batch."""
    offer_id = resolve_offer_key(offer_id_or_url)
    with _image_generation_queue_lock(offer_id):
        return _start_remaining_image_generation_unlocked(
            offer_id,
            retry_failed_only=retry_failed_only,
        )


def _approved_generated_image_urls(package_dir: Path, decisions: dict[str, Any]) -> list[dict[str, str]]:
    """Return approved, locally verified ToAPI image outputs in suite order."""
    rows: list[dict[str, str]] = []
    seen_shots: set[str] = set()
    for audit_path in sorted(package_dir.glob("generation_audit_*.json")):
        artifact_id = audit_path.stem.removeprefix("generation_audit_")
        decision = decisions.get(artifact_id) if isinstance(decisions.get(artifact_id), dict) else {}
        audit = _load_json(audit_path) or {}
        shot_id = str(audit.get("shot_id") or artifact_id.split("_", 1)[0])
        image_file = package_dir / "generated" / f"{artifact_id}.png"
        data = ((audit.get("final_response") or {}).get("result") or {}).get("data") or []
        remote_url = str((data[0] or {}).get("url") or "") if data and isinstance(data[0], dict) else ""
        if (
            decision.get("decision") != "approved"
            or not bool(audit.get("download_verified"))
            or not image_file.is_file()
            or not remote_url.startswith("https://")
        ):
            continue
        if shot_id in seen_shots:
            continue
        seen_shots.add(shot_id)
        rows.append({"shot_id": shot_id, "artifact_id": artifact_id, "url": remote_url})
    priority = {"sc1": 0, "sc2": 1, "sc3": 2, "sp1": 3}
    return sorted(rows, key=lambda row: (priority.get(row["shot_id"], 99), row["artifact_id"]))


def _miaoshou_editable_weight_kg(value: float) -> float:
    """Miaoshou accepts 0.01kg minimum and at most two decimal places.

    Round upward so an existing shipping weight is never understated merely to
    satisfy an API formatting constraint.
    """
    number = float(value or 0)
    if number < 0.01:
        raise ValueError("Miaoshou requires a weight of at least 0.01 kg")
    return math.ceil((number - 1e-10) * 100) / 100


def _generated_urls_for_artifacts(package_dir: Path, artifacts: list[dict[str, Any]]) -> list[str]:
    urls: list[str] = []
    for row in artifacts:
        artifact_id = str((row or {}).get("artifact_id") or "")
        audit = _load_json(package_dir / f"generation_audit_{artifact_id}.json") or {}
        data = ((audit.get("final_response") or {}).get("result") or {}).get("data") or []
        url = str((data[0] or {}).get("url") or "") if data and isinstance(data[0], dict) else ""
        if url.startswith("https://") and url not in urls:
            urls.append(url)
    return urls


def sync_generated_image_to_miaoshou(
    offer_id_or_url: str, artifact_id: str, action: str, *, post=None
) -> dict[str, Any]:
    """Apply one explicit generated-image decision to Miaoshou.

    Only the selected generated URL is added or removed. Existing source images,
    title, SKU map, prices, variants, dimensions, inventory, claiming, and
    publishing are deliberately outside this operation.
    """
    offer_id = resolve_offer_key(offer_id_or_url)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(artifact_id or "")):
        raise ValueError("invalid generated image identifier")
    if action not in {"keep", "remove"}:
        raise ValueError("generated image action must be keep or remove")
    state = load_state(offer_id)
    content = state.setdefault("content_package", {})
    package_dir = _content_package_dir(str(content.get("collect_box_id") or ""))
    if package_dir is None:
        raise ValueError("content review package not found")
    audit = _load_json(package_dir / f"generation_audit_{artifact_id}.json") or {}
    image_file = package_dir / "generated" / f"{artifact_id}.png"
    data = ((audit.get("final_response") or {}).get("result") or {}).get("data") or []
    image_url = str((data[0] or {}).get("url") or "") if data and isinstance(data[0], dict) else ""
    if not bool(audit.get("download_verified")) or not image_file.is_file() or not image_url.startswith("https://"):
        raise ValueError("the generated image has not passed local download verification")

    if post is None:
        from modules.miaoshou.client import post_open
        post = post_open
    detail_id = int(str(content.get("collect_box_id") or ""))
    detail_path = "/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_detail"
    edit_path = "/open/v1/product/common_collect_box/common_collect_box/edit_common_collect_box_detail"
    current_resp = post(detail_path, {"commonCollectBoxDetailId": detail_id})
    if current_resp.get("result") != "success":
        raise RuntimeError(f"Miaoshou detail read failed: {current_resp.get('code')} {current_resp.get('message', '')}")
    payload = current_resp.get("data") or {}
    current = payload.get("editCommonCollectBoxDetail") or {}
    oss_md5 = str(payload.get("ossMd5") or "")
    if not current or not oss_md5:
        raise RuntimeError("Miaoshou detail is missing editable data or ossMd5")

    updated = dict(current)
    current_urls = [str(url).strip() for url in current.get("imgUrls") or [] if str(url).strip()]
    notes = str(current.get("notes") or "")
    image_tag = f'<p><img src="{html.escape(image_url, quote=True)}" alt="Generated product image" style="display:block;width:100%;height:auto;"/></p>'
    if action == "keep":
        updated["imgUrls"] = list(dict.fromkeys(current_urls + [image_url]))
        if image_url not in notes:
            updated["notes"] = notes + image_tag
    else:
        updated["imgUrls"] = [url for url in current_urls if url != image_url]
        updated["notes"] = re.sub(
            r'<p><img[^>]*src=["\']' + re.escape(image_url) + r'["\'][^>]*></p>',
            "", notes, flags=re.IGNORECASE,
        )

    current_weight = float(current.get("weight") or 0)
    saved_weight = float((state.get("review") or {}).get("weight_kg") or 0)
    candidate_weight = current_weight if current_weight >= 0.01 else saved_weight
    if candidate_weight < 0.01:
        raise ValueError("Miaoshou requires a saved Treasury weight of at least 0.01 kg before image sync")
    editable_weight = _miaoshou_editable_weight_kg(candidate_weight)
    if current_weight < 0.01 or abs(editable_weight - current_weight) > 0.000001:
        updated["weight"] = editable_weight
        updated["skuMap"] = {key: {**dict(row or {}), "weight": editable_weight} for key, row in (current.get("skuMap") or {}).items()}

    decisions = content.setdefault("generated_image_miaoshou_decisions", {})
    decisions[artifact_id] = {"action": action, "status": "writing", "started_at": _now(), "url": image_url}
    save_state(offer_id, state)
    try:
        save_resp = post(edit_path, {"commonCollectBoxDetailId": detail_id, "editCommonCollectBoxDetail": updated, "ossMd5": oss_md5})
    except Exception as exc:
        decisions[artifact_id].update({"status": "failed", "error": str(exc)[:1000]})
        save_state(offer_id, state)
        raise
    if save_resp.get("result") != "success":
        error = str(save_resp.get("message") or save_resp.get("code") or "write failed")
        decisions[artifact_id].update({"status": "failed", "error": error})
        save_state(offer_id, state)
        raise RuntimeError(f"Miaoshou image sync failed: {error}")
    verify_resp = post(detail_path, {"commonCollectBoxDetailId": detail_id})
    verified = ((verify_resp.get("data") or {}).get("editCommonCollectBoxDetail") or {}) if verify_resp.get("result") == "success" else {}
    url_present = image_url in [str(url) for url in verified.get("imgUrls") or []]
    note_present = image_url in str(verified.get("notes") or "")
    checks = {"image_list": url_present == (action == "keep"), "detail_notes": note_present == (action == "keep"), "title_unchanged": str(verified.get("title") or "") == str(current.get("title") or "")}
    decisions[artifact_id].update({"status": "synced" if all(checks.values()) else "verification_failed", "synced_at": _now(), "checks": checks, "error": ""})
    save_state(offer_id, state)
    return {"ok": all(checks.values()), "artifact_id": artifact_id, "action": action, "collect_box_id": str(detail_id), "checks": checks, "claimed": False, "published": False}


def save_generated_image_decision(
    offer_id_or_url: str, artifact_id: str, action: str
) -> dict[str, Any]:
    """Save one image-review decision locally without writing to Miaoshou."""
    offer_id = resolve_offer_key(offer_id_or_url)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(artifact_id or "")):
        raise ValueError("invalid generated image identifier")
    if action not in {"keep", "remove"}:
        raise ValueError("generated image action must be keep or remove")
    state = load_state(offer_id)
    content = state.setdefault("content_package", {})
    package_dir = _content_package_dir(str(content.get("collect_box_id") or ""))
    if package_dir is None:
        raise ValueError("content review package not found")
    audit = _load_json(package_dir / f"generation_audit_{artifact_id}.json") or {}
    image_file = package_dir / "generated" / f"{artifact_id}.png"
    data = ((audit.get("final_response") or {}).get("result") or {}).get("data") or []
    image_url = str((data[0] or {}).get("url") or "") if data and isinstance(data[0], dict) else ""
    if not bool(audit.get("download_verified")) or not image_file.is_file() or not image_url.startswith("https://"):
        raise ValueError("the generated image has not passed local download verification")
    decisions = content.setdefault("generated_image_miaoshou_decisions", {})
    decisions[artifact_id] = {
        "action": action,
        "status": "reviewed_locally",
        "reviewed_at": _now(),
        "url": image_url,
        "error": "",
    }
    review = state.setdefault("review", {})
    image_order = [
        str(url).strip()
        for url in (review.get("image_order") or [])
        if str(url).strip() and str(url).strip() != image_url
    ]
    if action == "keep":
        image_order.append(image_url)
    review["image_order"] = image_order
    save_state(offer_id, state)
    return {
        "ok": True,
        "artifact_id": artifact_id,
        "action": action,
        "content_package": content_package_summary(offer_id),
        "written_to_miaoshou": False,
    }


def _ordered_selected_images(
    offer_id: str, state: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Return the unified source + generated image bar in its saved order."""
    state = state or load_state(offer_id)
    source = _source_summary(offer_id)
    review = state.get("review") if isinstance(state.get("review"), dict) else {}
    content = (
        state.get("content_package")
        if isinstance(state.get("content_package"), dict)
        else {}
    )
    available: dict[str, dict[str, str]] = {}
    defaults: list[str] = []

    source_rows = review.get("image_actions") or source.get("images") or []
    for index, row in enumerate(source_rows, start=1):
        if not isinstance(row, dict) or str(row.get("action") or "review") != "keep":
            continue
        url = str(row.get("output_url") or row.get("url") or "").strip()
        if not url or url in available:
            continue
        available[url] = {
            "url": url,
            "kind": "source",
            "label": f"Source image {index}",
            "artifact_id": "",
            "shot_id": "",
        }
        defaults.append(url)

    package_dir = _content_package_dir(str(content.get("collect_box_id") or ""))
    for row in _generated_review_images(offer_id, content, package_dir):
        if str(row.get("miaoshou_action") or "review") != "keep":
            continue
        url = str(row.get("url") or "").strip()
        if not url or url in available:
            continue
        available[url] = {
            "url": url,
            "kind": "generated",
            "label": str(row.get("shot_id") or row.get("artifact_id") or "AI image"),
            "artifact_id": str(row.get("artifact_id") or ""),
            "shot_id": str(row.get("shot_id") or ""),
        }
        defaults.append(url)

    requested = [
        str(url).strip()
        for url in (review.get("image_order") or [])
        if str(url).strip()
    ]
    ordered_urls = list(dict.fromkeys(
        [url for url in requested if url in available] + defaults
    ))
    items = [available[url] for url in ordered_urls]
    return {
        "items": items,
        "urls": ordered_urls,
        "source_urls": [row["url"] for row in items if row["kind"] == "source"],
        "generated_urls": [row["url"] for row in items if row["kind"] == "generated"],
    }


def _image_approval_blockers(offer_id: str, state: dict[str, Any]) -> list[str]:
    """Return local review blockers before any Miaoshou request is attempted."""
    review = state.get("review") if isinstance(state.get("review"), dict) else {}
    source = _source_summary(offer_id)
    source_rows = review.get("image_actions") or source.get("images") or []
    pending_source = sum(
        1 for row in source_rows if isinstance(row, dict)
        and str(row.get("action") or "review") not in {"keep", "remove"}
    )
    content = state.get("content_package") if isinstance(state.get("content_package"), dict) else {}
    package_dir = _content_package_dir(str(content.get("collect_box_id") or ""))
    pending_generated = sum(
        1 for row in _generated_review_images(offer_id, content, package_dir)
        if str(row.get("miaoshou_action") or "review") not in {"keep", "remove"}
    )
    blockers = []
    if pending_source:
        blockers.append(f"{pending_source} source image(s) still require explicit keep or remove")
    if pending_generated:
        blockers.append(f"{pending_generated} generated image(s) still require explicit keep or remove")
    return blockers


def _miaoshou_sync_steps(active_id: str = "") -> list[dict[str, str]]:
    definitions = (
        ("review_gate", "审核门禁与最终顺序"),
        ("read_current", "读取妙手采集箱当前版本"),
        ("write_images", "写入主图与详情图"),
        ("readback_verify", "回读并逐项验证"),
    )
    active_found = False
    rows = []
    for step_id, label in definitions:
        if step_id == active_id:
            status = "running"
            active_found = True
        elif active_found:
            status = "pending"
        else:
            status = "completed"
        rows.append({"id": step_id, "label": label, "status": status, "detail": ""})
    return rows


def _set_miaoshou_sync_phase(
    write_state: dict[str, Any],
    *,
    status: str,
    phase: str,
    active_step: str = "",
    error: str = "",
    detail: str = "",
) -> None:
    write_state["status"] = status
    write_state["phase"] = phase
    write_state["steps"] = _miaoshou_sync_steps(active_step)
    if detail and active_step:
        for row in write_state["steps"]:
            if row["id"] == active_step:
                row["detail"] = detail
    if error:
        write_state["error"] = error[:1000]
        for row in write_state["steps"]:
            if row["id"] == active_step:
                row["status"] = "failed"
                row["detail"] = error[:1000]


def write_ordered_images_to_miaoshou(
    offer_id_or_url: str, *, post=None
) -> dict[str, Any]:
    """Run one guarded image sync; duplicate clicks cannot start two writes."""
    offer_id = resolve_offer_key(offer_id_or_url)
    lock = _miaoshou_image_sync_lock(offer_id)
    if not lock.acquire(blocking=False):
        raise RuntimeError("妙手图片同步已在进行中，请等待当前任务完成")
    try:
        return _write_ordered_images_to_miaoshou_unlocked(offer_id, post=post)
    finally:
        lock.release()


def _write_ordered_images_to_miaoshou_unlocked(
    offer_id_or_url: str, *, post=None
) -> dict[str, Any]:
    """Write the unified image bar to Miaoshou main and detail images.

    The operator's saved order becomes the exact ``imgUrls`` order and the
    image order in ``notes``. Other product fields are preserved. The function
    performs a read-back verification and never claims or publishes a product.
    """
    offer_id = resolve_offer_key(offer_id_or_url)
    state = load_state(offer_id)
    content = state.setdefault("content_package", {})
    approval_blockers = _image_approval_blockers(offer_id, state)
    if approval_blockers:
        raise ValueError("cannot synchronize images: " + "; ".join(approval_blockers))
    selected = _ordered_selected_images(offer_id, state)
    image_urls = list(selected["urls"])
    if not image_urls:
        raise ValueError("keep at least one image before synchronizing to Miaoshou")

    if post is None:
        from modules.miaoshou.client import post_open

        post = post_open
    source = _source_summary(offer_id)
    detail_id = int(_content_collect_box_id(offer_id, state, source))
    content["collect_box_id"] = str(detail_id)
    detail_path = "/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_detail"
    edit_path = "/open/v1/product/common_collect_box/common_collect_box/edit_common_collect_box_detail"
    write_state = {
        "status": "preparing",
        "phase": "read_current",
        "started_at": _now(),
        "collect_box_id": str(detail_id),
        "ordered_image_urls": image_urls,
        "source_image_count": len(selected["source_urls"]),
        "generated_image_count": len(selected["generated_urls"]),
        "suite_revision": max(1, int(content.get("suite_revision") or 1)),
        "recipe_signature": _content_recipe_signature(content),
        "steps": _miaoshou_sync_steps("read_current"),
        "checks": {},
        "error": "",
    }
    content["miaoshou_ordered_images_write"] = write_state
    state.setdefault("review", {})["image_order"] = image_urls
    save_state(offer_id, state)
    try:
        current_resp = post(detail_path, {"commonCollectBoxDetailId": detail_id})
    except Exception as exc:
        _set_miaoshou_sync_phase(
            write_state,
            status="failed",
            phase="read_current",
            active_step="read_current",
            error=str(exc),
        )
        write_state["finished_at"] = _now()
        save_state(offer_id, state)
        raise
    if current_resp.get("result") != "success":
        message = (
            f"妙手详情读取失败: {current_resp.get('code')} "
            f"{current_resp.get('message', '')}"
        ).strip()
        _set_miaoshou_sync_phase(
            write_state,
            status="failed",
            phase="read_current",
            active_step="read_current",
            error=message,
        )
        write_state["finished_at"] = _now()
        save_state(offer_id, state)
        raise RuntimeError(
            message
        )
    payload = current_resp.get("data") or {}
    current = payload.get("editCommonCollectBoxDetail") or {}
    oss_md5 = str(payload.get("ossMd5") or "")
    if not current or not oss_md5:
        message = "妙手详情缺少编辑数据或 ossMd5"
        _set_miaoshou_sync_phase(
            write_state,
            status="failed",
            phase="read_current",
            active_step="read_current",
            error=message,
        )
        write_state["finished_at"] = _now()
        save_state(offer_id, state)
        raise RuntimeError(message)

    current_notes = str(current.get("notes") or "")
    notes_without_images = re.sub(
        r"<p\b[^>]*>\s*<img\b[^>]*>\s*</p>",
        "",
        current_notes,
        flags=re.IGNORECASE,
    )
    notes_without_images = re.sub(
        r"<img\b[^>]*>",
        "",
        notes_without_images,
        flags=re.IGNORECASE,
    )
    notes_without_images = re.sub(
        r"<p\b[^>]*>\s*</p>",
        "",
        notes_without_images,
        flags=re.IGNORECASE,
    ).strip()
    ordered_image_notes = "".join(
        f'<p><img src="{html.escape(url, quote=True)}" alt="Product image {index}" '
        'style="display:block;width:100%;height:auto;"/></p>'
        for index, url in enumerate(image_urls, start=1)
    )
    notes = notes_without_images + ordered_image_notes

    updated = dict(current)
    updated["imgUrls"] = image_urls
    updated["notes"] = notes
    saved_weight = float((state.get("review") or {}).get("weight_kg") or 0)
    if saved_weight < 0.01:
        message = (
            "Miaoshou requires a saved product weight of at least 0.01 kg "
            "before image synchronization"
        )
        _set_miaoshou_sync_phase(
            write_state,
            status="failed",
            phase="write_images",
            active_step="write_images",
            error=message,
        )
        write_state["finished_at"] = _now()
        save_state(offer_id, state)
        raise ValueError(message)
    current_weight = float(current.get("weight") or 0)
    updated["weight"] = (
        current_weight
        if current_weight >= 0.01
        and abs(_miaoshou_editable_weight_kg(current_weight) - current_weight) < 0.000001
        else _miaoshou_editable_weight_kg(saved_weight)
    )
    saved_sku_facts = (state.get("review") or {}).get("sku_commercial_facts") or {}
    updated_skus = {}
    for sku_key, row in (current.get("skuMap") or {}).items():
        current_sku_weight = float((row or {}).get("weight") or 0)
        if current_sku_weight >= 0.01 and abs(
            _miaoshou_editable_weight_kg(current_sku_weight) - current_sku_weight
        ) < 0.000001:
            effective_sku_weight = current_sku_weight
        else:
            saved_sku_weight = float(
                ((saved_sku_facts.get(sku_key) or {}).get("weight_kg")) or saved_weight
            )
            effective_sku_weight = _miaoshou_editable_weight_kg(saved_sku_weight)
        updated_skus[sku_key] = {
            **dict(row or {}),
            "weight": effective_sku_weight,
        }
    updated["skuMap"] = updated_skus

    weight_repair_required = (
        updated.get("weight") != current.get("weight")
        or updated_skus != (current.get("skuMap") or {})
    )
    if weight_repair_required:
        repair_payload = dict(current)
        repair_payload["weight"] = updated["weight"]
        repair_payload["skuMap"] = updated_skus
        repair_resp = post(edit_path, {
            "commonCollectBoxDetailId": detail_id,
            "editCommonCollectBoxDetail": repair_payload,
            "ossMd5": oss_md5,
        })
        if repair_resp.get("result") != "success":
            raise RuntimeError(
                f"Miaoshou weight repair failed: {repair_resp.get('code')} "
                f"{repair_resp.get('message', '')}"
            )
        repaired_resp = post(detail_path, {"commonCollectBoxDetailId": detail_id})
        repaired_data = repaired_resp.get("data") or {}
        repaired = repaired_data.get("editCommonCollectBoxDetail") or {}
        if (
            repaired_resp.get("result") != "success"
            or repaired.get("weight") != updated.get("weight")
            or (repaired.get("skuMap") or {}) != updated_skus
        ):
            raise RuntimeError("Miaoshou weight repair read-back failed")
        oss_md5 = str(repaired_data.get("ossMd5") or "")
        if not oss_md5:
            raise RuntimeError("Miaoshou weight repair read-back is missing ossMd5")

    write_state.update({
        "previous_img_urls": list(current.get("imgUrls") or []),
        "previous_notes": current_notes,
    })
    _set_miaoshou_sync_phase(
        write_state,
        status="writing",
        phase="write_images",
        active_step="write_images",
        detail=f"正在写入 {len(image_urls)} 张主图与详情图",
    )
    save_state(offer_id, state)

    try:
        save_resp = post(edit_path, {
            "commonCollectBoxDetailId": detail_id,
            "editCommonCollectBoxDetail": updated,
            "ossMd5": oss_md5,
        })
    except Exception as exc:
        _set_miaoshou_sync_phase(
            write_state,
            status="failed",
            phase="write_images",
            active_step="write_images",
            error=str(exc),
        )
        write_state["finished_at"] = _now()
        save_state(offer_id, state)
        raise
    if save_resp.get("result") != "success":
        error = str(save_resp.get("message") or save_resp.get("code") or "write failed")
        _set_miaoshou_sync_phase(
            write_state,
            status="failed",
            phase="write_images",
            active_step="write_images",
            error=error,
        )
        write_state["finished_at"] = _now()
        save_state(offer_id, state)
        raise RuntimeError(
            f"妙手图片写入失败: {save_resp.get('code')} {save_resp.get('message', '')}"
        )

    _set_miaoshou_sync_phase(
        write_state,
        status="verifying",
        phase="readback_verify",
        active_step="readback_verify",
        detail="妙手已接受写入，正在回读核对顺序与关键字段",
    )
    save_state(offer_id, state)
    try:
        verify_resp = post(detail_path, {"commonCollectBoxDetailId": detail_id})
    except Exception as exc:
        _set_miaoshou_sync_phase(
            write_state,
            status="verification_failed",
            phase="readback_verify",
            active_step="readback_verify",
            error=str(exc),
        )
        write_state["finished_at"] = _now()
        save_state(offer_id, state)
        raise
    if verify_resp.get("result") != "success":
        _set_miaoshou_sync_phase(
            write_state,
            status="verification_failed",
            phase="readback_verify",
            active_step="readback_verify",
            error="read-back failed",
        )
        write_state["finished_at"] = _now()
        save_state(offer_id, state)
        raise RuntimeError("妙手图片写入后验证读取失败")
    verified = ((verify_resp.get("data") or {}).get("editCommonCollectBoxDetail") or {})
    verified_notes = str(verified.get("notes") or "")
    positions = [verified_notes.find(url) for url in image_urls]
    checks = {
        "main_images_exact_order": list(verified.get("imgUrls") or []) == image_urls,
        "detail_images_exact_order": (
            all(position >= 0 for position in positions)
            and positions == sorted(positions)
            and len(re.findall(r"<img\b", verified_notes, flags=re.IGNORECASE))
            == len(image_urls)
        ),
        "title_unchanged": str(verified.get("title") or "") == str(current.get("title") or ""),
        "item_num_unchanged": str(verified.get("itemNum") or "") == str(current.get("itemNum") or ""),
        "weight_reconciled": verified.get("weight") == updated.get("weight"),
        "sku_map_reconciled": (verified.get("skuMap") or {}) == updated.get("skuMap"),
    }
    checks_passed = all(checks.values())
    _set_miaoshou_sync_phase(
        write_state,
        status="verified" if checks_passed else "verification_failed",
        phase="completed" if checks_passed else "readback_verify",
        active_step="" if checks_passed else "readback_verify",
        error="" if checks_passed else "one or more read-back checks failed",
    )
    write_state.update({
        "finished_at": _now(),
        "checks": checks,
        "written_image_count": len(image_urls),
    })
    save_state(offer_id, state)
    return {
        "ok": checks_passed,
        "written_to_miaoshou": True,
        "verified": checks_passed,
        "collect_box_id": str(detail_id),
        "written_image_count": len(image_urls),
        "source_image_count": len(selected["source_urls"]),
        "generated_image_count": len(selected["generated_urls"]),
        "image_urls": image_urls,
        "checks": checks,
        "sync": {
            "status": write_state["status"],
            "phase": write_state["phase"],
            "steps": list(write_state["steps"]),
            "error": str(write_state.get("error") or ""),
            "written_image_count": len(image_urls),
            "ordered_image_count": len(image_urls),
            "checks": dict(checks),
            "collect_box_id": str(detail_id),
        },
        "claimed": False,
        "published": False,
    }


def write_approved_generated_images_to_miaoshou(offer_id_or_url: str, *, post=None) -> dict[str, Any]:
    """Replace one collect box's image URLs with human-approved generated outputs.

    This is intentionally limited to ``imgUrls`` and image-only detail notes.
    If the existing collect-box weight is below Miaoshou's editable minimum, it
    also carries forward the operator's saved Treasury weight so the API can
    accept the otherwise image-only write. It never claims, publishes, or
    changes title, SKU, price, variants, dimensions, or inventory.
    """
    offer_id = resolve_offer_key(offer_id_or_url)
    state = load_state(offer_id)
    content = state.setdefault("content_package", {})
    package_dir = _content_package_dir(str(content.get("collect_box_id") or ""))
    if package_dir is None:
        raise ValueError("content review package not found")
    decisions = content.get("asset_decisions") if isinstance(content.get("asset_decisions"), dict) else {}
    approved = _approved_generated_image_urls(package_dir, decisions)
    if len(approved) < 4:
        raise ValueError("all four generated images must be locally verified and explicitly approved before write-back")

    if post is None:
        from modules.miaoshou.client import post_open
        post = post_open
    detail_id = int(str(content.get("collect_box_id") or ""))
    detail_path = "/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_detail"
    edit_path = "/open/v1/product/common_collect_box/common_collect_box/edit_common_collect_box_detail"
    current_resp = post(detail_path, {"commonCollectBoxDetailId": detail_id})
    if current_resp.get("result") != "success":
        raise RuntimeError(f"妙手详情读取失败: {current_resp.get('code')} {current_resp.get('message', '')}")
    payload = current_resp.get("data") or {}
    current = payload.get("editCommonCollectBoxDetail") or {}
    oss_md5 = str(payload.get("ossMd5") or "")
    if not current or not oss_md5:
        raise RuntimeError("妙手详情缺少编辑数据或 ossMd5")

    generated_urls = [row["url"] for row in approved]
    prior_write = content.get("miaoshou_generated_images_write") if isinstance(content.get("miaoshou_generated_images_write"), dict) else {}
    current_urls = [str(url).strip() for url in current.get("imgUrls") or [] if str(url).strip()]
    previous_urls = [str(url).strip() for url in prior_write.get("previous_img_urls") or [] if str(url).strip()]
    prior_generated_urls = [str(url).strip() for url in prior_write.get("generated_image_urls") or [] if str(url).strip()]
    if not prior_generated_urls:
        prior_generated_urls = _generated_urls_for_artifacts(package_dir, list(prior_write.get("artifacts") or []))
    # Repair a legacy replacement write by restoring its snapshot before append.
    if previous_urls and set(current_urls).issubset(set(prior_generated_urls)):
        base_urls = previous_urls
    else:
        base_urls = [url for url in current_urls if url not in prior_generated_urls]
    image_urls = list(dict.fromkeys(base_urls + generated_urls))
    generated_notes = "".join(
        f'<p><img src="{html.escape(url, quote=True)}" alt="Product image {idx}" style="display:block;width:100%;height:auto;"/></p>'
        for idx, url in enumerate(generated_urls, start=1)
    )
    current_notes = str(current.get("notes") or "")
    previous_notes = str(prior_write.get("previous_notes") or "")
    if previous_notes and set(current_urls).issubset(set(prior_generated_urls)):
        base_notes = previous_notes
    else:
        base_notes = current_notes
        for url in prior_generated_urls:
            base_notes = re.sub(
                r'<p><img[^>]*src=["\']' + re.escape(url) + r'["\'][^>]*></p>',
                "",
                base_notes,
                flags=re.IGNORECASE,
            )
    notes = base_notes + generated_notes
    updated = dict(current)
    updated["imgUrls"] = image_urls
    updated["notes"] = notes
    current_weight = float(current.get("weight") or 0)
    saved_weight = float((state.get("review") or {}).get("weight_kg") or 0)
    candidate_weight = current_weight if current_weight >= 0.01 else saved_weight
    if candidate_weight < 0.01:
        raise ValueError("Miaoshou requires a weight of at least 0.01 kg; save a valid weight in Treasury before image write-back")
    editable_weight = _miaoshou_editable_weight_kg(candidate_weight)
    used_saved_weight = current_weight < 0.01
    weight_normalized = abs(editable_weight - current_weight) > 0.000001
    if used_saved_weight or weight_normalized:
        updated["weight"] = editable_weight
        updated["skuMap"] = {
            key: {**dict(row or {}), "weight": editable_weight}
            for key, row in (current.get("skuMap") or {}).items()
        }

    # Persist a rollback snapshot locally before the real write.
    content["miaoshou_generated_images_write"] = {
        "status": "writing",
        "started_at": _now(),
        "collect_box_id": str(detail_id),
        "previous_img_urls": base_urls,
        "previous_notes": base_notes,
        "previous_title": str(current.get("title") or ""),
        "previous_weight": current_weight,
        "written_weight": float(updated.get("weight") or 0),
        "weight_source": "saved_treasury_review" if used_saved_weight else "existing_miaoshou_value",
        "weight_normalized_upward": weight_normalized,
        "artifacts": [{"shot_id": row["shot_id"], "artifact_id": row["artifact_id"]} for row in approved],
        "generated_image_urls": generated_urls,
    }
    save_state(offer_id, state)

    try:
        save_resp = post(edit_path, {
            "commonCollectBoxDetailId": detail_id,
            "editCommonCollectBoxDetail": updated,
            "ossMd5": oss_md5,
        })
    except Exception as exc:
        content["miaoshou_generated_images_write"].update({"status": "failed", "error": str(exc)[:1000]})
        save_state(offer_id, state)
        raise
    if save_resp.get("result") != "success":
        content["miaoshou_generated_images_write"].update({"status": "failed", "error": str(save_resp.get("message") or save_resp.get("code") or "write failed")})
        save_state(offer_id, state)
        raise RuntimeError(f"妙手图片写入失败: {save_resp.get('code')} {save_resp.get('message', '')}")

    verify_resp = post(detail_path, {"commonCollectBoxDetailId": detail_id})
    if verify_resp.get("result") != "success":
        raise RuntimeError("妙手图片写入后验证读取失败")
    verified = ((verify_resp.get("data") or {}).get("editCommonCollectBoxDetail") or {})
    current_skus = current.get("skuMap") or {}
    verified_skus = verified.get("skuMap") or {}
    sku_structure_unchanged = set(verified_skus) == set(current_skus) and all(
        {
            key: value
            for key, value in dict(verified_skus.get(sku_key) or {}).items()
            if key != "weight"
        }
        == {
            key: value
            for key, value in dict(current_skus.get(sku_key) or {}).items()
            if key != "weight"
        }
        for sku_key in current_skus
    )
    checks = {
        "images": list(verified.get("imgUrls") or []) == image_urls,
        "generated_description_images": all(url in str(verified.get("notes") or "") for url in generated_urls),
        "weight": abs(float(verified.get("weight") or 0) - float(updated.get("weight") or 0)) < 0.0001,
        "sku_weights": all(
            abs(float((row or {}).get("weight") or 0) - float(updated.get("weight") or 0)) < 0.0001
            for row in (verified.get("skuMap") or {}).values()
        ),
        "title_unchanged": str(verified.get("title") or "") == str(current.get("title") or ""),
        "sku_map_structure_unchanged": sku_structure_unchanged,
    }
    content["miaoshou_generated_images_write"].update({
        "status": "verified" if all(checks.values()) else "verification_failed",
        "finished_at": _now(),
        "checks": checks,
        "written_image_count": len(image_urls),
    })
    save_state(offer_id, state)
    return {
        "ok": all(checks.values()),
        "written_to_miaoshou": True,
        "collect_box_id": str(detail_id),
        "written_image_count": len(image_urls),
        "artifacts": [{"shot_id": row["shot_id"], "artifact_id": row["artifact_id"]} for row in approved],
        "checks": checks,
        "claimed": False,
        "published": False,
    }


def save_content_package_review(offer_id_or_url: str, review: dict[str, Any]) -> dict[str, Any]:
    """Persist explicit human content approvals; it never starts generation or writes Miaoshou."""
    offer_id = resolve_offer_key(offer_id_or_url)
    state = load_state(offer_id)
    if "expected_revision" in review:
        expected_revision = review.get("expected_revision")
        if (
            type(expected_revision) is not int
            or expected_revision != max(0, int(state.get("_revision") or 0))
        ):
            raise ValueError("content review is stale; refresh before saving source decisions")
    content = state.setdefault("content_package", {})
    previous_recipe = _content_recipe_signature(content)
    previous_planning_recipe = _planning_recipe_signature(content)
    previous_fact_card_approved = bool(content.get("fact_card_approved"))
    if "content_strategy" in review:
        strategy = str(review.get("content_strategy") or "").strip()
        if strategy not in CONTENT_STRATEGIES:
            raise ValueError(
                "content_strategy must be source_only or ai_assisted"
            )
        content["content_strategy"] = strategy
    else:
        content.setdefault("content_strategy", "ai_assisted")
    if "fact_card_approved" in review:
        content["fact_card_approved"] = bool(review.get("fact_card_approved"))
    fact_approval_changed = (
        bool(content.get("fact_card_approved"))
        != previous_fact_card_approved
    )
    if "planning_scope_approved" in review:
        content["planning_scope_approved"] = bool(
            review.get("planning_scope_approved")
        )
    ai_assisted = _content_strategy(content) == "ai_assisted"
    if ai_assisted:
        _enable_experience_recipe_review(content)
    if (
        not ai_assisted
        and "suite_approved" in review
        and not isinstance(review.get("storyboard_reviews"), dict)
    ):
        content["suite_approved"] = bool(review.get("suite_approved"))
    if "note" in review:
        content["note"] = str(review.get("note") or "").strip()[:2000]
    raw_customization = review.get("suite_customization")
    if ai_assisted and isinstance(raw_customization, dict):
        raw_counts = raw_customization.get("type_counts") if isinstance(raw_customization.get("type_counts"), dict) else {}
        clean_counts = {}
        for shot_type in ("white_bg", "scene", "selling_point", "macro_detail", "size_card"):
            raw_count = raw_counts.get(shot_type)
            if isinstance(raw_count, (int, float, str)) and str(raw_count).strip().isdigit():
                clean_counts[shot_type] = max(0, min(int(raw_count), 6 if shot_type != "size_card" else 1))
        size_card = raw_customization.get("size_card") if isinstance(raw_customization.get("size_card"), dict) else {}
        content["suite_customization"] = {
            "type_counts": clean_counts,
            "size_card": {
                "enabled": bool(size_card.get("enabled")) or bool(clean_counts.get("size_card")),
                "dimensions": str(size_card.get("dimensions") or "").strip()[:240],
                "confirmed": bool(size_card.get("confirmed")),
            },
        }
    package_dir = _content_package_dir(str(content.get("collect_box_id") or ""))
    review_package = _load_json(package_dir / "review_package.json") if package_dir else {}
    if package_dir and _apply_manual_storyboard_edits(content, review_package):
        _write_json_atomic(package_dir / "review_package.json", review_package)
    plan = review_package.get("plan") if isinstance(review_package.get("plan"), dict) else {}
    suite = plan.get("suite") if isinstance(plan.get("suite"), dict) else {}
    allowed_storyboard_ids = {
        str(item.get("id") or "")
        for item in (suite.get("items") or [])
        if isinstance(item, dict) and item.get("selected") and str(item.get("id") or "")
    }
    raw_storyboard_reviews = review.get("storyboard_reviews")
    if (
        ai_assisted
        and str(content.get("planning_review_mode") or "")
        != EXPERIENCE_RECIPE_REVIEW_MODE
        and isinstance(raw_storyboard_reviews, dict)
    ):
        storyboard_reviews: dict[str, dict[str, Any]] = {}
        for shot_id in allowed_storyboard_ids:
            row = raw_storyboard_reviews.get(shot_id)
            if not isinstance(row, dict):
                storyboard_reviews[shot_id] = {"decision": "pending", "note": ""}
                continue
            decision = str(row.get("decision") or "pending")
            if decision not in {"pending", "approved", "revise"}:
                decision = "pending"
            storyboard_reviews[shot_id] = {
                "decision": decision,
                "note": str(row.get("note") or "").strip()[:1200],
                "reviewed_at": _now(),
            }
        content["storyboard_reviews"] = storyboard_reviews
        content["suite_approved"] = bool(
            allowed_storyboard_ids
            and all(
                storyboard_reviews.get(shot_id, {}).get("decision") == "approved"
                for shot_id in allowed_storyboard_ids
            )
        )
    collect_box = review_package.get("collect_box") if isinstance(review_package.get("collect_box"), dict) else {}
    source = (
        _source_summary(offer_id)
        if (
            "identity_reference_urls" in review
            or "image_actions" in review
            or "video_action" in review
            or "video_url" in review
        )
        else {}
    )
    allowed_rows = {
        str(row.get("url") or "").strip(): row
        for row in (source.get("images") or [])
        if (
            isinstance(row, dict)
            and isinstance(row.get("url"), str)
            and str(row.get("url") or "").strip()
        )
    }
    allowed_refs = set(_identity_reference_image_urls(source, collect_box))
    refs: list[str] | None = None
    requested_primary = ""
    if "identity_reference_urls" in review:
        raw_refs = review.get("identity_reference_urls")
        if type(raw_refs) is not list:
            raise ValueError("identity_reference_urls must be a list")
        refs = []
        for url in raw_refs:
            if type(url) is not str:
                raise ValueError("identity references must be current source image URLs")
            clean_url = url.strip()
            if (
                not clean_url
                or clean_url not in allowed_refs
                or clean_url in refs
            ):
                raise ValueError("identity references must be unique current source images")
            refs.append(clean_url)
        raw_primary = review.get("primary_identity_url", "")
        if type(raw_primary) is not str:
            raise ValueError("primary identity reference must be a source image URL")
        requested_primary = raw_primary.strip()
        if requested_primary and requested_primary not in refs:
            raise ValueError("primary identity reference must belong to identity references")
    elif "primary_identity_url" in review:
        raise ValueError("primary identity reference requires identity references")
    if "image_actions" in review:
        requested_actions = review.get("image_actions")
        if type(requested_actions) is not list:
            raise ValueError("image_actions must be a list")
        if len(requested_actions) != len(allowed_rows):
            raise ValueError("source image decisions must include every current source image exactly once")
        actions_by_url = {}
        for row in requested_actions:
            if not isinstance(row, dict):
                raise ValueError("source image decisions must contain objects only")
            url = str(row.get("output_url") or row.get("url") or "").strip()
            action = str(row.get("action") or "review").strip()
            if (
                url not in allowed_rows
                or url in actions_by_url
                or action not in {"keep", "review", "remove"}
            ):
                raise ValueError("source image decisions must use current source images")
            actions_by_url[url] = {
                **allowed_rows[url],
                "url": url,
                "action": action,
                "note": str(row.get("note") or "").strip()[:1200],
            }
        if set(actions_by_url) != set(allowed_rows):
            raise ValueError("source image decisions must include every current source image")
        state["review"] = {
            **(state.get("review") if isinstance(state.get("review"), dict) else {}),
            "image_actions": [actions_by_url[url] for url in allowed_rows],
        }
        kept_urls = [url for url, row in actions_by_url.items() if row["action"] == "keep"]
        if refs is not None:
            if any(url not in kept_urls for url in refs):
                raise ValueError("identity references must be explicitly kept source images")
        requested_order = [
            str(url).strip() for url in (review.get("image_order") or [])
            if str(url).strip() in kept_urls
        ]
        state["review"]["image_order"] = list(dict.fromkeys(requested_order + kept_urls))
    if "video_action" in review or "video_url" in review:
        raw_video_action = review.get("video_action")
        if type(raw_video_action) is not str:
            raise ValueError("video_action must be keep, remove, or none")
        video_action = raw_video_action.strip()
        source_video_url = str(
            (source.get("video") or {}).get("url") or ""
        ).strip()
        if source_video_url:
            if video_action not in {"keep", "remove"}:
                raise ValueError("video_action must be keep or remove")
            if "video_url" in review:
                raw_video_url = review.get("video_url")
                if (
                    type(raw_video_url) is not str
                    or raw_video_url.strip() != source_video_url
                ):
                    raise ValueError(
                        "video_url must match the current collected source video"
                    )
        else:
            if video_action != "none":
                raise ValueError(
                    "video_action must be none when no source video exists"
                )
            if review.get("video_url") not in (None, ""):
                raise ValueError(
                    "video_url must be empty when no source video exists"
                )
        state_review = (
            state.get("review")
            if isinstance(state.get("review"), dict)
            else {}
        )
        state_review["video_action"] = video_action
        state_review["video_url"] = source_video_url
        state["review"] = state_review
    if refs is not None:
        content["identity_reference_urls"] = refs
        content["primary_identity_url"] = (
            requested_primary
            if requested_primary
            else (refs[0] if refs else "")
        )
    raw_decisions = review.get("asset_decisions")
    if isinstance(raw_decisions, dict):
        decisions = content.setdefault("asset_decisions", {})
        for artifact_id, row in raw_decisions.items():
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(artifact_id)) or not isinstance(row, dict):
                continue
            decision = str(row.get("decision") or "pending")
            if decision not in {"pending", "approved", "rework", "rejected"}:
                continue
            decisions[str(artifact_id)] = {
                "decision": decision,
                "note": str(row.get("note") or "").strip()[:1000],
                "reviewed_at": _now(),
            }
    if ai_assisted and not fact_approval_changed:
        _adopt_current_storyboard_recipe(content, review_package)
    elif ai_assisted:
        content["suite_approved"] = False
        content["storyboard_reviews"] = {}
        content.pop("storyboard_recipe_adopted_at", None)
        content.pop("storyboard_recipe_signature", None)
    current_recipe = _content_recipe_signature(content)
    if current_recipe != previous_recipe:
        pending_regeneration_shot_ids = [
            str(shot_id)
            for shot_id in (content.get("pending_regeneration_shot_ids") or [])
            if str(shot_id)
        ]
        planning_recipe_changed = (
            _planning_recipe_signature(content) != previous_planning_recipe
        )
        _invalidate_paid_image_state(content)
        if pending_regeneration_shot_ids and not planning_recipe_changed:
            content.pop("force_regenerate_all", None)
            content["pending_regeneration_shot_ids"] = (
                pending_regeneration_shot_ids
            )
        elif planning_recipe_changed:
            content.pop("pending_regeneration_shot_ids", None)
        content["suite_revision"] = max(0, int(content.get("suite_revision") or 0)) + 1
        content["recipe_changed_at"] = _now()
    else:
        content["suite_revision"] = max(1, int(content.get("suite_revision") or 1))
    content["updated_at"] = _now()
    save_state(offer_id, state)
    return content_package_summary(offer_id)


def finalize_content_package_review(
    offer_id_or_url: str,
    approval: dict[str, Any],
) -> dict[str, Any]:
    """Approve the exact current AI-assisted final image set.

    This is the missing terminal action for content stage 02.  It does not call
    a model, regenerate images, or write Miaoshou.  It only closes the review
    after every current source/generated image has an explicit decision and the
    retained set has one exact saved order.
    """

    offer_id = resolve_offer_key(offer_id_or_url)
    state = load_state(offer_id)
    expected_revision = approval.get("expected_revision")
    current_revision = max(0, int(state.get("_revision") or 0))
    if type(expected_revision) is not int or expected_revision != current_revision:
        raise ValueError("content approval is stale; refresh before approving")
    approved_by = str(approval.get("approved_by") or "").strip()
    if approved_by != "Kyle":
        raise ValueError("final content approval must be approved by Kyle")
    content = (
        state.get("content_package")
        if isinstance(state.get("content_package"), dict)
        else {}
    )
    if _content_strategy(content) != "ai_assisted":
        raise ValueError(
            "source-only content must use its source-only final approval action"
        )
    review = state.get("review") if isinstance(state.get("review"), dict) else {}
    miaoshou_ordered_image_urls = _verified_miaoshou_final_images(review, content)
    approval_payload = {
        "schema_version": "ai-assisted-final-content-approval/v1",
        "status": "approved",
        "approved_by": approved_by,
        "image_order": list(review.get("image_order") or []),
        "miaoshou_ordered_image_urls": miaoshou_ordered_image_urls,
        "video_action": str(review.get("video_action") or "none"),
        "asset_decisions": content.get("asset_decisions") or {},
        "generated_image_decisions": (
            content.get("generated_image_miaoshou_decisions") or {}
        ),
    }
    approval_digest = hashlib.sha256(
        json.dumps(
            approval_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    content["suite_approved"] = True
    content["storyboard_recipe_adopted_at"] = _now()
    content["storyboard_recipe_signature"] = _planning_recipe_signature(content)
    content["final_content_approval"] = {
        **approval_payload,
        "approval_digest": approval_digest,
        "approved_at": _now(),
    }
    content["updated_at"] = _now()
    state["content_package"] = content
    save_state(offer_id, state)
    return content_package_summary(offer_id)


def save_source_only_review(
    offer_id_or_url: str, review: dict[str, Any]
) -> dict[str, Any]:
    """Save a source-only draft or explicitly approve its exact final content."""

    offer_id = resolve_offer_key(offer_id_or_url)
    state = load_state(offer_id)
    expected_revision_raw = review.get("expected_revision")
    expected_revision = (
        expected_revision_raw
        if type(expected_revision_raw) is int
        else -1
    )
    current_revision = max(0, int(state.get("_revision") or 0))
    if expected_revision != current_revision:
        raise ValueError(
            "source image draft is stale; refresh before saving selection and order"
        )
    confirm_value = review.get("confirm_final_content_approval")
    if confirm_value is not None and type(confirm_value) is not bool:
        raise ValueError(
            "confirm_final_content_approval must be a literal boolean"
        )
    approve_final = confirm_value is True
    approved_by = str(review.get("approved_by") or "").strip()
    if approve_final and approved_by != "Kyle":
        raise ValueError("final source-only content approval must be approved by Kyle")
    if not approve_final and approved_by:
        raise ValueError(
            "approved_by is only allowed with explicit final content approval"
        )

    source = _source_summary(offer_id)
    source_rows = [
        dict(row)
        for row in (source.get("images") or [])
        if isinstance(row, dict) and str(row.get("url") or "").strip()
    ]
    allowed_by_url = {
        str(row.get("url") or "").strip(): row for row in source_rows
    }
    if not allowed_by_url:
        raise ValueError("this offer has no collected source images")

    raw_actions = review.get("image_actions")
    if not isinstance(raw_actions, list):
        raise ValueError("image_actions must contain every collected source image")
    submitted_by_url: dict[str, dict[str, Any]] = {}
    for row in raw_actions:
        if not isinstance(row, dict):
            raise ValueError("each source image decision must be an object")
        url = str(row.get("url") or row.get("output_url") or "").strip()
        if url not in allowed_by_url:
            raise ValueError(
                "source image decisions may only reference this offer's collected images"
            )
        if url in submitted_by_url:
            raise ValueError("source image decisions cannot contain duplicate URLs")
        action = str(row.get("action") or "review").strip()
        if action not in {"review", "keep", "remove"}:
            raise ValueError("source image action must be review, keep, or remove")
        if action == "keep" and not url.startswith("https://"):
            raise ValueError("kept source images must use HTTPS")
        submitted_by_url[url] = {
            "url": url,
            "kind": str(allowed_by_url[url].get("kind") or "main"),
            "action": action,
            "note": str(row.get("note") or "").strip()[:1200],
        }
    if set(submitted_by_url) != set(allowed_by_url):
        raise ValueError(
            "source image list changed; refresh before saving selection and order"
        )

    clean_actions = [submitted_by_url[url] for url in allowed_by_url]
    kept_urls = [
        row["url"] for row in clean_actions if row["action"] == "keep"
    ]
    raw_order = review.get("image_order")
    if not isinstance(raw_order, list):
        raise ValueError("image_order must be a list")
    clean_order = [str(url).strip() for url in raw_order if str(url).strip()]
    if len(clean_order) != len(set(clean_order)):
        raise ValueError("source image order cannot contain duplicate URLs")
    if set(clean_order) != set(kept_urls):
        raise ValueError(
            "source image order must contain every kept image exactly once"
        )

    current_review = (
        dict(state.get("review"))
        if isinstance(state.get("review"), dict)
        else {}
    )
    current_review["image_actions"] = clean_actions
    current_review["image_order"] = clean_order
    video_url = str((source.get("video") or {}).get("url") or "").strip()
    if video_url:
        video_action = str(review.get("video_action") or "remove").strip()
        if video_action not in {"keep", "remove"}:
            raise ValueError("video_action must be keep or remove")
        current_review["video_action"] = video_action
        current_review["video_url"] = video_url
    else:
        current_review["video_action"] = "none"
        current_review["video_url"] = ""

    content = state.setdefault("content_package", {})
    _invalidate_paid_image_state(content)
    content["content_strategy"] = "source_only"
    content.pop("force_regenerate_all", None)
    content.pop("pending_regeneration_shot_ids", None)
    selection = _source_only_selection(current_review)
    content["source_only_review_status"] = (
        "ready_for_final_approval" if selection["ready"] else "draft"
    )
    content["source_only_review_signature"] = _source_only_review_signature(
        clean_actions, clean_order
    )
    content["source_only_reviewed_at"] = _now()
    content["source_only_external_writes"] = []
    if approve_final:
        if not selection["ready"]:
            raise ValueError(
                "source-only final content cannot be approved until every source "
                "image, order, and video decision is complete"
            )
        review_signature = str(content["source_only_review_signature"])
        approval_digest = source_only_final_approval_digest(
            review_signature=review_signature,
            video_action=str(current_review.get("video_action") or ""),
            video_url=str(current_review.get("video_url") or ""),
            approved_by=approved_by,
        )
        video_identity_digest = hashlib.sha256(
            str(current_review.get("video_url") or "").strip().encode("utf-8")
        ).hexdigest()
        content["fact_card_approved"] = True
        content["planning_scope_approved"] = True
        content["source_only_review_status"] = "approved"
        content["source_only_final_approval"] = {
            "schema_version": SOURCE_ONLY_FINAL_APPROVAL_SCHEMA,
            "status": "approved",
            "approved_by": approved_by,
            "source_only_review_signature": review_signature,
            "video_action": str(current_review.get("video_action") or ""),
            "video_identity_digest": f"sha256:{video_identity_digest}",
            "approval_digest": approval_digest,
            "approved_at": _now(),
        }
    else:
        content["fact_card_approved"] = False
        content["planning_scope_approved"] = False
        content.pop("source_only_final_approval", None)
    state["review"] = current_review
    save_state(offer_id, state)
    return build_preview(offer_id)


def content_package_file(offer_id_or_url: str, *, artifact_id: str = "", report: bool = False) -> Path | None:
    """Resolve a safe local report/image path for the Treasury HTTP server."""
    summary = content_package_summary(offer_id_or_url)
    package_dir = _content_package_dir(str(summary.get("collect_box_id") or ""))
    if package_dir is None:
        return None
    if report:
        candidate = package_dir / "review_report.html"
        return candidate if candidate.is_file() else None
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", artifact_id):
        return None
    candidate = package_dir / "generated" / f"{artifact_id}.png"
    return candidate if candidate.is_file() else None


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


def _enforce_min_profit_price(
    list_price: float,
    *,
    price_step: float,
    profit_cny_for_list_price,
) -> tuple[float, bool]:
    """Raise a rounded list price until the post-discount profit reaches the CNY floor."""
    candidate = _round_up(float(list_price), price_step)
    adjusted = False
    for _ in range(100_000):
        if float(profit_cny_for_list_price(candidate)) + 1e-9 >= MIN_ESTIMATED_PROFIT_CNY:
            return (int(candidate) if price_step >= 1 else round(candidate, 2)), adjusted
        candidate += price_step
        adjusted = True
    raise RuntimeError("unable to satisfy the minimum estimated profit price guard")


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

    def effective_profit_for_list_price(candidate: float) -> tuple[float, float]:
        effective_sale = round(candidate * (1 - DISCOUNT_RESERVE_RATE), 2)
        effective_profit = effective_sale - (
            goods_cost_local
            + logistics_local
            + (effective_sale * rule.commission_rate)
            + (effective_sale * rule.transaction_rate)
            + _capped_fee(effective_sale, rule.extra_rate, rule.extra_cap_local)[0]
            + (effective_sale * rule.affiliate_rate)
            + (effective_sale * rule.ad_rate)
            + (effective_sale * rule.creator_rate)
            + (effective_sale * rule.seller_tax_rate)
            + rule.fixed_fee_local
        )
        return effective_sale, effective_profit

    list_price, min_profit_adjusted = _enforce_min_profit_price(
        list_price,
        price_step=rule.rounding_step,
        profit_cny_for_list_price=lambda candidate: effective_profit_for_list_price(candidate)[1] * rule.cny_per_local,
    )
    effective_sale_local, effective_profit_local = effective_profit_for_list_price(list_price)
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
        "minimum_profit_cny": MIN_ESTIMATED_PROFIT_CNY,
        "min_profit_adjusted": min_profit_adjusted,
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
        "notes": "SEA reverse pricing; seller absorbs tax; 35% backend discount reserve and CNY 5 minimum estimated-profit guard included.",
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

    def effective_profit_for_list_price(candidate: float) -> tuple[float, float]:
        effective_sale = round(candidate * (1 - MX_RULE["discount_reserve_rate"]), 2)
        effective_profit = effective_sale - (
            goods_cost_local
            + hidden_shipping_local
            + (effective_sale * MX_RULE["import_tax_rate"])
            + (effective_sale * MX_RULE["commission_rate"])
            + (effective_sale * MX_RULE["sfp_rate"])
            + (effective_sale * MX_RULE["affiliate_rate"])
            + (effective_sale * MX_RULE["ad_rate"])
            + MX_RULE["per_item_fee_local"]
        )
        return effective_sale, effective_profit

    list_price, min_profit_adjusted = _enforce_min_profit_price(
        list_price,
        price_step=1.0,
        profit_cny_for_list_price=lambda candidate: effective_profit_for_list_price(candidate)[1] / MX_RULE["cny_per_local"],
    )
    effective_sale_local, effective_profit_local = effective_profit_for_list_price(list_price)
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
        "minimum_profit_cny": MIN_ESTIMATED_PROFIT_CNY,
        "min_profit_adjusted": min_profit_adjusted,
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
        "notes": "MX includes import tax, SFP, affiliate, ad, 30% list discount reserve, and a CNY 5 minimum estimated-profit guard.",
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

    def effective_profit_for_list_price(candidate: float) -> tuple[float, float]:
        effective_sale = round(candidate * (1 - GB_RULE["discount_reserve_rate"]), 2)
        effective_profit = effective_sale - (
            goods_cost_local
            + shipping_local
            + (effective_sale * GB_RULE["vat_rate"])
            + (effective_sale * GB_RULE["commission_rate"])
            + (effective_sale * GB_RULE["smart_promo_rate"])
            + (effective_sale * GB_RULE["affiliate_rate"])
            + (effective_sale * GB_RULE["ad_rate"])
        )
        return effective_sale, effective_profit

    list_price, min_profit_adjusted = _enforce_min_profit_price(
        list_price,
        price_step=1.0,
        profit_cny_for_list_price=lambda candidate: effective_profit_for_list_price(candidate)[1] * GB_RULE["cny_per_local"],
    )
    effective_sale_local, effective_profit_local = effective_profit_for_list_price(list_price)
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
        "minimum_profit_cny": MIN_ESTIMATED_PROFIT_CNY,
        "min_profit_adjusted": min_profit_adjusted,
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
        "notes": "GB includes VAT-effective deduction, commission, smart promo, ad, 25% list discount reserve, and a CNY 5 minimum estimated-profit guard.",
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
                (
                    f"已为最低 CNY {row.get('minimum_profit_cny', MIN_ESTIMATED_PROFIT_CNY):.2f} 利润上调挂牌价"
                    if row.get("min_profit_adjusted")
                    else f"最低预计利润 CNY {row.get('minimum_profit_cny', MIN_ESTIMATED_PROFIT_CNY):.2f}"
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
                        (
                            f"已为最低 CNY {mx.get('minimum_profit_cny', MIN_ESTIMATED_PROFIT_CNY):.2f} 利润上调挂牌价"
                            if mx.get("min_profit_adjusted")
                            else f"最低预计利润 CNY {mx.get('minimum_profit_cny', MIN_ESTIMATED_PROFIT_CNY):.2f}"
                        ),
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
                        (
                            f"已为最低 CNY {uk.get('minimum_profit_cny', MIN_ESTIMATED_PROFIT_CNY):.2f} 利润上调挂牌价"
                            if uk.get("min_profit_adjusted")
                            else f"最低预计利润 CNY {uk.get('minimum_profit_cny', MIN_ESTIMATED_PROFIT_CNY):.2f}"
                        ),
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
            "requested_common_collect_id": precollect.get(
                "requested_common_collect_id"
            ),
            "resolved_common_collect_id": precollect.get(
                "resolved_common_collect_id"
            ),
            "resolved_duplicate": precollect.get("resolved_duplicate") is True,
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
    if review.get("fx_rates"):
        fx_rates = merge_fx_rates(review.get("fx_rates"))
    else:
        try:
            from modules.sourcing.fx_rates import get_exchange_rates

            live = get_exchange_rates(force_refresh=False)
            fx_rates = merge_fx_rates(live.get("rates") if live.get("live") or live.get("cached") else None)
        except Exception:
            fx_rates = merge_fx_rates(None)
    miaoshou_draft = _load_json(STATE_DIR / f"{offer_id}_miaoshou_draft.json") or {}
    tiktok_claim = _load_json(STATE_DIR / f"{offer_id}_tiktok_claim.json") or {}
    site_drafts = _load_json(STATE_DIR / f"{offer_id}_site_drafts.json") or {}
    content = content_package_summary(offer_id)
    source_sku_keys = [str(row.get("key") or row.get("name") or "") for row in source.get("skus") or [] if str(row.get("key") or row.get("name") or "")]
    saved_sku_keys = review.get("selected_sku_keys") if isinstance(review.get("selected_sku_keys"), list) else None
    review_payload = {
        "selected_sites": review.get("selected_sites") or [m["id"] for m in SEA_MARKETS if m.get("enabled")],
        "title": review.get("title") or source.get("title_recommended") or source.get("title_source"),
        "seller_sku": review.get("seller_sku") or source.get("seller_sku"),
        "category": review.get("category") or source.get("category"),
        "cost_cny": cost,
        "weight_kg": weight,
        "package_cm": dims,
        "video_action": review.get("video_action") or source.get("video", {}).get("action"),
        "video_url": review.get("video_url") or source.get("video", {}).get("url"),
        "support_cod": True,
        "image_actions": review.get("image_actions") or source.get("images"),
        "generated_image_actions": content.get("generated_review_images") or [],
        "image_order": list(review.get("image_order") or []),
        "overseas_image_candidates": overseas_images,
        "image_generation_requests": review.get("image_generation_requests") or [],
        "selected_sku_keys": saved_sku_keys if saved_sku_keys is not None else source_sku_keys,
        "sku_label_overrides": dict(
            review.get("sku_label_overrides")
            if isinstance(review.get("sku_label_overrides"), dict)
            else {}
        ),
        "fields_locked": bool(review.get("fields_locked")),
        "fx_rates": fx_rates,
    }
    workflow = _product_workflow_summary(
        source=source,
        review=review_payload,
        content=content,
        miaoshou_draft=miaoshou_draft,
        tiktok_claim=tiktok_claim,
        site_drafts=site_drafts,
    )
    from domains.product_operations.product_facts import (
        build_product_facts_snapshot,
    )

    product_facts = build_product_facts_snapshot(
        product_id=offer_id,
        source=source,
        review=review,
    )
    workflow["product_facts_ready"] = product_facts.ready
    workflow["product_fact_blockers"] = list(product_facts.blockers)
    workflow["commercial_ready"] = bool(
        workflow.get("commercial_ready") and product_facts.ready
    )
    if product_facts.blockers:
        workflow["blockers"] = list(
            dict.fromkeys(
                list(workflow.get("blockers") or []) + list(product_facts.blockers)
            )
        )
        commercial_step = next(
            (
                step
                for step in workflow.get("steps") or []
                if step.get("id") == "commercial"
            ),
            None,
        )
        if commercial_step is not None:
            commercial_step["status"] = (
                "current" if workflow.get("image_review_ready") else "pending"
            )
        if workflow.get("current_stage") in {"commercial", "miaoshou", "channels"}:
            workflow["current_stage"] = "commercial"
            if commercial_step is not None:
                workflow["current_label"] = commercial_step.get("label")

    return {
        "ok": True,
        "mode": "first_review_no_model_call",
        "offer_id": offer_id,
        "revision": max(0, int(state.get("_revision") or 0)),
        "source": source,
        "review": review_payload,
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
            "change_summary": miaoshou_draft.get("change_summary") or {},
            "blockers": miaoshou_draft.get("blockers") or [],
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
        "content_package": content,
        "product_facts": product_facts.payload(),
        "workflow": workflow,
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
    saved_review = state.get("review")
    current = dict(saved_review) if isinstance(saved_review, dict) else {}
    updates = dict(review or {})
    supersede = updates.pop("supersede", False)
    expected_revision = updates.pop("expected_revision", None)
    supersede_reason = str(updates.pop("supersede_reason", "") or "").strip()
    product_approval = (
        state.get("product_approval")
        if isinstance(state.get("product_approval"), dict)
        else {}
    )
    was_formally_locked = bool(current.get("fields_locked")) or str(
        product_approval.get("status") or ""
    ).strip().lower() == "approved"
    approval_bound_changes = {
        field
        for field in _APPROVAL_BOUND_REVIEW_FIELDS
        if field in updates and updates.get(field) != current.get(field)
    }
    if (
        was_formally_locked
        and "fields_locked" in updates
        and updates.get("fields_locked") is not True
    ):
        approval_bound_changes.add("fields_locked")
    if was_formally_locked and approval_bound_changes:
        if supersede is not True:
            raise ValueError(
                "approved product facts are locked; commercial changes require supersede=true and expected_revision"
            )
        if isinstance(expected_revision, bool):
            clean_expected_revision = None
        elif isinstance(expected_revision, int):
            clean_expected_revision = expected_revision
        elif (
            isinstance(expected_revision, str)
            and expected_revision.strip().isdigit()
        ):
            clean_expected_revision = int(expected_revision.strip())
        else:
            clean_expected_revision = None
        current_revision = max(0, int(state.get("_revision") or 0))
        if clean_expected_revision is None:
            raise ValueError(
                "expected_revision is required to supersede approved product facts"
            )
        if clean_expected_revision != current_revision:
            raise ValueError(
                "expected_revision is stale; refresh before superseding approved product facts"
            )
        current.update(updates)
        current["fields_locked"] = False
        superseded_at = _now()
        prior_approval_id = str(product_approval.get("approval_id") or "").strip()
        if product_approval:
            state["product_approval"] = {
                **product_approval,
                "status": "superseded",
                "superseded_at": superseded_at,
                "superseded_by": "legacy_save_review",
                "superseded_revision": clean_expected_revision,
                "superseded_fields": sorted(approval_bound_changes),
            }
        supersessions = list(state.get("commercial_supersessions") or [])
        supersessions.append(
            {
                "source": "legacy_save_review",
                "status": "superseded",
                "expected_revision": clean_expected_revision,
                "changed_fields": sorted(approval_bound_changes),
                "reason": supersede_reason,
                "superseded_at": superseded_at,
                "prior_approval_id": prior_approval_id or None,
            }
        )
        state["commercial_supersessions"] = supersessions
    else:
        current.update(updates)
        if was_formally_locked:
            current["fields_locked"] = True
    source = _source_summary(offer_id)
    allowed_sku_keys = {
        str(row.get("key") or row.get("name") or "")
        for row in source.get("skus") or []
        if str(row.get("key") or row.get("name") or "")
    }
    raw_selected_skus = current.get("selected_sku_keys")
    if isinstance(raw_selected_skus, list):
        current["selected_sku_keys"] = [
            str(value) for value in raw_selected_skus
            if str(value) in allowed_sku_keys
        ]
    if allowed_sku_keys and not current.get("selected_sku_keys"):
        raise ValueError("至少保留一个商品规格")
    selected_image_urls: list[str] = []
    for row in current.get("image_actions") or []:
        if str((row or {}).get("action") or "review") != "keep":
            continue
        url = str((row or {}).get("output_url") or (row or {}).get("url") or "").strip()
        if url and url not in selected_image_urls:
            selected_image_urls.append(url)
    content = state.get("content_package") if isinstance(state.get("content_package"), dict) else {}
    package_dir = _content_package_dir(str(content.get("collect_box_id") or ""))
    for row in _generated_review_images(offer_id, content, package_dir):
        if str(row.get("miaoshou_action") or "review") != "keep":
            continue
        url = str(row.get("url") or "").strip()
        if url and url not in selected_image_urls:
            selected_image_urls.append(url)
    requested_order = [
        str(url).strip()
        for url in (current.get("image_order") or [])
        if str(url).strip()
    ]
    current["image_order"] = list(dict.fromkeys(
        [url for url in requested_order if url in selected_image_urls]
        + selected_image_urls
    ))
    if current.get("fields_locked"):
        content = content_package_summary(offer_id)
        workflow = _product_workflow_summary(
            source=source,
            review=current,
            content=content,
            miaoshou_draft=_load_json(STATE_DIR / f"{offer_id}_miaoshou_draft.json") or {},
            tiktok_claim=_load_json(STATE_DIR / f"{offer_id}_tiktok_claim.json") or {},
            site_drafts=_load_json(STATE_DIR / f"{offer_id}_site_drafts.json") or {},
        )
        prerequisite_failures = []
        if not workflow["content_ready"]:
            prerequisite_failures.append("内容与图片配方尚未审核通过")
        if not workflow["generation_ready"]:
            prerequisite_failures.append("整套图片尚未生成完成")
        if not workflow["image_review_ready"]:
            prerequisite_failures.append("图片审核尚未完成")
        prerequisite_failures.extend(workflow.get("blockers") or [] if workflow.get("current_stage") == "commercial" else [])
        if not was_formally_locked:
            from domains.product_operations.product_facts import (
                build_product_facts_snapshot,
            )

            product_facts = build_product_facts_snapshot(
                product_id=offer_id,
                source=source,
                review=current,
            )
            prerequisite_failures.extend(product_facts.blockers)
        if prerequisite_failures:
            raise ValueError("暂时不能锁定发布信息: " + "; ".join(dict.fromkeys(prerequisite_failures)))
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


def _next_seller_sku(requested_count: int = 1) -> str:
    """Return the first free contiguous Seller-SKU block.

    The legacy allocator only inspected published TikTok rows, so it could
    hand out a base number that another workbench had already locked or that a
    verified TikTok claim had expanded into variant numbers. The allocator is
    still read-only, but now treats those local facts as occupied too.
    """
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

    from domains.product_operations import reservations_from_documents

    states: dict[str, dict[str, Any]] = {}
    claims: dict[str, dict[str, Any]] = {}
    if STATE_DIR.is_dir():
        for path in STATE_DIR.glob("*.json"):
            if not path.stem.isdigit():
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                states[path.stem] = value
        for path in STATE_DIR.glob("*_tiktok_claim.json"):
            offer_id = path.name.removesuffix("_tiktok_claim.json")
            if not offer_id.isdigit():
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                claims[offer_id] = value
    reserved = {
        int(fact.seller_sku)
        for fact in reservations_from_documents(states, claims)
        if fact.seller_sku.isdigit()
    }
    occupied = {*values, *reserved}
    width = max(1, int(requested_count or 1))
    start = max(values) + 1
    while start + width - 1 <= 9999:
        candidate = range(start, start + width)
        if all(number not in occupied for number in candidate):
            return f"{start:04d}"
        start += 1
    raise RuntimeError("没有足够的连续 Seller SKU 编号可供分配")


def _sequential_sku_numbers(sku_map: dict[str, Any], base_sku: str) -> dict[str, str]:
    digits = "".join(ch for ch in str(base_sku or "") if ch.isdigit())
    if not digits:
        raise RuntimeError("缺少可连续编号的平台 SKU 起始值")
    start = int(digits[-4:])
    return {
        key: f"{start + index:04d}"[-4:]
        for index, key in enumerate(sku_map)
    }


def _strict_selected_miaoshou_sku_map(
    sku_map: Any,
    draft: dict[str, Any],
    *,
    region: str,
) -> dict[str, Any]:
    """Validate selected variants by stable itemNum, never mutable ERP map keys."""

    selected_keys = [
        str(value).strip()
        for value in (draft.get("selectedSkuKeys") or ())
    ]
    selected_count = len(selected_keys)
    if selected_count < 1:
        raise RuntimeError(f"{region} immutable plan has no selected SKU keys")
    if not all(selected_keys) or len(set(selected_keys)) != selected_count:
        raise RuntimeError(f"{region} immutable plan has invalid selected SKU keys")
    base_item_num = str(draft.get("itemNum") or "").strip()
    if not base_item_num.isdigit():
        raise RuntimeError(f"{region} immutable plan has invalid seller SKU")
    expected_item_nums = {
        str((int(base_item_num) + offset) % 10000).zfill(4)
        for offset in range(selected_count)
    }
    if len(expected_item_nums) != selected_count:
        raise RuntimeError(f"{region} immutable plan has duplicate sequential SKU numbers")
    if not isinstance(sku_map, dict) or not sku_map:
        raise RuntimeError(f"{region} existing draft has no verifiable SKU map")
    if len(sku_map) != selected_count:
        raise RuntimeError(
            f"{region} existing draft SKU entry count does not match immutable plan"
        )
    actual_item_nums: list[str] = []
    retained: dict[str, Any] = {}
    for key, value in sku_map.items():
        if not isinstance(value, dict):
            raise RuntimeError(f"{region} existing draft has invalid SKU entry")
        item_num = str(value.get("itemNum") or "").strip()
        if not re.fullmatch(r"\d{4}", item_num):
            raise RuntimeError(
                f"{region} existing draft SKU itemNum is missing or not four digits"
            )
        actual_item_nums.append(item_num)
        retained[str(key)] = value
    if len(set(actual_item_nums)) != len(actual_item_nums):
        raise RuntimeError(f"{region} existing draft has duplicate SKU itemNum values")
    if set(actual_item_nums) != expected_item_nums:
        raise RuntimeError(
            f"{region} existing draft SKU itemNum set does not match immutable plan"
        )
    return retained


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
    # Miaoshou only permits one sub-site of the same global TikTok shop on a
    # collect-box detail.  A single "lively" detail therefore cannot safely
    # represent PH, MY, TH, VN, MX and GB: publishing the first site consumes
    # the only claimed detail and every later site reports "no data".
    #
    # Keep the commercial publish group in the key, but isolate each region
    # onto its own collect-box detail.  Different brands in the same country
    # also remain isolated because their publish groups differ.
    publish_group = _publish_group_for_target(target)
    region = str(target.get("region") or "").strip().upper()
    return f"{publish_group}:{region or 'DEFAULT'}"


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


def _claim_serial_number(
    group_targets: list[tuple[str, dict[str, Any], str]],
) -> int:
    """Return a stable Miaoshou copy number for one site-isolated detail."""

    market_positions = {
        str(market.get("id") or ""): index
        for index, market in enumerate(SEA_MARKETS, start=1)
    }
    positions = [
        market_positions.get(str(target_id), len(SEA_MARKETS) + 1)
        for target_id, _target, _shop_id in group_targets
    ]
    return min(positions) if positions else 1


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


def _miaoshou_platform_package_cm(
    draft: dict[str, Any],
) -> tuple[float, float, float]:
    """Map approved package facts to Miaoshou/TikTok transport constraints.

    The product fact remains exact (for example a 0.02 cm decal thickness).
    TikTok's site draft requires each transport dimension to be at least
    1 cm, so only the marketplace payload uses this conservative floor.
    """

    return tuple(
        max(1.0, float(draft.get(field) or 0))
        for field in ("packageLength", "packageWidth", "packageHeight")
    )


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
    package_length, package_width, package_height = _miaoshou_platform_package_cm(
        draft
    )
    info["packageLength"] = package_length
    info["packageWidth"] = package_width
    info["packageHeight"] = package_height
    info["mainImgVideoUrl"] = draft.get("mainImgVideoUrl") or ""
    info["mainImgAppVideoId"] = ""
    info["mainImgPlatformVideoId"] = ""
    info["isCodOpen"] = "1" if cod_enabled else "0"
    info["itemNum"] = str(draft.get("itemNum") or info.get("itemNum") or "")[-4:]
    _apply_audited_english_variant_labels(
        info,
        draft.get("skuLabelOverrides") or {},
    )

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


def _tiktok_category_id(preview: dict[str, Any]) -> str:
    """Resolve a TikTok leaf category from the approved product category."""

    review = preview.get("review") if isinstance(preview.get("review"), dict) else {}
    raw_category = review.get("category")
    values = (
        (
            raw_category.get("name"),
            raw_category.get("leaf"),
            raw_category.get("label"),
        )
        if isinstance(raw_category, dict)
        else (raw_category,)
    )
    for value in values:
        clean = re.sub(r"\s+", " ", str(value or "")).strip().casefold()
        compact = clean.replace(" ", "")
        for label, category_id in TIKTOK_CATEGORY_BY_PRODUCT_CATEGORY.items():
            normalized = label.casefold()
            if clean == normalized or compact == normalized.replace(" ", ""):
                return category_id
    raise RuntimeError(
        "No audited TikTok category mapping exists for the approved product category"
    )


def _audited_listing_title(
    state: dict[str, Any],
    *,
    channel: str,
    site: str,
    fallback: str,
) -> str:
    listing_copy = (
        state.get("listing_copy")
        if isinstance(state.get("listing_copy"), dict)
        else {}
    )
    status = str(listing_copy.get("status") or "")
    if status.startswith("superseded"):
        raise RuntimeError("Audited listing title candidates are stale")
    for row in listing_copy.get("candidates") or []:
        if not isinstance(row, dict):
            continue
        if (
            str(row.get("channel") or "").casefold() == channel.casefold()
            and str(row.get("site") or "").upper() == site.upper()
            and str(row.get("policy_check") or "") == "passed"
        ):
            title = _normalize_title(str(row.get("title") or ""))
            if title:
                return title
    return _normalize_title(fallback)


def _regional_listing_draft(
    draft: dict[str, Any],
    state: dict[str, Any],
    *,
    channel: str,
    site: str,
) -> dict[str, Any]:
    regional = dict(draft)
    regional["title"] = _audited_listing_title(
        state,
        channel=channel,
        site=site,
        fallback=str(draft.get("title") or ""),
    )
    return regional


def prepare_miaoshou_draft(offer_id_or_url: str) -> dict[str, Any]:
    """Build a local, no-write draft for the Miaoshou second review."""
    offer_id = resolve_offer_key(offer_id_or_url)
    preview = build_preview(offer_id)
    review = preview.get("review") or {}
    source = preview.get("source") or {}
    workflow = preview.get("workflow") or {}
    blockers: list[str] = []

    if not review.get("fields_locked"):
        blockers.append("第一轮审核尚未锁定")

    seller_sku = str(review.get("seller_sku") or "").strip()
    if not seller_sku:
        try:
            seller_sku = _next_seller_sku(
                len(review.get("selected_sku_keys") or ()) or 1
            )
        except (OSError, sqlite3.Error, RuntimeError) as exc:
            blockers.append(f"Seller SKU 自动分配失败: {exc}")

    selected_images: list[str] = []
    selected_source_images: list[str] = []
    selected_generated_images: list[str] = []
    optimization_items: list[dict[str, str]] = []
    for item in review.get("image_actions") or []:
        action = str(item.get("action") or "review")
        url = str(item.get("output_url") or item.get("url") or "").strip()
        if action == "keep" and url and url not in selected_images:
            selected_images.append(url)
            selected_source_images.append(url)
    for item in review.get("generated_image_actions") or []:
        action = str(item.get("miaoshou_action") or "review")
        url = str(item.get("url") or "").strip()
        if action == "keep" and url and url not in selected_images:
            selected_images.append(url)
            selected_generated_images.append(url)
    requested_image_order = [
        str(url).strip()
        for url in (review.get("image_order") or [])
        if str(url).strip()
    ]
    selected_images = list(dict.fromkeys(
        [url for url in requested_image_order if url in selected_images]
        + selected_images
    ))
    if len(selected_images) < 3:
        blockers.append("通过且去重后的商品图片少于 3 张")

    package = [float(x or 0) for x in (review.get("package_cm") or [0, 0, 0])]
    if len(package) != 3 or any(x <= 0 for x in package):
        blockers.append("商品尺寸不完整")
    weight = float(review.get("weight_kg") or 0)
    if weight <= 0:
        blockers.append("商品重量不完整")
    title = _normalize_title(str(review.get("title") or "").strip())
    if not title:
        blockers.append("英文标题为空")
    elif not _english_title_ready(title):
        blockers.append("英文标题必须包含英文字母且不能含中文")
    if workflow.get("content_required"):
        if not workflow.get("content_ready"):
            blockers.append("内容与图片配方尚未审核通过")
        if not workflow.get("generation_ready"):
            blockers.append("整套图片尚未生成完成")
        if not workflow.get("image_review_ready"):
            blockers.append("图片审核尚未完成")

    selected_sku_keys = [str(value) for value in review.get("selected_sku_keys") or [] if str(value)]
    if source.get("skus") and not selected_sku_keys:
        blockers.append("至少保留一个商品规格")

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
        "selectedSkuKeys": selected_sku_keys,
        "skuLabelOverrides": dict(
            review.get("sku_label_overrides")
            if isinstance(review.get("sku_label_overrides"), dict)
            else {}
        ),
        "selectedSites": list(review.get("selected_sites") or []),
        "supportCod": True,
    }
    change_summary = {
        "title": title,
        "seller_sku": seller_sku,
        "weight_g": round(weight * 1000),
        "package_cm": package,
        "source_image_count": len(selected_source_images),
        "generated_image_count": len(selected_generated_images),
        "final_image_count": len(selected_images),
        "selected_sku_count": len(selected_sku_keys) or len(source.get("skus") or []),
        "selected_site_count": len(review.get("selected_sites") or []),
    }
    result = {
        "ok": True,
        "ready": not blockers,
        "mode": "miaoshou_second_review_preparation_no_write",
        "offer_id": offer_id,
        "draft": draft,
        "blockers": blockers,
        "optimization_items": optimization_items,
        "change_summary": change_summary,
        "written_to_miaoshou": False,
        "updated_at": _now(),
    }
    _write_json_atomic(STATE_DIR / f"{offer_id}_miaoshou_draft.json", result)
    if seller_sku and seller_sku != review.get("seller_sku"):
        state = load_state(offer_id)
        state.setdefault("review", {})["seller_sku"] = seller_sku
        save_state(offer_id, state)
    return result


def _filter_miaoshou_variant_maps(
    detail: dict[str, Any],
    selected_sku_map: dict[str, Any],
) -> None:
    """Keep sale-property maps aligned with the selected SKU combinations."""

    selected_parts: list[set[str]] = [set(), set(), set()]
    for sku_key in selected_sku_map:
        parts = str(sku_key).strip(";").split(";")
        for index, part in enumerate(parts[:3]):
            if part:
                selected_parts[index].add(part)

    for index, field in enumerate(("colorMap", "sizeMap", "saleProp3Map")):
        current_map = detail.get(field)
        if not isinstance(current_map, dict) or not selected_parts[index]:
            continue
        detail[field] = {
            key: value
            for key, value in current_map.items()
            if str(key) in selected_parts[index]
        }


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
    current_sku_map = current.get("skuMap") or {}
    selected_sku_keys = {str(value) for value in draft.get("selectedSkuKeys") or [] if str(value)}
    if selected_sku_keys:
        current_sku_map = {
            key: value for key, value in current_sku_map.items()
            if str(key) in selected_sku_keys or str(key).strip(";") in selected_sku_keys
        }
        if not current_sku_map:
            raise RuntimeError("审核通过的规格无法与妙手当前 SKU 对应，请刷新商品后重新选择")
    updated_skus = {}
    sku_numbers = _sequential_sku_numbers(current_sku_map, draft["itemNum"])
    for key, value in current_sku_map.items():
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
    _filter_miaoshou_variant_maps(updated, updated_skus)

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
        "video_action": (
            str(verified.get("mainImgVideoUrl") or "")
            == str(draft.get("mainImgVideoUrl") or "")
        ),
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
    _write_json_atomic(STATE_DIR / f"{prepared['offer_id']}_miaoshou_draft.json", result)
    if result["verified"]:
        state = load_state(prepared["offer_id"])
        content = state.setdefault("content_package", {})
        content["miaoshou_ordered_images_write"] = {
            "status": "verified",
            "verified": True,
            "collect_box_id": str(detail_id),
            "ordered_image_urls": list(draft["imgUrls"]),
            "written_image_count": len(draft["imgUrls"]),
            "checks": {
                "images": checks["images"],
                "description_images": checks["description_images"],
            },
            "finished_at": _now(),
            "source": "formal_release_common_draft",
        }
        save_state(prepared["offer_id"], state)
    return result


def _miaoshou_post_retry(post, path: str, payload: dict[str, Any], action: str) -> dict[str, Any]:
    from modules.miaoshou.client import MiaoshouBusinessRejectedError

    response: dict[str, Any] = {}
    last_error = ""
    for attempt in range(5):
        try:
            response = post(path, payload)
        except MiaoshouBusinessRejectedError as exc:
            last_error = str(exc)
            rate_limited = str(exc.code or "").casefold() == (
                "platformqpsratelimit"
            ).casefold()
            if not rate_limited or attempt == 4:
                raise MiaoshouBusinessRejectedError(
                    f"{action}失败: {last_error}",
                    code=exc.code,
                ) from exc
            time.sleep(2 + attempt * 2)
            continue
        except RuntimeError as exc:
            last_error = str(exc)
            rate_limited = any(
                marker in last_error.lower()
                for marker in (
                    "platformqpsratelimit",
                    "频率超限",
                    "qps",
                    "rate limit",
                    "too many requests",
                )
            )
            if not rate_limited or attempt == 4:
                raise RuntimeError(f"{action}失败: {last_error}") from exc
            time.sleep(2 + attempt * 2)
            continue
        if response.get("result") == "success":
            return response
        if response.get("code") != "platformQpsRateLimit":
            break
        time.sleep(2 + attempt * 2)
    raise RuntimeError(
        f"{action}失败: {response.get('code')} {response.get('message', '') or last_error}"
    )


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
    _write_json_atomic(draft_path, draft_state)
    return {
        "ok": True,
        "offer_id": offer_id,
        "base_sku": base_sku,
        "sku_item_nums": list(sku_numbers.values()),
        "verified": all(checks.values()),
        "checks": checks,
    }


def sync_miaoshou_second_review(offer_id_or_url: str, *, post=None) -> dict[str, Any]:
    """Read Miaoshou back without rewriting an already approved fact snapshot."""
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
    draft_path = STATE_DIR / f"{offer_id}_miaoshou_draft.json"
    draft_state = _load_json(draft_path) or {}
    approval = (
        state.get("product_approval")
        if isinstance(state.get("product_approval"), dict)
        else {}
    )
    locked = bool(
        review.get("fields_locked")
        and str(approval.get("status") or "").casefold() == "approved"
    )
    checks: dict[str, bool] = {}
    if locked:
        expected_package = [
            float(value)
            for value in (review.get("package_cm") or ())
        ]
        expected_images = [
            str(value).strip()
            for value in (review.get("image_order") or ())
            if str(value).strip()
        ]
        expected_video = str(
            ((draft_state.get("draft") or {}).get("mainImgVideoUrl"))
            or ""
        )
        checks = {
            "title": (
                str(detail.get("title") or "").strip()
                == str(review.get("title") or "").strip()
            ),
            "seller_sku": (
                str(detail.get("itemNum") or "").strip()[-4:]
                == str(review.get("seller_sku") or "").strip()[-4:]
            ),
            "weight": abs(
                float(detail.get("weight") or 0)
                - float(review.get("weight_kg") or 0)
            ) < 0.0001,
            "package": (
                len(expected_package) == 3
                and all(
                    abs(actual - expected) < 0.0001
                    for actual, expected in zip(package, expected_package)
                )
            ),
            "images": images == expected_images,
            "video": (
                (
                    str(review.get("video_action") or "").casefold() == "keep"
                    and bool(detail.get("mainImgVideoUrl"))
                    and (
                        not expected_video
                        or str(detail.get("mainImgVideoUrl") or "")
                        == expected_video
                    )
                )
                or (
                    str(review.get("video_action") or "").casefold() != "keep"
                    and not detail.get("mainImgVideoUrl")
                )
            ),
        }
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            raise RuntimeError(
                "Miaoshou readback differs from the locked approval: "
                + ", ".join(failed)
            )
        state["miaoshou_second_review"] = {
            "status": "verified",
            "verified": True,
            "checks": checks,
            "verified_at": _now(),
            "source": "miaoshou_open_api",
        }
    else:
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
    _write_json_atomic(draft_path, draft_state)
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
        "locked_approval_preserved": locked,
        "checks": checks,
    }


def claim_miaoshou_to_tiktok(
    offer_id_or_url: str,
    *,
    post=None,
    selected_target_ids: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
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
        state = load_state(offer_id)
        source = preview.get("source") or {}
        source_item_id = (
            source.get("source_id")
            or ((source.get("precollect") or {}).get("records") or [{}])[0].get("source_id")
            or offer_id
        )
        state = load_state(offer_id)
        selected = list(
            selected_target_ids
            if selected_target_ids is not None
            else (state.get("review") or {}).get("selected_sites") or []
        )
        selected = list(dict.fromkeys(str(value).strip().lower() for value in selected if str(value).strip()))
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
        _write_json_atomic(claim_path, result)

        # A governed release adapter executes one target at a time.  Do not
        # carry stale shops from an earlier attempt into a new claim, otherwise
        # a retry can silently prepare or publish an already-successful site.
        shops: dict[str, Any] = {
            key: dict(value)
            for key, value in (existing.get("shops") or {}).items()
            if key in selected and isinstance(value, dict)
        }
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

        detail_group_detail_ids: dict[str, int] = {
            str(key): int(value)
            for key, value in (
                existing.get("detail_group_detail_ids")
                or existing.get("publish_group_detail_ids")
                or {}
            ).items()
            if value
        }
        detail_group_targets: dict[str, dict[str, Any]] = {}
        for detail_group, group_targets in grouped_targets.items():
            group_detail_id = int(detail_group_detail_ids.get(detail_group) or 0)
            if not group_detail_id:
                group_detail_id = _claim_common_to_tiktok_detail(
                    post,
                    offer_id,
                    serial_number=_claim_serial_number(group_targets),
                )
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

        current_detail_ids = [
            int(detail_group_detail_ids.get(group_key) or 0)
            for group_key in grouped_targets
            if int(detail_group_detail_ids.get(group_key) or 0)
        ]
        tiktok_detail_id = int(
            current_detail_ids[0]
            if current_detail_ids
            else tiktok_detail_id or 0
        )

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
            _write_json_atomic(claim_path, result)

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
        _write_json_atomic(claim_path, result)
        draft_path = STATE_DIR / f"{offer_id}_miaoshou_draft.json"
        draft_state = _load_json(draft_path) or {}
        draft_state.update({"claimed": True, "tiktok_detail_id": tiktok_detail_id, "published": False, "updated_at": _now()})
        _write_json_atomic(draft_path, draft_state)
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
        _write_json_atomic(claim_path, failed)
        raise
    finally:
        lock.release()


def load_miaoshou_tiktok_claim(offer_id_or_url: str) -> dict[str, Any]:
    """Read the persisted TikTok claim receipt without creating or repairing it."""

    offer_id = resolve_offer_key(offer_id_or_url)
    value = _load_json(STATE_DIR / f"{offer_id}_tiktok_claim.json")
    return dict(value) if isinstance(value, dict) else {}


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
    _write_json_atomic(claim_path, current)
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
    allow_claim_repair: bool = True,
    strict_selected_skus: bool = False,
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
        if (
            not allow_claim_repair
            or "\u672a\u9009\u62e9\u9884\u53d1\u5e03\u5e97\u94fa" not in str(e)
        ):
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
    package_length, package_width, package_height = _miaoshou_platform_package_cm(
        draft
    )
    info.update({
        "title": draft.get("title") or info.get("title"),
        "notes": draft.get("notes") or info.get("notes") or "",
        "imgUrls": list(draft.get("imgUrls") or []),
        "weight": float(draft.get("weight") or 0),
        "packageLength": package_length,
        "packageWidth": package_width,
        "packageHeight": package_height,
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
    if strict_selected_skus:
        selected_sku_map = _strict_selected_miaoshou_sku_map(
            info.get("skuMap"), draft, region=region
        )
        info["skuMap"] = selected_sku_map
        _filter_miaoshou_variant_maps(info, selected_sku_map)
    _apply_audited_english_variant_labels(
        info,
        draft.get("skuLabelOverrides") or {},
    )
    sku_numbers = _sequential_sku_numbers(info.get("skuMap") or {}, draft.get("itemNum") or "")
    for sku_key, sku in (info.get("skuMap") or {}).items():
        stock = int(DEFAULT_LISTING_STOCK)
        sku.update({
            "price": list_price,
            "priceIncludeVat": list_price,
            "itemNum": sku_numbers[sku_key],
            "stock": stock,
            "weight": float(draft.get("weight") or 0),
            "packageLength": package_length,
            "packageWidth": package_width,
            "packageHeight": package_height,
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
        "english_variants": _english_variant_checks_pass(verified, info),
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
        "source_package_cm": [
            float(draft.get("packageLength") or 0),
            float(draft.get("packageWidth") or 0),
            float(draft.get("packageHeight") or 0),
        ],
        "platform_package_cm": [
            package_length,
            package_width,
            package_height,
        ],
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
    strict_selected_skus: bool = False,
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

    package_length, package_width, package_height = _miaoshou_platform_package_cm(
        draft
    )
    info.update({
        "title": _normalize_title(draft.get("title") or info.get("title") or ""),
        "notes": draft.get("notes") or info.get("notes") or "",
        "imgUrls": list(draft.get("imgUrls") or []),
        "weight": float(draft.get("weight") or 0),
        "packageLength": package_length,
        "packageWidth": package_width,
        "packageHeight": package_height,
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
    if strict_selected_skus:
        selected_sku_map = _strict_selected_miaoshou_sku_map(
            info.get("skuMap"), draft, region=region
        )
        info["skuMap"] = selected_sku_map
        _filter_miaoshou_variant_maps(info, selected_sku_map)

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

    _apply_audited_english_variant_labels(
        info,
        draft.get("skuLabelOverrides") or {},
    )

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
            "packageLength": package_length,
            "packageWidth": package_width,
            "packageHeight": package_height,
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
        "english_variants": _english_variant_checks_pass(verified, info),
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
        "source_package_cm": [
            float(draft.get("packageLength") or 0),
            float(draft.get("packageWidth") or 0),
            float(draft.get("packageHeight") or 0),
        ],
        "platform_package_cm": [
            package_length,
            package_width,
            package_height,
        ],
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
        state = load_state(offer_id)
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
        category_id = _tiktok_category_id(preview)
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
        _write_json_atomic(output_path, result)

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
        _write_json_atomic(output_path, result)

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
                        draft=_regional_listing_draft(
                            draft,
                            state,
                            channel="tiktok",
                            site=region,
                        ),
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
                _write_json_atomic(output_path, result)
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
                        draft=_regional_listing_draft(
                            draft,
                            state,
                            channel="tiktok",
                            site=region,
                        ),
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
                _write_json_atomic(output_path, result)
                time.sleep(0.8)
                continue

            raise RuntimeError(f"Unsupported region {region}")

        result["in_progress"] = False
        result["updated_at"] = _now()
        _write_json_atomic(output_path, result)
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
        _write_json_atomic(output_path, failed)
        raise
    finally:
        lock.release()
