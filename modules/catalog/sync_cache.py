"""商品同步本地缓存（减少重复 API 调用）。"""

from __future__ import annotations

import json
import time
from pathlib import Path

from core.config import ROOT, get

CACHE_ROOT = ROOT / "data" / "cache"


def _catalog_cfg() -> dict:
    return get("catalog_sync") or {}


def cache_ttl_sec() -> int:
    hours = float(_catalog_cfg().get("cache_ttl_hours") or 6)
    return int(hours * 3600)


def ozon_cache_ttl_sec() -> int:
    hours = float(_catalog_cfg().get("ozon_cache_ttl_hours") or _catalog_cfg().get("cache_ttl_hours") or 6)
    return int(hours * 3600)


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ---- TikTok 商品详情 ----

def tk_detail_path(shop_cipher: str, product_id: str) -> Path:
    safe = (shop_cipher or "shop").replace("/", "_")
    return CACHE_ROOT / "tiktok" / safe / f"{product_id}.json"


def load_tk_detail(shop_cipher: str, product_id: str, *, ttl_sec: int | None = None) -> dict | None:
    path = tk_detail_path(shop_cipher, product_id)
    if not path.is_file():
        return None
    raw = _read_json(path)
    if not isinstance(raw, dict):
        return None
    cached_at = int(raw.get("cached_at") or 0)
    ttl = ttl_sec if ttl_sec is not None else cache_ttl_sec()
    if ttl > 0 and cached_at and (time.time() - cached_at) > ttl:
        return None
    detail = raw.get("detail")
    return detail if isinstance(detail, dict) else None


def save_tk_detail(shop_cipher: str, product_id: str, detail: dict) -> None:
    _write_json(
        tk_detail_path(shop_cipher, product_id),
        {"cached_at": int(time.time()), "detail": detail},
    )


# ---- Shopee 店铺 manifest ----

def shopee_manifest_path(shop_id: int) -> Path:
    return CACHE_ROOT / "shopee" / str(shop_id) / "manifest.json"


def load_shopee_manifest(shop_id: int) -> dict | None:
    raw = _read_json(shopee_manifest_path(shop_id))
    return raw if isinstance(raw, dict) else None


def save_shopee_manifest(shop_id: int, item_ids: list[int]) -> None:
    _write_json(
        shopee_manifest_path(shop_id),
        {"item_ids": sorted(int(x) for x in item_ids), "synced_at": int(time.time())},
    )


def shopee_shop_unchanged(shop_id: int, item_ids: list[int], *, ttl_sec: int | None = None) -> bool:
    m = load_shopee_manifest(shop_id)
    if not m:
        return False
    ttl = ttl_sec if ttl_sec is not None else cache_ttl_sec()
    if ttl <= 0:
        return False
    if int(time.time()) - int(m.get("synced_at") or 0) > ttl:
        return False
    return list(m.get("item_ids") or []) == sorted(int(x) for x in item_ids)


# ---- Shopee 单商品 model 缓存 ----

def shopee_item_path(shop_id: int, item_id: int) -> Path:
    return CACHE_ROOT / "shopee" / str(shop_id) / "items" / f"{item_id}.json"


def load_shopee_item(shop_id: int, item_id: int, *, ttl_sec: int | None = None) -> dict | None:
    raw = _read_json(shopee_item_path(shop_id, item_id))
    if not isinstance(raw, dict):
        return None
    cached_at = int(raw.get("cached_at") or 0)
    ttl = ttl_sec if ttl_sec is not None else cache_ttl_sec()
    if ttl > 0 and cached_at and (time.time() - cached_at) > ttl:
        return None
    item = raw.get("item")
    return item if isinstance(item, dict) else None


def save_shopee_item(shop_id: int, item_id: int, item: dict) -> None:
    _write_json(
        shopee_item_path(shop_id, item_id),
        {"cached_at": int(time.time()), "item": item},
    )


# ---- Ozon 快照 TTL ----

def ozon_snapshot_fresh(path: Path, *, ttl_sec: int | None = None) -> bool:
    if not path.is_file():
        return False
    ttl = ttl_sec if ttl_sec is not None else ozon_cache_ttl_sec()
    if ttl <= 0:
        return False
    return (time.time() - path.stat().st_mtime) < ttl
