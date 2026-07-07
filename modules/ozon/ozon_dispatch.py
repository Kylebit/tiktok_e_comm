"""Ozon 待审草稿入队（build_draft + 裁图 → pending_drafts.json）。"""
from __future__ import annotations

from typing import Any, Callable

from modules.catalog.sku_key import tk_match_key
from modules.ozon.catalog_draft import build_draft
from modules.ozon.catalog_source import list_unmigrated_from_catalog, to_4digit_offer_id
from modules.ozon.pending_drafts import list_pending, save_pending
from modules.ozon.webapp_bridge import proxy_json


def _pending_seller_skus() -> set[str]:
    return {str(d.get("seller_sku") or "").strip() for d in list_pending() if d.get("seller_sku")}


def _pending_offer_ids() -> set[str]:
    out: set[str] = set()
    for d in list_pending():
        sku = str(d.get("seller_sku") or "")
        oid = str(d.get("offer_id") or to_4digit_offer_id(sku))
        if oid:
            out.add(oid.zfill(4)[-4:])
    return out


def pick_ozon_candidates(*, limit: int) -> list[dict]:
    """未搬运、非重复行、不在待审队列。"""
    pending_skus = _pending_seller_skus()
    pending_oids = _pending_offer_ids()
    seen_offer: set[str] = set()
    seen_group: set[str] = set()
    out: list[dict] = []

    for it in list_unmigrated_from_catalog():
        if len(out) >= limit:
            break
        if it.get("tk_dup"):
            continue
        sku = str(it.get("seller_sku") or "").strip()
        oid = str(it.get("offer_id") or to_4digit_offer_id(sku)).zfill(4)[-4:]
        if not sku or not oid:
            continue
        if sku in pending_skus or oid in pending_oids:
            continue
        if oid in seen_offer:
            continue
        gid = str(it.get("tk_group_id") or "")
        if gid and gid in seen_group:
            continue
        seen_offer.add(oid)
        if gid:
            seen_group.add(gid)
        out.append(it)
    return out


def prep_one_ozon_draft(
    seller_sku: str,
    *,
    on_progress: Callable[[str], None] | None = None,
    process_images: bool = False,
) -> dict[str, Any]:
    def log(msg: str) -> None:
        if on_progress:
            on_progress(msg)
        else:
            print(msg, flush=True)

    log(f"  {seller_sku}: 生成俄语草稿…")
    draft = build_draft(seller_sku)
    if not isinstance(draft, dict) or (draft.get("error") and not draft.get("draft_title")):
        raise RuntimeError(f"草稿失败: {draft.get('error') if isinstance(draft, dict) else draft}")

    images_src = draft.get("images") or []
    if not images_src:
        raise RuntimeError(f"{seller_sku} 无源图")

    log(f"  {seller_sku}: 裁图 ({len(images_src)} 张)…")
    processed: list = []
    if process_images:
        try:
            status, proc = proxy_json("POST", f"process_images/{seller_sku}", payload={"images": images_src})
            if status == 200 and isinstance(proc, dict):
                processed = proc.get("images") or []
        except Exception as exc:
            log(f"  ⚠ 裁图跳过（打开 /ozon 时会自动裁）: {exc}")
    else:
        log(f"  {seller_sku}: 裁图留待审核页（入队更快）")

    payload = dict(draft)
    payload["seller_sku"] = seller_sku
    payload["images"] = images_src
    if processed:
        payload["processed_images"] = processed
    payload["offer_id"] = str(draft.get("offer_id") or to_4digit_offer_id(seller_sku))
    payload["match_key"] = tk_match_key(seller_sku)
    record = save_pending(payload)
    return {"ok": True, "seller_sku": seller_sku, "offer_id": payload["offer_id"], "record": record}


def queue_ozon_drafts(*, count: int, on_progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    candidates = pick_ozon_candidates(limit=count)
    queued: list[dict] = []
    errors: list[dict] = []
    for i, it in enumerate(candidates, 1):
        sku = str(it.get("seller_sku") or "")
        if on_progress:
            on_progress(f"[{i}/{len(candidates)}] Ozon {sku} · {str(it.get('title') or '')[:50]}")
        try:
            row = prep_one_ozon_draft(sku, on_progress=on_progress)
            row["title"] = it.get("title") or ""
            queued.append(row)
        except Exception as exc:
            errors.append({"seller_sku": sku, "offer_id": it.get("offer_id"), "error": str(exc)})
    return {
        "requested": count,
        "candidates": len(candidates),
        "queued": queued,
        "errors": errors,
        "ozon_url": "http://127.0.0.1:8765/ozon",
    }
