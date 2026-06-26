"""批量 Ozon 上品：草稿 → 3:4 图 → import（与 ozon/webapp UI 相同流程）。"""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from typing import Callable

from modules.ozon.config import ozon_data_dir
from modules.ozon.price_review import scan_red_prices


def _progress(cb: Callable[[str], None] | None, msg: str) -> None:
    if cb:
        cb(msg)
    else:
        print(msg)


def list_unmigrated() -> list[dict]:
    from modules.ozon.catalog_source import list_unmigrated_from_catalog

    return list_unmigrated_from_catalog()


def migrate_one(seller_sku: str, on_progress: Callable[[str], None] | None = None) -> dict:
    """seller_sku: 6位 TK 货号；migrate  payload 用 draft 返回的 4位 offer_id。"""
    _progress(on_progress, f"  {seller_sku}: 生成俄语草稿…")
    from modules.ozon.catalog_draft import build_draft

    draft = build_draft(seller_sku)
    if not isinstance(draft, dict) or (draft.get("error") and not draft.get("draft_title")):
        return {"seller_sku": seller_sku, "ok": False, "step": "draft", "error": draft}

    offer_id = str(draft.get("offer_id") or seller_sku)
    images_src = draft.get("images") or []
    if not images_src:
        return {"seller_sku": seller_sku, "offer_id": offer_id, "ok": False, "step": "draft", "error": "无源图"}

    _progress(on_progress, f"  {seller_sku}: 转换并上传图片 ({len(images_src)} 张)…")
    status, proc = proxy_json(
        "POST",
        f"process_images/{seller_sku}",
        payload={"images": images_src},
    )
    if status != 200 or not isinstance(proc, dict):
        return {"seller_sku": seller_sku, "offer_id": offer_id, "ok": False, "step": "images", "error": proc}
    images = proc.get("images") or []
    if not images:
        return {"seller_sku": seller_sku, "offer_id": offer_id, "ok": False, "step": "images", "error": "图片处理失败"}

    payload = {
        "offer_id": offer_id,
        "images": images,
        "title": draft.get("draft_title") or "",
        "description": draft.get("draft_description") or "",
        "price": str(draft.get("price") or "45"),
        "old_price": str(draft.get("old_price") or "62"),
        "color_name": draft.get("color_name") or "",
        "color_dict_id": draft.get("color_dict_id") or "",
        "material": draft.get("material") or "ПВХ (поливинилхлорид)",
        "material_dict_id": draft.get("material_dict_id") or 61996,
        "hashtags": draft.get("hashtags") or "",
        "kit": draft.get("kit") or "",
        "weight": draft.get("weight") or "",
        "depth": draft.get("depth") or "",
        "width": draft.get("width") or "",
        "height": draft.get("height") or "",
        "len_cm": draft.get("len_cm") or "",
        "wid_cm": draft.get("wid_cm") or "",
        "category_id": draft.get("category_id") or "",
        "type_id": draft.get("type_id") or "",
        "migrate_profile": draft.get("migrate_profile") or "generic",
        "tk_category_id": draft.get("tk_category_id") or "",
        "tk_category_leaf": draft.get("tk_category_leaf") or "",
    }

    _progress(on_progress, f"  {offer_id}: 提交 Ozon import（含 Rich 内容，可能需数分钟）…")
    status, result = proxy_json("POST", "migrate", payload=payload)
    if status != 200 or not isinstance(result, dict):
        return {"seller_sku": seller_sku, "offer_id": offer_id, "ok": False, "step": "migrate", "error": result}

    import_status = result.get("status")
    if import_status == "pending":
        import_status = _poll_imported(offer_id, on_progress=on_progress)

    ok = import_status == "imported"
    return {
        "seller_sku": seller_sku,
        "offer_id": offer_id,
        "ok": ok,
        "status": import_status,
        "rich_status": result.get("rich_status"),
        "errors": result.get("errors") or [],
        "title": payload["title"],
    }


def _poll_imported(offer_id: str, *, on_progress: Callable[[str], None] | None = None) -> str:
    """Ozon import 异步时多等一会儿再查 offer 是否已上线。"""
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
        "migrated": migrated_ids,
        "migrate_errors": errors,
    }
    try:
        red = scan_red_prices()
        if isinstance(red, list):
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
    """搬运队列前 count 个待上品 SKU。"""
    pending = list_unmigrated()
    if not pending:
        return {"ok": True, "migrated": [], "failed": [], "message": "无待搬运商品"}

    targets = pending[: max(1, count)]
    _progress(on_progress, f"待搬运 {len(pending)} 个，本次处理 {len(targets)} 个")

    migrated: list[str] = []
    failed: list[dict] = []
    for i, it in enumerate(targets, 1):
        seller_sku = it.get("seller_sku") or it["offer_id"]
        oid = it["offer_id"]
        _progress(on_progress, f"[{i}/{len(targets)}] {seller_sku} (offer {oid}) — {it.get('title', '')[:50]}")
        try:
            res = migrate_one(seller_sku, on_progress=on_progress)
            res["offer_id"] = oid
        except Exception as e:
            res = {"offer_id": oid, "ok": False, "error": str(e)}
        if res.get("ok"):
            migrated.append(oid)
            _progress(on_progress, f"  ✅ {oid} imported (rich: {res.get('rich_status')})")
        else:
            failed.append(res)
            _progress(on_progress, f"  ❌ {oid} {res.get('step') or ''} {res.get('error') or res.get('status')}")

    _update_daily_summary(migrated, failed)
    return {
        "ok": len(failed) == 0,
        "migrated": migrated,
        "failed": failed,
        "remaining": max(0, len(pending) - len(targets)),
    }
