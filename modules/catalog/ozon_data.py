"""从 Ozon webapp JSON + API 快照读取商品（与 tk_sku_map 对齐）。"""

from __future__ import annotations

import json
from pathlib import Path

from modules.catalog.sku_key import tk_match_key
from modules.ozon.config import ozon_data_dir as _ozon_data_dir


def _ozon_dir() -> Path | None:
    return _ozon_data_dir()


def _attrs_items(base: Path) -> list[dict]:
    attrs_path = base / "all_products_attrs.json"
    if not attrs_path.is_file():
        return []
    try:
        data = json.loads(attrs_path.read_text(encoding="utf-8"))
        items = data.get("result") if isinstance(data, dict) else data
        return items if isinstance(items, list) else []
    except json.JSONDecodeError:
        return []


def load_ozon_by_key() -> dict[str, dict]:
    """match_key → ozon 行（API 已上架优先，tk_sku_map 补待迁移）。"""
    base = _ozon_dir()
    if not base:
        return {}

    migrated: set[str] = set()
    mig_path = base / "migrated_offers.json"
    if mig_path.is_file():
        try:
            raw = json.loads(mig_path.read_text(encoding="utf-8"))
            migrated = set(str(x) for x in raw) if isinstance(raw, list) else set()
        except json.JSONDecodeError:
            migrated = set()

    tk_map: dict = {}
    tk_path = base / "tk_sku_map.json"
    if tk_path.is_file():
        try:
            tk_map = json.loads(tk_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            tk_map = {}

    out: dict[str, dict] = {}

    for it in _attrs_items(base):
        oid = str(it.get("offer_id") or "").strip()
        mk = tk_match_key(oid)
        if not mk:
            continue
        imgs = it.get("images") or []
        image_url = it.get("primary_image") or (imgs[0] if imgs else "")
        out[mk] = {
            "platform": "ozon",
            "match_key": mk,
            "seller_sku": oid,
            "offer_id": oid,
            "product_id": str(it.get("sku") or it.get("id") or ""),
            "product_name": (it.get("name") or "").strip(),
            "image_url": image_url,
            "migrated": True,
            "status": "live",
            "tk_id": "",
        }
        migrated.add(oid)

    for key, row in tk_map.items():
        if not isinstance(row, dict):
            continue
        seller_sku = str(row.get("seller_sku") or "").strip()
        match_key = tk_match_key(seller_sku) or str(key).zfill(4)
        if match_key in out:
            if not out[match_key].get("tk_id") and row.get("tk_id"):
                out[match_key]["tk_id"] = str(row.get("tk_id") or "")
            continue
        imgs = row.get("image_urls") or []
        offer_id = seller_sku or match_key
        migrated_flag = offer_id in migrated
        out[match_key] = {
            "platform": "ozon",
            "match_key": match_key,
            "seller_sku": seller_sku,
            "offer_id": offer_id,
            "product_id": str(row.get("tk_id") or ""),
            "product_name": (row.get("title") or "").strip(),
            "image_url": imgs[0] if imgs else "",
            "migrated": migrated_flag,
            "status": "live" if migrated_flag else "pending",
            "tk_id": str(row.get("tk_id") or ""),
        }

    return out
