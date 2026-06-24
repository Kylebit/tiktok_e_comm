"""TikTok category_id → Ozon type_id / profile 映射表（可持久化、搬运成功后自动学习）。"""

from __future__ import annotations

import json
from datetime import datetime

from modules.ozon.config import ozon_data_dir

MAP_FILENAME = "tk_category_ozon_map.json"


def _map_path():
    base = ozon_data_dir()
    if not base:
        return None
    return base / MAP_FILENAME


def load_map() -> dict:
    path = _map_path()
    if not path or not path.is_file():
        return {"mappings": {}, "type_profiles": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"mappings": {}, "type_profiles": {}}
    if "mappings" not in data:
        data["mappings"] = {}
    if "type_profiles" not in data:
        data["type_profiles"] = {}
    return data


def save_map(data: dict) -> None:
    path = _map_path()
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def lookup(tk_category_id: str) -> dict | None:
    cid = str(tk_category_id or "").strip()
    if not cid:
        return None
    entry = load_map().get("mappings", {}).get(cid)
    return dict(entry) if isinstance(entry, dict) else None


def profile_for_type_id(type_id: int) -> str:
    data = load_map()
    tp = data.get("type_profiles") or {}
    return str(tp.get(str(type_id)) or tp.get(str(int(type_id))) or "generic")


def record_mapping(
    *,
    tk_category_id: str,
    tk_category_name: str = "",
    type_id: int,
    category_id: int,
    profile: str,
    source: str = "migrate",
) -> None:
    cid = str(tk_category_id or "").strip()
    if not cid:
        return
    data = load_map()
    data["mappings"][cid] = {
        "tk_category_name": tk_category_name,
        "type_id": int(type_id),
        "category_id": int(category_id),
        "profile": profile,
        "source": source,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    data.setdefault("type_profiles", {})[str(int(type_id))] = profile
    save_map(data)
