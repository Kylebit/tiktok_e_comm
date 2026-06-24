"""Ozon 商品生命周期：迁移前清理错误/归档卡片。"""

from __future__ import annotations


def fetch_offer_info(ozon_post, offer_id: str) -> dict | None:
    resp = ozon_post("/v3/product/info/list", {"offer_id": [str(offer_id)]})
    items = resp.get("items") or resp.get("result", {}).get("items") or []
    if not items:
        return None
    return items[0]


def delete_offer(ozon_post, offer_id: str) -> dict:
    return ozon_post("/v2/products/delete", {"products": [{"offer_id": str(offer_id)}]})


def ensure_offer_reset(
    ozon_post,
    offer_id: str,
    *,
    category_id: int,
    type_id: int,
) -> dict:
    """
    若 Ozon 已有同 offer_id 且类目不一致 / 校验失败 / 已归档，先删除再允许重新 import。
    返回 {action, detail}.
    """
    info = fetch_offer_info(ozon_post, offer_id)
    if not info:
        return {"action": "none", "detail": "not_on_ozon"}

    cur_cat = int(info.get("description_category_id") or 0)
    cur_type = int(info.get("type_id") or 0)
    statuses = info.get("statuses") or {}
    validation = (statuses.get("validation_status") or "").lower()
    is_created = bool(statuses.get("is_created"))
    archived = bool(info.get("is_archived"))
    declined = bool(statuses.get("decline_reasons") or statuses.get("status_failed"))

    mismatch = cur_cat != int(category_id) or cur_type != int(type_id)
    failed = validation in ("fail", "failed", "not_passed") or not is_created or declined
    needs_reset = mismatch or failed or archived

    if not needs_reset:
        return {
            "action": "keep",
            "detail": f"existing cat={cur_cat} type={cur_type}",
            "product_id": info.get("id"),
        }

    reason = []
    if mismatch:
        reason.append(f"category {cur_cat}/{cur_type} -> {category_id}/{type_id}")
    if failed:
        reason.append(f"validation={validation or '?'}")
    if archived:
        reason.append("archived")

    resp = delete_offer(ozon_post, offer_id)
    status = (resp.get("status") or [{}])[0]
    deleted = bool(status.get("is_deleted"))
    return {
        "action": "deleted" if deleted else "delete_failed",
        "detail": "; ".join(reason),
        "delete_response": resp,
        "product_id": info.get("id"),
    }
