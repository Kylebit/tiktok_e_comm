"""从商品目录构建 Ozon 待搬运列表，并同步 tk_sku_map.json。"""

from __future__ import annotations

import json
import time
from pathlib import Path

from modules.catalog import listings as cat_mod
from modules.catalog.sku_key import tk_match_key
from modules.ozon.config import ozon_data_dir
from modules.ozon.tk_variant import group_variant_index, tk_group_info
from modules.products.image_ai import extract_listing_image_urls

_LISTED_CACHE: dict = {"offer_ids": set(), "fetched_at": 0.0}
_LISTED_CACHE_TTL_SEC = 300


def to_4digit_offer_id(seller_sku: str) -> str:
    """Ozon offer_id：6 位数字货号取后四位，与 tk_match_key 一致。"""
    mk = tk_match_key(seller_sku)
    if mk:
        return mk
    return (seller_sku or "").strip()


def _pick_tk_row(
    item: dict,
    *,
    match_key: str | None = None,
    seller_sku: str | None = None,
) -> dict | None:
    """TikTok 站点行：优先 seller_sku / match_key，否则第一个有货号的行。"""
    tk = item.get("tiktok")
    if not tk:
        return None
    rows = [r for r in tk.get("regions") or [] if (r.get("seller_sku") or "").strip()]
    if seller_sku:
        sk = seller_sku.strip()
        for row in rows:
            if (row.get("seller_sku") or "").strip() == sk:
                return row
    if match_key:
        for row in rows:
            if tk_match_key(row.get("seller_sku") or "") == match_key:
                return row
    return rows[0] if rows else None


def _normalize_listed_offer_id(offer_id: str) -> str:
    s = str(offer_id or "").strip()
    if not s:
        return ""
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return s
    return digits.zfill(4)[-4:]


def _is_formally_listed_on_ozon(info: dict) -> bool:
    """Ozon /v3/product/info/list 返回项：已创建且未归档视为正式上架。"""
    if not info or info.get("is_archived"):
        return False
    statuses = info.get("statuses") or {}
    if statuses.get("is_created"):
        return True
    state = (statuses.get("status") or info.get("status") or "").lower()
    return state in ("price_sent", "processed", "moderating", "active", "visible")


def fetch_ozon_listed_offer_ids(*, force_refresh: bool = False) -> set[str]:
    """通过 Ozon API 拉取已正式上架的 offer_id（4 位）集合，带短期缓存。"""
    now = time.time()
    cached = _LISTED_CACHE.get("offer_ids") or set()
    if cached and not force_refresh and (now - float(_LISTED_CACHE.get("fetched_at") or 0)) < _LISTED_CACHE_TTL_SEC:
        return set(cached)

    try:
        from modules.ozon.client import ozon_post
    except Exception:
        return set(cached)

    offer_ids: set[str] = set()
    last_id = ""
    product_ids: list[int] = []
    while True:
        resp = ozon_post(
            "/v3/product/list",
            {"filter": {"visibility": "ALL"}, "last_id": last_id, "limit": 1000},
        )
        result = resp.get("result") or {}
        items = result.get("items") or []
        for it in items:
            oid = _normalize_listed_offer_id(it.get("offer_id") or "")
            if oid:
                offer_ids.add(oid)
            pid = it.get("product_id")
            if pid is not None:
                product_ids.append(int(pid))
        last_id = result.get("last_id") or ""
        if not last_id or not items:
            break

    for i in range(0, len(product_ids), 50):
        batch = product_ids[i : i + 50]
        try:
            resp = ozon_post("/v3/product/info/list", {"product_id": batch, "offer_id": [], "sku": []})
        except Exception:
            continue
        for info in resp.get("items") or resp.get("result", {}).get("items") or []:
            if not _is_formally_listed_on_ozon(info):
                continue
            oid = _normalize_listed_offer_id(info.get("offer_id") or "")
            if oid:
                offer_ids.add(oid)

    _LISTED_CACHE["offer_ids"] = offer_ids
    _LISTED_CACHE["fetched_at"] = now
    return set(offer_ids)


def _needs_migrate(item: dict, *, listed_offer_ids: set[str] | None = None) -> bool:
    """仅 TikTok 商品；已在 Ozon 正式上架的排除（catalog 标记 + Ozon API）。"""
    if not item.get("tiktok"):
        return False
    oz = item.get("ozon")
    if oz and oz.get("migrated"):
        return False
    row = _pick_tk_row(item)
    if not row:
        return False
    if listed_offer_ids is not None:
        seller_sku = (row.get("seller_sku") or "").strip()
        oid = _normalize_listed_offer_id(to_4digit_offer_id(seller_sku))
        if oid and oid in listed_offer_ids:
            return False
    return True


def _fetch_tk_detail(product_id: str, shop_cipher: str) -> dict:
    if not product_id or not shop_cipher:
        return {}
    try:
        from core import auth as tk_auth
        from core.api_client import get as tk_get

        token = tk_auth.ensure_valid_token()["access_token"]
        resp = tk_get(
            f"/product/202309/products/{product_id}",
            token,
            {"shop_cipher": shop_cipher},
        )
        return resp.get("data") or {}
    except Exception:
        return {}


def _map_entry_from_item(
    item: dict,
    *,
    fetch_detail: bool = False,
    match_key: str | None = None,
    seller_sku: str | None = None,
) -> dict | None:
    mk = match_key or (tk_match_key(seller_sku) if seller_sku else None) or item.get("match_key") or ""
    row = _pick_tk_row(item, match_key=mk or None, seller_sku=seller_sku)
    if not row:
        return None
    seller_sku = (row.get("seller_sku") or "").strip()
    if not seller_sku:
        return None
    title = row.get("product_name") or (item.get("tiktok") or {}).get("product_name") or ""
    product_id = (row.get("product_id") or "").strip()
    shop_cipher = (row.get("shop_cipher") or "").strip()
    image_urls: list[str] = []
    package_dimensions_cm: dict | None = None
    if fetch_detail and product_id and shop_cipher:
        detail = _fetch_tk_detail(product_id, shop_cipher)
        image_urls = extract_listing_image_urls(detail)
        if not title and detail.get("title"):
            title = detail["title"]
        package_dimensions_cm = _extract_package_dimensions_cm(detail, seller_sku)
    if not image_urls and row.get("image_url"):
        image_urls = [row["image_url"]]
    tk = item.get("tiktok")
    if tk:
        for r in tk.get("regions") or []:
            if r.get("image_url") and r["image_url"] not in image_urls:
                image_urls.append(r["image_url"])
    return {
        "seller_sku": seller_sku,
        "title": title,
        "image_urls": image_urls[:6],
        "tk_id": product_id or "",
        "match_key": item.get("match_key") or mk or tk_match_key(seller_sku),
        "source_row": row,
        "package_dimensions_cm": package_dimensions_cm,
    }


def _extract_package_dimensions_cm(detail: dict, seller_sku: str) -> dict | None:
    """从 TikTok 商品详情（原链接）取该 SKU 的包裹长宽高（cm）。
    优先 SKU 级 sku_dimensions，否则用商品级 package_dimensions。"""
    sku_dim: dict = {}
    for s in detail.get("skus") or []:
        if (s.get("seller_sku") or "").strip() == seller_sku:
            sku_dim = s.get("sku_dimensions") or {}
            break
    dim = sku_dim or detail.get("package_dimensions") or {}
    length, width, height = dim.get("length"), dim.get("width"), dim.get("height")
    if not (length and width and height):
        return None
    try:
        return {
            "length": float(length),
            "width": float(width),
            "height": float(height),
        }
    except (TypeError, ValueError):
        return None


def _load_tk_map() -> dict:
    base = ozon_data_dir()
    if not base:
        return {}
    path = base / "tk_sku_map.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_tk_map(data: dict) -> None:
    base = ozon_data_dir()
    if not base:
        return
    path = base / "tk_sku_map.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _find_map_key(data: dict, match_key: str) -> str | None:
    for k, row in data.items():
        if not isinstance(row, dict):
            continue
        sk = str(row.get("seller_sku") or "").strip()
        if tk_match_key(sk) == match_key or str(k).zfill(4) == match_key:
            return str(k)
    return None


def sync_catalog_to_tk_map(*, max_items: int | None = None) -> int:
    """把目录中未上 Ozon 的商品写入 tk_sku_map（不覆盖已有 tk_id/图片）。"""
    data = _load_tk_map()
    updated = 0
    offset = 0
    limit = 500
    while True:
        page = cat_mod.list_products(limit=limit, offset=offset)
        for item in page.get("items") or []:
            if not _needs_migrate(item):
                continue
            entry = _map_entry_from_item(item, fetch_detail=False)
            if not entry:
                continue
            mk = entry["match_key"]
            map_key = _find_map_key(data, mk) or (mk.lstrip("0") or mk)
            prev = data.get(map_key) if isinstance(data.get(map_key), dict) else {}
            merged = dict(prev)
            merged["seller_sku"] = entry["seller_sku"]
            if entry["title"]:
                merged["title"] = entry["title"]
            if entry["image_urls"]:
                merged["image_urls"] = entry["image_urls"]
            if entry["tk_id"] and not merged.get("tk_id"):
                merged["tk_id"] = entry["tk_id"]
            if merged != prev:
                data[map_key] = merged
                updated += 1
            if max_items and updated >= max_items:
                _save_tk_map(data)
                return updated
        offset += limit
        if offset >= page.get("total", 0):
            break
    if updated:
        _save_tk_map(data)
    return updated


def iter_migrate_candidates(*, listed_offer_ids: set[str] | None = None):
    if listed_offer_ids is None:
        listed_offer_ids = fetch_ozon_listed_offer_ids()
    offset = 0
    limit = 500
    while True:
        page = cat_mod.list_products(limit=limit, offset=offset)
        for item in page.get("items") or []:
            if _needs_migrate(item, listed_offer_ids=listed_offer_ids):
                yield item
        offset += limit
        if offset >= page.get("total", 0):
            break


def catalog_item_by_seller_sku(seller_sku: str) -> dict | None:
    key = tk_match_key(seller_sku)
    page = cat_mod.list_products(sku=seller_sku, limit=50, offset=0)
    for item in page.get("items") or []:
        if item.get("match_key") == key:
            return item
        row = _pick_tk_row(item)
        if row and (row.get("seller_sku") or "").strip() == seller_sku.strip():
            return item
    return None


def list_unmigrated_from_catalog(*, sync_map: bool = True) -> list[dict]:
    """
    商品目录 → 待搬运列表（未在 Ozon 正式上架的全部 TikTok 商品）。
    同 SPU 多 SKU（tk_group）各占一行，不按 tk_id 误标重复。
    """
    if sync_map:
        sync_catalog_to_tk_map()

    from modules.ozon.pending_drafts import dismissed_offer_ids, dismissed_seller_skus

    dismissed = dismissed_seller_skus()
    dismissed_oids = dismissed_offer_ids()
    listed_offer_ids = fetch_ozon_listed_offer_ids()

    items: list[dict] = []
    seen_offer: set[str] = set()
    seen_spu_lone: set[str] = set()
    variant_cache: dict[str, dict[str, dict]] = {}

    for cat_item in iter_migrate_candidates(listed_offer_ids=listed_offer_ids):
        match_key = cat_item.get("match_key") or ""
        entry = _map_entry_from_item(cat_item, fetch_detail=False, match_key=match_key)
        if not entry:
            continue
        offer_id = to_4digit_offer_id(entry["seller_sku"])
        if offer_id in listed_offer_ids:
            continue
        # 用户已忽略的产品，永久排除（按 seller_sku 或 4 位 offer_id，覆盖跨国 SKU）
        if entry["seller_sku"] in dismissed or offer_id in dismissed_oids:
            continue

        group = tk_group_info(cat_item)
        group_id = group["group_id"] if group else ""
        if group_id and group_id not in variant_cache:
            variant_cache[group_id] = group_variant_index(group["match_keys"])

        seller_sku = entry["seller_sku"]
        tk_id = entry.get("tk_id") or ""
        is_group = bool(group)

        dup = False
        if offer_id in seen_offer:
            dup = True
        elif not is_group and tk_id and tk_id in seen_spu_lone:
            dup = True
        seen_offer.add(offer_id)
        if not is_group and tk_id:
            seen_spu_lone.add(tk_id)

        variant = (variant_cache.get(group_id) or {}).get(match_key) if group else None
        variant_label = (variant.get("model_name") or "").strip() if variant else ""
        image = (entry.get("image_urls") or [""])[0]
        if variant and variant.get("image_url"):
            image = variant["image_url"]

        row = {
            "offer_id": offer_id,
            "seller_sku": seller_sku,
            "tk_id": tk_id,
            "title": entry.get("title") or "",
            "image": image,
            "image_count": len(entry.get("image_urls") or []),
            "match_key": entry.get("match_key") or match_key,
            "tk_dup": dup,
            "catalog": True,
            "variant_label": variant_label,
            "tk_group_id": group_id,
            "tk_group_keys": group["match_keys"] if group else [],
            "tk_group_size": group["size"] if group else 0,
            "tk_group_primary": group["primary_key"] if group else "",
        }
        if variant and variant.get("price_cny"):
            try:
                row["price_preview_cny"] = int(float(variant["price_cny"]))
            except (TypeError, ValueError):
                pass
        items.append(row)

    items.sort(key=lambda x: (x.get("tk_group_id") or x["offer_id"], x["offer_id"]))
    return items
