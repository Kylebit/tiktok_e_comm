"""批量 Ozon 草稿入队：build_draft → save_pending（禁止跳过审核区直接上架）。"""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from typing import Callable

from modules.catalog.sku_key import tk_match_key
from modules.ozon.config import ozon_data_dir
from modules.ozon.ozon_dispatch import prep_one_ozon_draft
from modules.ozon.webapp_bridge import proxy_json


def _progress(cb: Callable[[str], None] | None, msg: str) -> None:
    if cb:
        cb(msg)
    else:
        print(msg)


def list_unmigrated() -> list[dict]:
    from modules.ozon.catalog_source import list_unmigrated_from_catalog

    return list_unmigrated_from_catalog()


def queue_one_to_pending(seller_sku: str, on_progress: Callable[[str], None] | None = None) -> dict:
    """单个 SKU：生成草稿并写入 pending_drafts 审核区。"""
    _progress(on_progress, f"  {seller_sku}: 入队草稿审核…")
    try:
        record = prep_one_ozon_draft(seller_sku, on_progress=on_progress, process_images=False)
        return {"seller_sku": seller_sku, "ok": True, "record": record}
    except Exception as exc:
        import traceback

        return {
            "seller_sku": seller_sku,
            "ok": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "traceback": traceback.format_exc()[-1200:],
        }


def queue_group_to_pending(
    group_id: str,
    *,
    items: list[dict] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> dict:
    """多规格组：每个变体分别 build_draft → save_pending，禁止直接 migrate。"""
    pool = items if items is not None else list_unmigrated()
    queue = [
        it
        for it in pool
        if str(it.get("tk_group_id") or "") == str(group_id) and not it.get("tk_dup")
    ]
    if not queue:
        return {"ok": False, "group_id": group_id, "queued": [], "errors": [{"error": "组内无可搬运规格"}]}

    _progress(on_progress, f"整组 {group_id}：{len(queue)} 个规格入队审核…")
    queued: list[dict] = []
    errors: list[dict] = []
    for i, it in enumerate(queue, 1):
        seller_sku = str(it.get("seller_sku") or it.get("offer_id") or "").strip()
        if not seller_sku:
            continue
        _progress(on_progress, f"  [{i}/{len(queue)}] {seller_sku}")
        res = queue_one_to_pending(seller_sku, on_progress=on_progress)
        if res.get("ok"):
            queued.append(res)
        else:
            errors.append(res)
    return {
        "ok": len(errors) == 0,
        "group_id": group_id,
        "queued": queued,
        "errors": errors,
        "count": len(queued),
    }


def migrate_one(seller_sku: str, on_progress: Callable[[str], None] | None = None) -> dict:
    """兼容旧调用：改为入队审核，不直接 import Ozon。"""
    return queue_one_to_pending(seller_sku, on_progress=on_progress)


def _poll_imported(offer_id: str, *, on_progress: Callable[[str], None] | None = None) -> str:
    """保留供 legacy_webapp 或其他模块 import；批量搬运不再调用。"""
    from modules.ozon.client import ozon_post

    for i in range(12):
        _progress(on_progress, f"  {offer_id}: Ozon 处理中，等待… ({i + 1}/12)")
        time.sleep(15)
        try:
            resp = ozon_post("/v3/product/info/list", {"offer_id": [offer_id]})
            if resp.get("items"):
                return "imported"
        except Exception:
            pass
    return "pending"


def _update_daily_summary(migrated_ids: list[str], errors: list[dict]) -> None:
    base = ozon_data_dir()
    if not base:
        return
    path = base / "daily_summary.json"
    today = str(date.today())
    data = {
        "date": today,
        "queued_for_review": migrated_ids,
        "queue_errors": errors,
    }
    try:
        status, red = proxy_json("GET", "red_prices")
        if status == 200 and isinstance(red, list):
            new_red = sum(1 for r in red if r.get("is_new"))
            data["red_count"] = len(red)
            data["new_red_count"] = new_red
    except Exception:
        pass
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def migrate_batch(
    count: int = 5,
    *,
    on_progress: Callable[[str], None] | None = None,
) -> dict:
    """待搬运前 count 个 SKU/组 → 草稿审核区（不直接上品）。"""
    pending = list_unmigrated()
    if not pending:
        return {"ok": True, "queued": [], "failed": [], "message": "无待搬运商品"}

    targets = pending[: max(1, count)]
    _progress(on_progress, f"待搬运 {len(pending)} 个，本次入队审核 {len(targets)} 个")

    queued: list[str] = []
    failed: list[dict] = []
    seen_groups: set[str] = set()

    for i, it in enumerate(targets, 1):
        seller_sku = str(it.get("seller_sku") or it.get("offer_id") or "").strip()
        oid = str(it.get("offer_id") or tk_match_key(seller_sku)).zfill(4)[-4:]
        group_id = str(it.get("tk_group_id") or "")

        if group_id:
            if group_id in seen_groups:
                continue
            seen_groups.add(group_id)
            _progress(on_progress, f"[{i}/{len(targets)}] 整组 {group_id}")
            res = queue_group_to_pending(group_id, items=pending, on_progress=on_progress)
            if res.get("ok"):
                queued.extend(str(r.get("seller_sku") or "") for r in res.get("queued") or [])
            else:
                failed.extend(res.get("errors") or [])
            continue

        _progress(on_progress, f"[{i}/{len(targets)}] {seller_sku} (offer {oid}) — {str(it.get('title') or '')[:50]}")
        res = queue_one_to_pending(seller_sku, on_progress=on_progress)
        if res.get("ok"):
            queued.append(seller_sku)
            _progress(on_progress, f"  ✅ {seller_sku} 已入队审核")
        else:
            failed.append(res)
            _progress(on_progress, f"  ❌ {seller_sku} {res.get('error')}")

    _update_daily_summary(queued, failed)
    return {
        "ok": len(failed) == 0,
        "queued": queued,
        "failed": failed,
        "remaining": max(0, len(pending) - len(targets)),
        "message": "草稿已入队审核区，请在 /ozon 审核后再提交 Ozon",
    }
