"""商品目录：TikTok 物流实测重量（多包裹取中位数）同步与查询。"""

from __future__ import annotations

import statistics
import time
from typing import Callable

from core.db import connect, init_db
from modules.catalog.sku_key import tk_match_key
from modules.ozon.logistics_weight import (
    SCAN_DAYS,
    _aggregate,
    _scan_packages,
    _read_cache,
    _write_cache,
)
from core import auth

REGION_SKU_PREFIX = {"MY": "66", "PH": "77", "VN": "88", "TH": "99"}
CACHE_TTL_SEC = 24 * 3600


def _region_prefix(region: str) -> str:
    return REGION_SKU_PREFIX.get((region or "").upper(), "")


def _region_db_samples(region: str, by_sku: dict[str, dict]) -> dict[str, list[dict]]:
    """从 DB 已有记录还原某国样本，避免重复扫 API。"""
    pref = _region_prefix(region)
    if not pref:
        return {}
    out: dict[str, list[dict]] = {}
    for sk, entry in by_sku.items():
        if not sk.startswith(pref):
            continue
        wg = int(entry.get("weight_g") or 0)
        if wg <= 0:
            continue
        cnt = max(1, min(int(entry.get("package_count") or 1), 5))
        out[sk] = [
            {
                "package_id": "",
                "weight_g": wg,
                "dimension": {},
                "update_time": int(entry.get("updated_at") or 0),
            }
            for _ in range(cnt)
        ]
    return out


def _region_db_count(region: str, by_sku: dict[str, dict]) -> int:
    pref = _region_prefix(region)
    if not pref:
        return 0
    return sum(1 for sk in by_sku if sk.startswith(pref))


def _region_has_db_weights(region: str, by_sku: dict[str, dict], *, min_skus: int = 5) -> bool:
    return _region_db_count(region, by_sku) >= min_skus


def _region_needs_scan(
    region: str,
    cipher: str,
    by_sku: dict[str, dict],
    *,
    force_refresh: bool,
    days: int = SCAN_DAYS,
) -> bool:
    """该国是否还需调 API（近 days 天有效缓存可跳过）。"""
    del region, by_sku  # 按店铺缓存判断，不用 DB 条数跳过（避免旧窗口数据不再扫）
    if force_refresh:
        return True
    if _cache_fresh(cipher, min_skus=5, min_scan_days=days):
        return False
    return True


def _shop_ciphers_with_region() -> list[tuple[str, str]]:
    init_db()
    conn = connect()
    rows = conn.execute(
        """SELECT DISTINCT p.shop_cipher, COALESCE(s.region, '') AS region
           FROM products p
           LEFT JOIN shops s ON s.cipher = p.shop_cipher
           WHERE p.shop_cipher IS NOT NULL AND p.shop_cipher != ''"""
    ).fetchall()
    conn.close()
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for r in rows:
        cipher = r["shop_cipher"]
        if cipher in seen:
            continue
        seen.add(cipher)
        out.append((cipher, (r["region"] or "?").upper()))
    return out


def _shop_ciphers() -> list[str]:
    return [c for c, _ in _shop_ciphers_with_region()]


def save_weight(
    seller_sku: str,
    *,
    weight_g: int,
    package_count: int,
    depth_mm: int | None = None,
    width_mm: int | None = None,
    height_mm: int | None = None,
) -> None:
    sk = (seller_sku or "").strip()
    if not sk or weight_g <= 0:
        return
    init_db()
    conn = connect()
    conn.execute(
        """INSERT INTO sku_logistics_weights
           (seller_sku, weight_g, package_count, depth_mm, width_mm, height_mm, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(seller_sku) DO UPDATE SET
             weight_g=excluded.weight_g,
             package_count=excluded.package_count,
             depth_mm=excluded.depth_mm,
             width_mm=excluded.width_mm,
             height_mm=excluded.height_mm,
             updated_at=excluded.updated_at""",
        (
            sk,
            int(weight_g),
            int(package_count),
            depth_mm,
            width_mm,
            height_mm,
            int(time.time()),
        ),
    )
    conn.commit()
    conn.close()


def load_by_seller_sku() -> dict[str, dict]:
    init_db()
    conn = connect()
    out: dict[str, dict] = {}
    for r in conn.execute(
        """SELECT seller_sku, weight_g, package_count, depth_mm, width_mm, height_mm, updated_at
           FROM sku_logistics_weights WHERE weight_g > 0"""
    ):
        out[r["seller_sku"]] = {
            "weight_g": int(r["weight_g"]),
            "package_count": int(r["package_count"] or 0),
            "depth_mm": r["depth_mm"],
            "width_mm": r["width_mm"],
            "height_mm": r["height_mm"],
            "updated_at": int(r["updated_at"] or 0),
        }
    conn.close()
    return out


def weight_index_by_match_key() -> dict[str, dict]:
    """match_key → 物流实测重量（四国 seller_sku 合并取中位数）。"""
    by_sku = load_by_seller_sku()
    if not by_sku:
        return {}

    key_samples: dict[str, list[dict]] = {}
    for sk, entry in by_sku.items():
        key = tk_match_key(sk)
        if not key:
            continue
        key_samples.setdefault(key, []).append(entry)

    out: dict[str, dict] = {}
    for key, samples in key_samples.items():
        weights = sorted(s["weight_g"] for s in samples if s.get("weight_g"))
        if not weights:
            continue
        mid = int(round(statistics.median(weights)))
        total_pkgs = sum(int(s.get("package_count") or 0) for s in samples)
        latest = samples[-1]
        out[key] = {
            "weight_g": mid,
            "package_count": total_pkgs,
            "depth_mm": latest.get("depth_mm"),
            "width_mm": latest.get("width_mm"),
            "height_mm": latest.get("height_mm"),
            "weight_source": "logistics",
        }

    from modules.catalog.weight_overrides import load_overrides

    for key, ov in load_overrides().items():
        out[key] = {**out.get(key, {}), "weight_g": ov["weight_g"], "weight_source": "manual_override"}

    return out


def lookup_stored(seller_sku: str) -> dict | None:
    sk = (seller_sku or "").strip()
    if not sk:
        return None
    entry = load_by_seller_sku().get(sk)
    if not entry:
        mk = tk_match_key(sk)
        if mk:
            alt = weight_index_by_match_key().get(mk)
            if alt:
                entry = alt
    if not entry:
        return None
    return {
        "weight_g": str(entry["weight_g"]),
        "depth": str(entry.get("depth_mm") or ""),
        "width": str(entry.get("width_mm") or ""),
        "height": str(entry.get("height_mm") or ""),
        "package_count": int(entry.get("package_count") or 0),
        "weight_source": "logistics",
    }


def _persist_index(index: dict[str, dict]) -> int:
    n = 0
    for sku, agg in index.items():
        wg = agg.get("weight_g")
        if not wg:
            continue
        try:
            weight_g = int(wg)
        except (TypeError, ValueError):
            continue
        save_weight(
            sku,
            weight_g=weight_g,
            package_count=int(agg.get("package_count") or 0),
            depth_mm=int(agg["depth"]) if agg.get("depth") else None,
            width_mm=int(agg["width"]) if agg.get("width") else None,
            height_mm=int(agg["height"]) if agg.get("height") else None,
        )
        n += 1
    return n


def _cache_fresh(cipher: str, *, max_age_sec: int = CACHE_TTL_SEC, min_skus: int = 3, min_scan_days: int = SCAN_DAYS) -> dict | None:
    cache = _read_cache(cipher)
    if not cache or not cache.get("cached_at"):
        return None
    if int(cache.get("scan_days") or 0) < min_scan_days:
        return None
    if (time.time() - int(cache["cached_at"])) > max_age_sec:
        return None
    index = cache.get("index")
    if not isinstance(index, dict) or not index:
        return None
    if len(index) < min_skus:
        return None
    return cache


def _samples_from_cached_index(cache: dict) -> dict[str, list[dict]]:
    """把单店缓存的聚合结果还原为样本（供多国合并再取中位数）。"""
    out: dict[str, list[dict]] = {}
    index = cache.get("index") or {}
    cached_at = int(cache.get("cached_at") or 0)
    for sku, entry in index.items():
        if not isinstance(entry, dict):
            continue
        try:
            wg = int(entry.get("weight_g") or 0)
        except (TypeError, ValueError):
            continue
        if wg <= 0:
            continue
        cnt = max(1, int(entry.get("package_count") or 1))
        dim = {}
        if entry.get("depth"):
            dim["length"] = str(int(entry["depth"]) // 10)
        if entry.get("width"):
            dim["width"] = str(int(entry["width"]) // 10)
        if entry.get("height"):
            dim["height"] = str(int(entry["height"]) // 10)
        samples = [
            {
                "package_id": entry.get("sample_package_id") or "",
                "weight_g": wg,
                "dimension": dim,
                "update_time": cached_at,
            }
            for _ in range(min(cnt, 5))
        ]
        out[sku] = samples
    return out


def sync_logistics_weights(
    on_progress: Callable[[str], None] | None = None,
    *,
    max_pages: int = 80,
    force_refresh: bool = False,
    days: int = SCAN_DAYS,
) -> dict:
    """逐国扫描已完成包裹（默认近一年），合并四国数据后按 seller_sku 取中位数写入 SQLite。"""
    def prog(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    shops = _shop_ciphers_with_region()
    if not shops:
        return {"shops": 0, "skus": 0, "skipped": True}

    shops = sorted(_shop_ciphers_with_region(), key=lambda x: x[1])
    if not shops:
        return {"shops": 0, "skus": 0, "skipped": True}

    all_samples: dict[str, list[dict]] = {}
    per_region: dict[str, int] = {}
    cached_regions: list[str] = []
    scanned_regions: list[str] = []
    db_regions: list[str] = []
    existing_db = load_by_seller_sku()

    prog(f"物流重量：共 {len(shops)} 国店铺，近 {days} 天包裹，逐国拉取…")
    for i, (cipher, region) in enumerate(shops, 1):
        label = region or f"shop{i}"
        existing_db = load_by_seller_sku()

        if not _region_needs_scan(label, cipher, existing_db, force_refresh=force_refresh, days=days):
            cache = _cache_fresh(cipher, min_skus=5, min_scan_days=days)
            if cache:
                by_sku = _samples_from_cached_index(cache)
                for sku, samples in by_sku.items():
                    all_samples.setdefault(sku, []).extend(samples)
                per_region[label] = len(by_sku)
                cached_regions.append(label)
                prog(f"物流重量：{label} 读缓存 {len(by_sku)} SKU（{days}天）")
                partial = {sku: _aggregate(all_samples[sku]) for sku in by_sku if all_samples.get(sku)}
                _persist_index(partial)
                continue

        prog(f"物流重量：{label} 扫描 API ({i}/{len(shops)})，近{days}天…")
        try:
            tok = auth.access_token()
            by_sku = _scan_packages(
                cipher,
                tok,
                seller_sku=None,
                days=days,
                min_samples=10**9,
                max_pages=max_pages,
            )
            per_region[label] = len(by_sku)
            scanned_regions.append(label)
            prog(f"物流重量：{label} 完成 {len(by_sku)} SKU")
            if by_sku:
                shop_index = {sku: _aggregate(samples) for sku, samples in by_sku.items()}
                _write_cache(
                    cipher,
                    {
                        "cached_at": int(time.time()),
                        "scan_days": days,
                        "index": shop_index,
                        "region": label,
                    },
                )
                for sku, samples in by_sku.items():
                    all_samples.setdefault(sku, []).extend(samples)
                partial = {sku: _aggregate(all_samples[sku]) for sku in by_sku if all_samples.get(sku)}
                _persist_index(partial)
        except Exception as e:
            prog(f"物流重量：{label} 失败 {e}")
            per_region[label] = 0

    index = {sku: _aggregate(samples) for sku, samples in all_samples.items() if samples}
    n = _persist_index(index)
    mk = weight_index_by_match_key()
    prog(f"物流重量：写入 {n} 个 SKU，覆盖 {len(mk)} 个对齐码")
    return {
        "shops": len(shops),
        "skus": n,
        "match_keys": len(mk),
        "cached": bool(cached_regions) and not scanned_regions,
        "cached_regions": cached_regions,
        "db_regions": db_regions,
        "scanned_regions": scanned_regions,
        "per_region": per_region,
    }
