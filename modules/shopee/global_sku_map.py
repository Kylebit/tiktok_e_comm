"""CNSC 全球商品规格货号映射（店铺 API 常读不到 global_model_sku 时的补充）。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from core.config import ROOT, get
from core.db import connect, init_db
from modules.catalog.sku_key import parse_search_key, shopee_match_key

_MAP_PATH = ROOT / get("shopee", {}).get("global_sku_map", "data/shopee_global_sku_map.json")


def map_path() -> Path:
    p = Path(get("shopee", {}).get("global_sku_map", "data/shopee_global_sku_map.json"))
    return p if p.is_absolute() else ROOT / p


def load_map() -> dict[str, dict]:
    path = map_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return raw if isinstance(raw, dict) else {}


def save_map(data: dict) -> None:
    path = map_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _model_sku(entry: dict) -> str:
    models = entry.get("models") or []
    if not models:
        return str(entry.get("global_item_sku") or entry.get("match_key") or "").strip()
    return str(models[0].get("global_model_sku") or "").strip()


def all_match_keys() -> set[str]:
    keys: set[str] = set()
    for entry in load_map().values():
        if not isinstance(entry, dict):
            continue
        mk = parse_search_key(str(entry.get("match_key") or ""))
        if mk:
            keys.add(mk)
    return keys


def rows_for_match_key(match_key: str) -> list[dict]:
    """从全球映射生成目录/编辑用 Shopee 行（按已发布站点展开）。"""
    key = parse_search_key(match_key)
    if not key:
        return []
    out: list[dict] = []
    for gid, entry in load_map().items():
        if not isinstance(entry, dict):
            continue
        entry_keys = {parse_search_key(str(entry.get("match_key") or ""))}
        for mk in entry.get("match_keys") or []:
            entry_keys.add(parse_search_key(str(mk)))
        entry_keys.discard("")
        if key not in entry_keys:
            continue
        title = str(entry.get("title") or "").strip()
        regions = entry.get("published_regions") or ["GLOBAL"]
        shop_items = entry.get("shop_items") or {}
        models = entry.get("models") or []
        if not models:
            sk = _model_sku(entry)
            if sk:
                models = [{"model_name": "", "global_model_sku": sk}]
        for model in models:
            if not isinstance(model, dict):
                continue
            sku = str(model.get("global_model_sku") or "").strip()
            mk = parse_search_key(sku) or key
            if mk != key:
                continue
            model_name = str(model.get("model_name") or "")
            for reg in regions:
                reg = str(reg).upper()
                shop_ref = shop_items.get(reg) if isinstance(shop_items, dict) else None
                item_id = str((shop_ref or {}).get("item_id") or "")
                model_id = str((shop_ref or {}).get("model_id") or "")
                if not model_id and item_id:
                    model_id = f"item_{item_id}"
                elif model_id.startswith("shop_"):
                    model_id = "item_" + model_id[5:]
                if not model_id:
                    model_id = f"global_{gid}_{sku or mk}"
                shop_id = int((shop_ref or {}).get("shop_id") or 0)
                out.append({
                    "platform": "shopee",
                    "region": reg,
                    "seller_sku": sku or mk,
                    "match_key": mk,
                    "model_id": model_id,
                    "shop_id": shop_id,
                    "product_id": item_id,
                    "global_item_id": str(gid),
                    "product_name": title,
                    "model_name": model_name,
                    "can_push": False,
                    "sku_label": "规格货号(全球)",
                    "source": "cnsc_global_map",
                })
    return out


def apply_to_db() -> int:
    """用全球映射覆盖/补充 shopee_products（优先 shop_items 精确匹配，其次同 region+空/长码）。"""
    data = load_map()
    if not data:
        return 0
    init_db()
    conn = connect()
    updated = 0
    now = int(time.time())

    for gid, entry in data.items():
        if not isinstance(entry, dict):
            continue
        sku = _model_sku(entry)
        if not sku:
            continue
        shop_items = entry.get("shop_items") or {}
        if isinstance(shop_items, dict):
            for reg, ref in shop_items.items():
                if not isinstance(ref, dict):
                    continue
                model_id = str(ref.get("model_id") or "")
                shop_id = int(ref.get("shop_id") or 0)
                if not model_id or not shop_id:
                    continue
                conn.execute(
                    """UPDATE shopee_products SET seller_sku = ?, updated_at = ?
                       WHERE model_id = ? AND shop_id = ?""",
                    (sku, now, model_id, shop_id),
                )
                updated += conn.total_changes

    conn.commit()
    conn.close()
    return updated


def hydrate_shop_items_from_map() -> int:
    """把 global map 里 shop_items 的 item_id 拉进 shopee_products（sync 未对齐时补全前端）。"""
    data = load_map()
    if not data:
        return 0
    from modules.shopee.auth import ensure_shop_token
    from modules.shopee.client import shop_get
    from modules.shopee.sync import REGION_CURRENCY, _image, _price, _stock

    init_db()
    conn = connect()
    inserted = 0
    now = int(time.time())

    for gid, entry in data.items():
        if not isinstance(entry, dict):
            continue
        sku = _model_sku(entry)
        if not sku:
            continue
        title = str(entry.get("title") or "").strip()
        shop_items = entry.get("shop_items") or {}
        if not isinstance(shop_items, dict):
            continue
        for reg, ref in shop_items.items():
            if not isinstance(ref, dict):
                continue
            shop_id = int(ref.get("shop_id") or 0)
            item_id = str(ref.get("item_id") or "").strip()
            if not shop_id or not item_id:
                continue
            model_id = str(ref.get("model_id") or f"item_{item_id}")
            if model_id.startswith("shop_"):
                model_id = "item_" + model_id[5:]
            region = str(reg).upper()
            currency = REGION_CURRENCY.get(region, "")
            try:
                token = ensure_shop_token(shop_id)
                resp = shop_get(
                    "/api/v2/product/get_item_base_info",
                    shop_id,
                    token,
                    {"item_id_list": item_id},
                )
                items = (resp.get("response") or {}).get("item_list") or []
                item = items[0] if items else {}
            except Exception:
                item = {}
            price, cur = _price(item.get("price_info"))
            row = {
                "model_id": model_id,
                "shop_id": shop_id,
                "region": region,
                "item_id": item_id,
                "seller_sku": sku or (item.get("item_sku") or "").strip(),
                "product_name": (item.get("item_name") or title).strip(),
                "model_name": "",
                "image_url": _image(item) if item else "",
                "price": price,
                "currency": cur or currency,
                "stock": _stock(item.get("stock_info_v2")) if item else 0,
                "status": item.get("item_status") or "NORMAL",
                "updated_at": now,
            }
            conn.execute(
                """INSERT INTO shopee_products (
                    model_id, shop_id, region, item_id, seller_sku, product_name, model_name,
                    image_url, price, currency, stock, status, updated_at
                ) VALUES (
                    :model_id, :shop_id, :region, :item_id, :seller_sku, :product_name, :model_name,
                    :image_url, :price, :currency, :stock, :status, :updated_at
                )
                ON CONFLICT(model_id, shop_id) DO UPDATE SET
                    region=excluded.region, item_id=excluded.item_id,
                    seller_sku=CASE WHEN excluded.seller_sku != '' THEN excluded.seller_sku ELSE shopee_products.seller_sku END,
                    product_name=excluded.product_name, model_name=excluded.model_name,
                    image_url=CASE WHEN excluded.image_url != '' THEN excluded.image_url ELSE shopee_products.image_url END,
                    price=excluded.price, currency=excluded.currency,
                    stock=excluded.stock, status=excluded.status, updated_at=excluded.updated_at""",
                row,
            )
            inserted += conn.total_changes

    conn.commit()
    conn.close()
    return inserted


def global_item_id_for_match_key(match_key: str) -> str | None:
    key = parse_search_key(match_key)
    if not key:
        return None
    for gid, entry in load_map().items():
        if not isinstance(entry, dict):
            continue
        extra = entry.get("match_keys") or []
        if isinstance(extra, list):
            for mk in extra:
                if parse_search_key(str(mk)) == key:
                    return str(gid)
        if parse_search_key(str(entry.get("match_key") or "")) == key:
            return str(gid)
    return None


def global_item_id_for_shop_item(
    *,
    shop_id: int | None = None,
    item_id: str | int | None = None,
    model_id: str | int | None = None,
) -> str | None:
    """从全球映射反查 global_item_id（按店铺 item / model）。"""
    iid = str(item_id or "").strip()
    mid = str(model_id or "").strip()
    sid = int(shop_id or 0)
    for gid, entry in load_map().items():
        if not isinstance(entry, dict):
            continue
        shop_items = entry.get("shop_items") or {}
        if not isinstance(shop_items, dict):
            continue
        for ref in shop_items.values():
            if not isinstance(ref, dict):
                continue
            if sid and int(ref.get("shop_id") or 0) != sid:
                continue
            ref_iid = str(ref.get("item_id") or "").strip()
            ref_mid = str(ref.get("model_id") or "").strip()
            if iid and ref_iid == iid:
                return str(gid)
            if mid and ref_mid == mid:
                return str(gid)
    return None


def update_global_model_sku_in_map(global_item_id: str, global_model_sku: str, match_key: str = "") -> None:
    """推送成功后同步 shopee_global_sku_map.json。"""
    data = load_map()
    gid = str(global_item_id).strip()
    entry = data.get(gid)
    if not isinstance(entry, dict):
        if not match_key:
            return
        upsert_global_entry(gid, match_key=match_key, global_model_sku=global_model_sku)
        return
    models = entry.get("models") or []
    if models and isinstance(models[0], dict):
        models[0]["global_model_sku"] = global_model_sku
    else:
        entry["models"] = [{"model_name": "Default", "global_model_sku": global_model_sku}]
    if match_key:
        entry["match_key"] = parse_search_key(match_key)
    data[gid] = entry
    save_map(data)


def record_shop_item(
    global_item_id: str,
    region: str,
    *,
    shop_id: int,
    item_id: int | str,
    model_id: str = "",
    image_route_digest: str = "",
    image_ids: list[str] | None = None,
) -> None:
    data = load_map()
    gid = str(global_item_id).strip()
    entry = data.get(gid)
    if not isinstance(entry, dict):
        return
    shop_items = entry.setdefault("shop_items", {})
    if not isinstance(shop_items, dict):
        shop_items = {}
        entry["shop_items"] = shop_items
    clean_region = str(region).upper()
    replacement = {
        "shop_id": int(shop_id),
        "item_id": str(item_id),
        "model_id": str(model_id or f"item_{item_id}"),
    }
    if image_route_digest or image_ids:
        clean_ids = [str(value or "").strip() for value in (image_ids or [])]
        if (
            not str(image_route_digest or "").strip()
            or not clean_ids
            or any(not value for value in clean_ids)
            or len(set(clean_ids)) != len(clean_ids)
        ):
            raise ValueError("regional localized image binding is invalid")
        replacement["localized_images"] = {
            "image_route_digest": str(image_route_digest).strip(),
            "image_ids": clean_ids,
        }
    previous = shop_items.get(clean_region)
    if (
        isinstance(previous, dict)
        and str(previous.get("item_id") or "")
        and str(previous.get("item_id")) != replacement["item_id"]
    ):
        retired = entry.setdefault("retired_shop_items", {})
        if not isinstance(retired, dict):
            retired = {}
            entry["retired_shop_items"] = retired
        rows = retired.setdefault(clean_region, [])
        if not isinstance(rows, list):
            rows = []
            retired[clean_region] = rows
        retired_identity = {
            "shop_id": int(previous.get("shop_id") or shop_id),
            "item_id": str(previous.get("item_id") or ""),
            "model_id": str(previous.get("model_id") or ""),
            "reason": "replaced_after_official_seller_delete",
            "replacement_item_id": replacement["item_id"],
        }
        if not any(
            isinstance(row, dict)
            and row.get("item_id") == retired_identity["item_id"]
            and row.get("replacement_item_id")
            == retired_identity["replacement_item_id"]
            for row in rows
        ):
            rows.append(retired_identity)
    shop_items[clean_region] = replacement
    published_regions = {
        str(value).upper()
        for value in (entry.get("published_regions") or [])
        if str(value).strip()
    }
    published_regions.add(clean_region)
    entry["published_regions"] = sorted(published_regions)
    data[gid] = entry
    save_map(data)


def upsert_global_entry(
    global_item_id: str,
    *,
    match_key: str,
    global_model_sku: str,
    title: str = "",
    model_name: str = "Default",
    published_regions: list[str] | None = None,
    shop_items: dict | None = None,
) -> None:
    data = load_map()
    gid = str(global_item_id).strip()
    key = parse_search_key(match_key)
    data[gid] = {
        "match_key": key,
        "title": title,
        "global_item_sku": "",
        "models": [{"model_name": model_name, "global_model_sku": global_model_sku}],
        "published_regions": (
            list(published_regions) if published_regions is not None else []
        ),
        "shop_items": shop_items or {},
    }
    save_map(data)


def replace_deleted_global_entry(
    deleted_global_item_id: str,
    replacement_global_item_id: str,
    *,
    match_key: str,
    global_model_sku: str,
    title: str = "",
) -> None:
    """Retire one officially deleted global identity and bind its replacement.

    The old row is retained for audit but no longer participates in match-key
    lookup.  This prevents a later process restart from selecting the deleted
    identity again merely because it appears first in the JSON mapping.
    """

    data = load_map()
    old_gid = str(deleted_global_item_id or "").strip()
    new_gid = str(replacement_global_item_id or "").strip()
    key = parse_search_key(match_key)
    if (
        not old_gid
        or not new_gid
        or old_gid == new_gid
        or not key
        or not global_model_sku
    ):
        raise ValueError("deleted global replacement identity is invalid")
    old = data.get(old_gid)
    if not isinstance(old, dict):
        raise ValueError("deleted global mapping entry is unavailable")
    old_keys = {
        parse_search_key(str(old.get("match_key") or "")),
        *{
            parse_search_key(str(value))
            for value in (old.get("match_keys") or ())
        },
    }
    old_keys.discard("")
    if key not in old_keys:
        raise ValueError("deleted global mapping does not match the seller SKU")
    if isinstance(data.get(new_gid), dict):
        raise ValueError("replacement global identity already exists")

    retired = dict(old)
    retired["retired_match_key"] = key
    retired["match_key"] = ""
    retired["match_keys"] = []
    retired["retired_reason"] = "official_global_status_deleted"
    retired["replacement_global_item_id"] = new_gid
    data[old_gid] = retired
    data[new_gid] = {
        "match_key": key,
        "title": str(title or "").strip(),
        "global_item_sku": "",
        "models": [
            {
                "model_name": "Default",
                "global_model_sku": str(global_model_sku),
            }
        ],
        "published_regions": [],
        "shop_items": {},
        "replaces_deleted_global_item_id": old_gid,
    }
    save_map(data)


def replace_inexact_global_entry(
    inexact_global_item_id: str,
    replacement_global_item_id: str,
    *,
    match_key: str,
    global_model_sku: str,
    title: str = "",
) -> None:
    """Retire a mapped global item whose approved variant matrix is stale."""

    data = load_map()
    old_gid = str(inexact_global_item_id or "").strip()
    new_gid = str(replacement_global_item_id or "").strip()
    key = parse_search_key(match_key)
    old = data.get(old_gid)
    if (
        not old_gid
        or not new_gid
        or old_gid == new_gid
        or not key
        or not global_model_sku
        or not isinstance(old, dict)
        or isinstance(data.get(new_gid), dict)
    ):
        raise ValueError("inexact global replacement identity is invalid")
    old_keys = {
        parse_search_key(str(old.get("match_key") or "")),
        *{
            parse_search_key(str(value))
            for value in (old.get("match_keys") or ())
        },
    }
    old_keys.discard("")
    if key not in old_keys:
        raise ValueError("inexact global mapping does not match the seller SKU")
    retired = dict(old)
    retired["retired_match_key"] = key
    retired["match_key"] = ""
    retired["match_keys"] = []
    retired["retired_reason"] = "approved_variant_matrix_inexact"
    retired["replacement_global_item_id"] = new_gid
    data[old_gid] = retired
    data[new_gid] = {
        "match_key": key,
        "title": str(title or "").strip(),
        "global_item_sku": "",
        "models": [{
            "model_name": "Default",
            "global_model_sku": str(global_model_sku),
        }],
        "published_regions": [],
        "shop_items": {},
        "replaces_inexact_global_item_id": old_gid,
    }
    save_map(data)


def upsert_global_group_entry(
    global_item_id: str,
    *,
    match_keys: list[str],
    title: str = "",
    tier_name: str = "Color",
    models: list[dict] | None = None,
    tk_product_id: str = "",
    tk_source_region: str = "",
) -> None:
    """多规格全球商品写入 shopee_global_sku_map.json。"""
    keys = [parse_search_key(k) for k in match_keys if parse_search_key(k)]
    if not keys:
        return
    data = load_map()
    gid = str(global_item_id).strip()
    model_rows = []
    for m in models or []:
        sk = parse_search_key(str(m.get("global_model_sku") or ""))
        if not sk:
            continue
        model_rows.append(
            {
                "model_name": str(m.get("model_name") or sk),
                "global_model_sku": sk,
                "tk_sku_id": str(m.get("tk_sku_id") or ""),
                "tk_seller_sku": str(m.get("tk_seller_sku") or ""),
            }
        )
    existing = data.get(gid)
    entry = dict(existing) if isinstance(existing, dict) else {}
    entry.update({
        "match_key": keys[0],
        "match_keys": keys,
        "title": title,
        "tier_name": tier_name,
        "global_item_sku": keys[0],
        "models": model_rows,
        "tk_product_id": tk_product_id,
        "tk_source_region": tk_source_region,
    })
    if not isinstance(entry.get("published_regions"), list):
        entry["published_regions"] = []
    if not isinstance(entry.get("shop_items"), dict):
        entry["shop_items"] = {}
    data[gid] = entry
    save_map(data)
