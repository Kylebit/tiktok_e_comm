"""从 Ozon webapp JSON + API 快照读取商品（与 tk_sku_map 对齐）。"""

from __future__ import annotations

import json
from pathlib import Path

from modules.catalog.sku_key import tk_match_key
from modules.ozon.config import ozon_data_dir as _ozon_data_dir
from modules.catalog.ozon_offline_data import load_ozon_from_directory


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

    return load_ozon_from_directory(base, attrs_reader=_attrs_items, key_resolver=tk_match_key)
