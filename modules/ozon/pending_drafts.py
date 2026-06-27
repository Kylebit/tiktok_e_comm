"""待审草稿持久化：agent 生成草稿+裁图后存到这里，前端 /ozon 打开时加载成待审卡片。

存储文件：<ozon_data_dir>/pending_drafts.json
结构：{ "<seller_sku>": { ...draft 字段..., "processed_images": [...], "saved_at": <ts> } }

提交上品成功 / 人工忽略后，从队列删除对应 seller_sku。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from modules.ozon.config import ozon_data_dir


def _store_path() -> Path:
    data = ozon_data_dir()
    if not data:
        raise RuntimeError("找不到 Ozon data 目录，无法读写待审草稿")
    return data / "pending_drafts.json"


def _read() -> dict:
    path = _store_path()
    if not path.is_file():
        return {}
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write(data: dict) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_pending() -> list[dict]:
    """按保存时间升序返回所有待审草稿。"""
    items = list(_read().values())
    items.sort(key=lambda x: x.get("saved_at", 0))
    return items


def save_pending(payload: dict) -> dict:
    """保存/覆盖一个待审草稿。payload 需含 seller_sku；processed_images 可选。"""
    seller_sku = str(payload.get("seller_sku") or "").strip()
    if not seller_sku:
        raise ValueError("save_pending 需要 seller_sku")
    data = _read()
    record = dict(payload)
    record["saved_at"] = int(time.time())
    data[seller_sku] = record
    _write(data)
    return record


def delete_pending(seller_sku: str) -> bool:
    seller_sku = str(seller_sku or "").strip()
    data = _read()
    if seller_sku in data:
        del data[seller_sku]
        _write(data)
        return True
    return False


# ----------------------------------------------------------- 已忽略产品 -----
# 用户在前端点「忽略」的产品：记下来，从待搬运列表里永久排除，不再生成草稿/上品。

def _dismissed_path() -> Path:
    data = ozon_data_dir()
    if not data:
        raise RuntimeError("找不到 Ozon data 目录，无法读写已忽略列表")
    return data / "dismissed_offers.json"


def _read_dismissed() -> dict:
    path = _dismissed_path()
    if not path.is_file():
        return {}
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _offer_id_of(seller_sku: str) -> str:
    """4 位 offer_id：取 seller_sku 后 4 位（同一产品跨国 SKU 共享后 4 位）。"""
    s = str(seller_sku or "").strip()
    return s[-4:] if len(s) >= 4 else s


def list_dismissed() -> dict:
    return _read_dismissed()


def dismissed_seller_skus() -> set:
    return set(_read_dismissed().keys())


def dismissed_offer_ids() -> set:
    """已忽略产品的 4 位 offer_id 集合，用于跨国 SKU（如 880019/990019 同为 0019）一并排除。"""
    out = set()
    for k, rec in _read_dismissed().items():
        oid = (rec or {}).get("offer_id") or _offer_id_of(k)
        if oid:
            out.add(oid)
    return out


def add_dismissed(seller_sku: str, tk_id: str = "", reason: str = "") -> dict:
    seller_sku = str(seller_sku or "").strip()
    if not seller_sku:
        raise ValueError("add_dismissed 需要 seller_sku")
    data = _read_dismissed()
    record = {
        "seller_sku": seller_sku,
        "offer_id": _offer_id_of(seller_sku),
        "tk_id": str(tk_id or ""),
        "reason": reason,
        "at": int(time.time()),
    }
    data[seller_sku] = record
    path = _dismissed_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # 忽略后同时移出待审队列
    delete_pending(seller_sku)
    return record


def remove_dismissed(seller_sku: str) -> bool:
    """撤销忽略（恢复到待搬运列表）。"""
    seller_sku = str(seller_sku or "").strip()
    data = _read_dismissed()
    if seller_sku in data:
        del data[seller_sku]
        _dismissed_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    return False
