"""待审草稿持久化：agent 生成草稿+裁图后存到这里，前端 /ozon 打开时加载成待审卡片。

存储文件：<ozon_data_dir>/pending_drafts.json
结构：{ "<seller_sku>": { ...draft 字段..., "processed_images": [...], "saved_at": <ts> } }

提交上品成功 / 人工忽略后，从队列删除对应 seller_sku。
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from modules.ozon.config import ozon_data_dir


def title_fingerprint(title_ms: str) -> str:
    """Stable hash of TikTok source title for stale-draft detection."""
    normalized = (title_ms or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


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


def get_pending(seller_sku: str) -> dict | None:
    rec = _read().get(str(seller_sku or "").strip())
    return rec if isinstance(rec, dict) else None


def pending_is_fresh(record: dict, title_ms: str) -> bool:
    """True when stored pending was generated from the same TikTok title."""
    if not isinstance(record, dict):
        return False
    stored_fp = (record.get("title_fingerprint") or "").strip()
    current_fp = title_fingerprint(title_ms)
    if stored_fp:
        return stored_fp == current_fp
    stored_title = (record.get("title_ms") or "").strip()
    if stored_title:
        return stored_title == (title_ms or "").strip()
    return False


def _catalog_title_ms(seller_sku: str) -> str | None:
    """Current TikTok title from catalog; None if SKU not found."""
    try:
        from modules.ozon.catalog_source import _map_entry_from_item, catalog_item_by_seller_sku

        item = catalog_item_by_seller_sku(seller_sku)
        if not item:
            return None
        entry = _map_entry_from_item(item, fetch_detail=False, seller_sku=seller_sku)
        if not entry:
            return None
        return (entry.get("title") or "").strip()
    except Exception:
        return None


def _purge_stale_in_store(data: dict) -> tuple[dict, int]:
    """Drop pending drafts whose TikTok title no longer matches catalog."""
    removed = 0
    out = dict(data)
    for sku in list(out.keys()):
        rec = out.get(sku)
        if not isinstance(rec, dict):
            continue
        current_title = _catalog_title_ms(sku)
        if current_title is None:
            continue
        if pending_is_fresh(rec, current_title):
            continue
        del out[sku]
        removed += 1
    return out, removed


def invalidate_stale_pending(seller_sku: str, title_ms: str) -> bool:
    """Remove stored pending draft when TikTok title changed. Returns True if removed."""
    seller_sku = str(seller_sku or "").strip()
    rec = get_pending(seller_sku)
    if not rec or pending_is_fresh(rec, title_ms):
        return False
    delete_pending(seller_sku)
    return True


def list_pending(*, purge_stale: bool = True) -> list[dict]:
    """按保存时间升序返回所有待审草稿；默认剔除 TikTok 标题已变的过期记录。"""
    data = _read()
    if purge_stale and data:
        fresh_data, removed = _purge_stale_in_store(data)
        if removed:
            _write(fresh_data)
            data = fresh_data
    items = [x for x in data.values() if isinstance(x, dict)]
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
    title_ms = str(record.get("title_ms") or "").strip()
    if title_ms:
        record["title_fingerprint"] = title_fingerprint(title_ms)
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
