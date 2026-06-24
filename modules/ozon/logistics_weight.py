"""从 TikTok Fulfillment 已完成包裹读取物流实测重量/尺寸（非卖家填写的商品重量）。"""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from core.config import ROOT
from core import auth
from core.api_client import get as api_get, post as api_post

CACHE_ROOT = ROOT / "data" / "cache" / "logistics_weight"
CACHE_TTL_SEC = 24 * 3600
MISSING_TTL_SEC = 6 * 3600
SCAN_DAYS = 365


def _cache_path(shop_cipher: str) -> Path:
    safe = (shop_cipher or "shop").replace("/", "_")
    return CACHE_ROOT / safe / "by_sku.json"


def _read_cache(shop_cipher: str) -> dict | None:
    path = _cache_path(shop_cipher)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _write_cache(shop_cipher: str, data: dict) -> None:
    path = _cache_path(shop_cipher)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _cm_to_mm(val: str | int | float | None) -> str:
    if val is None or val == "":
        return ""
    try:
        n = int(float(str(val).replace(",", ".")))
    except ValueError:
        return ""
    if n <= 0:
        return ""
    return str(n * 10)


def _parse_weight_g(weight: dict | None) -> int | None:
    if not isinstance(weight, dict):
        return None
    unit = str(weight.get("unit") or "").upper()
    raw = weight.get("value")
    if raw is None or raw == "":
        return None
    try:
        n = float(str(raw).replace(",", "."))
    except ValueError:
        return None
    if unit == "KILOGRAM":
        return int(round(n * 1000))
    if unit == "GRAM":
        return int(round(n))
    return int(round(n))


def _aggregate(samples: list[dict]) -> dict:
    weights = [s["weight_g"] for s in samples if s.get("weight_g")]
    out: dict = {
        "weight_g": str(int(round(statistics.median(weights)))) if weights else "",
        "package_count": len(samples),
        "sample_package_id": samples[-1].get("package_id", "") if samples else "",
    }
    latest = samples[-1] if samples else {}
    dim = latest.get("dimension") or {}
    out["depth"] = _cm_to_mm(dim.get("length"))
    out["width"] = _cm_to_mm(dim.get("width"))
    out["height"] = _cm_to_mm(dim.get("height"))
    return out


def _package_has_sku(pkg: dict, seller_sku: str) -> bool:
    for order in pkg.get("orders") or []:
        for sku in order.get("skus") or []:
            if (sku.get("name") or "").strip() == seller_sku:
                return True
    return False


def _extract_samples(package_id: str, detail: dict) -> list[tuple[str, dict]]:
    rows: list[tuple[str, dict]] = []
    weight_g = _parse_weight_g(detail.get("weight"))
    dim = detail.get("dimension") or {}
    for order in detail.get("orders") or []:
        for sku in order.get("skus") or []:
            name = (sku.get("name") or "").strip()
            if not name:
                continue
            rows.append(
                (
                    name,
                    {
                        "package_id": package_id,
                        "weight_g": weight_g,
                        "dimension": dim,
                        "update_time": int(detail.get("update_time") or 0),
                    },
                )
            )
    return rows


def _update_cache_index(shop_cipher: str, new_samples: dict[str, list[dict]]) -> dict[str, dict]:
    cache = _read_cache(shop_cipher) or {}
    index = cache.get("index") if isinstance(cache.get("index"), dict) else {}
    missing = cache.get("missing") if isinstance(cache.get("missing"), dict) else {}
    for sku, samples in new_samples.items():
        if samples:
            index[sku] = _aggregate(samples)
            missing.pop(sku, None)
    _write_cache(
        shop_cipher,
        {
            "cached_at": int(time.time()),
            "scan_days": SCAN_DAYS,
            "index": index,
            "missing": missing,
        },
    )
    return index


def _mark_missing(shop_cipher: str, seller_sku: str) -> None:
    cache = _read_cache(shop_cipher) or {}
    index = cache.get("index") if isinstance(cache.get("index"), dict) else {}
    missing = cache.get("missing") if isinstance(cache.get("missing"), dict) else {}
    missing[seller_sku] = int(time.time())
    _write_cache(
        shop_cipher,
        {
            "cached_at": int(cache.get("cached_at") or time.time()),
            "scan_days": SCAN_DAYS,
            "index": index,
            "missing": missing,
        },
    )


def _is_missing_recently(cache: dict | None, seller_sku: str) -> bool:
    missing = (cache or {}).get("missing") if isinstance((cache or {}).get("missing"), dict) else {}
    checked_at = int(missing.get(seller_sku) or 0)
    return checked_at > 0 and (time.time() - checked_at) < MISSING_TTL_SEC


def _scan_packages(
    shop_cipher: str,
    tok: str,
    *,
    seller_sku: str | None = None,
    days: int = SCAN_DAYS,
    max_pages: int = 30,
    min_samples: int = 1,
) -> dict[str, list[dict]]:
    """扫描已完成包裹（最近 N 天，分页上限）。"""
    now = int(time.time())
    start = now - min(days, SCAN_DAYS) * 86400
    by_sku: dict[str, list[dict]] = {}
    page_token = ""
    pages = 0
    target_hits = 0

    while pages < max_pages:
        tok = auth.access_token()
        query = {
            "shop_cipher": shop_cipher,
            "page_size": "30",
            "package_status": "COMPLETED",
        }
        if page_token:
            query["page_token"] = page_token
        resp = None
        for attempt in range(3):
            try:
                resp = api_post(
                    "/fulfillment/202309/packages/search",
                    tok,
                    query,
                    {"update_time_ge": start, "update_time_lt": now},
                )
                if resp.get("code") == 0:
                    break
                if int(resp.get("code") or 0) in (36009007, 36009037):
                    time.sleep(2 * (attempt + 1))
                    continue
                raise RuntimeError(resp.get("message") or str(resp))
            except RuntimeError as e:
                if "504" in str(e) or "timeout" in str(e).lower():
                    time.sleep(2 * (attempt + 1))
                    if attempt >= 2:
                        return by_sku
                    continue
                raise
        if not resp or resp.get("code") != 0:
            break
        data = resp.get("data") or {}
        for pkg in data.get("packages") or []:
            pid = str(pkg.get("id") or "")
            if not pid:
                continue
            if seller_sku and not _package_has_sku(pkg, seller_sku):
                continue
            try:
                detail_resp = api_get(
                    f"/fulfillment/202309/packages/{pid}",
                    auth.access_token(),
                    {"shop_cipher": shop_cipher},
                )
            except RuntimeError:
                continue
            if detail_resp.get("code") != 0:
                continue
            detail = detail_resp.get("data") or {}
            for sku_name, sample in _extract_samples(pid, detail):
                by_sku.setdefault(sku_name, []).append(sample)
                if seller_sku and sku_name == seller_sku:
                    target_hits += 1
            if seller_sku and target_hits >= min_samples:
                return by_sku
        page_token = data.get("next_page_token") or ""
        pages += 1
        if not page_token:
            break
        time.sleep(0.12)
    return by_sku


def refresh_logistics_index(
    shop_cipher: str,
    *,
    access_token: str | None = None,
    days: int = SCAN_DAYS,
    max_pages: int = 50,
) -> dict[str, dict]:
    """全量扫描已完成包裹，按 seller_sku 聚合物流实测重量。"""
    tok = access_token or auth.access_token()
    by_sku = _scan_packages(shop_cipher, tok, days=days, max_pages=max_pages, min_samples=10**9)
    index = {sku: _aggregate(samples) for sku, samples in by_sku.items() if samples}
    _write_cache(
        shop_cipher,
        {"cached_at": int(time.time()), "scan_days": days, "index": index},
    )
    return index


def lookup_logistics_weight(
    seller_sku: str,
    shop_cipher: str,
    *,
    force_refresh: bool = False,
) -> dict | None:
    """
    按 seller_sku 查物流实测重量。
    优先读缓存；缺失时只扫描含该 SKU 的已完成包裹。
    """
    sk = (seller_sku or "").strip()
    if not sk or not shop_cipher:
        return None

    cache = _read_cache(shop_cipher)
    stale = (
        not cache
        or not cache.get("cached_at")
        or (time.time() - int(cache["cached_at"])) > CACHE_TTL_SEC
    )
    index = (cache or {}).get("index") if isinstance((cache or {}).get("index"), dict) else {}

    if not force_refresh and not stale and sk in index and index[sk].get("weight_g"):
        entry = index[sk]
    elif not force_refresh and _is_missing_recently(cache, sk):
        return None
    else:
        try:
            tok = auth.access_token()
            samples = _scan_packages(
                shop_cipher,
                tok,
                seller_sku=sk,
                min_samples=1,
                max_pages=8,
            )
            if sk in samples:
                index = _update_cache_index(shop_cipher, samples)
                entry = index.get(sk)
            else:
                _mark_missing(shop_cipher, sk)
                entry = None
        except Exception:
            entry = index.get(sk) if sk in index else None

    if not entry or not entry.get("weight_g"):
        return None

    return {
        "weight_g": str(entry["weight_g"]),
        "depth": entry.get("depth") or "",
        "width": entry.get("width") or "",
        "height": entry.get("height") or "",
        "package_count": int(entry.get("package_count") or 0),
        "sample_package_id": entry.get("sample_package_id") or "",
        "weight_source": "logistics",
    }
