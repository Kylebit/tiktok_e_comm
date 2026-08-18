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


def archive_offer(ozon_post, product_id: int) -> dict:
    return ozon_post("/v1/product/archive", {"product_id": [int(product_id)]})


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
    archived = bool(info.get("is_archived"))
    mismatch = cur_cat != int(category_id) or cur_type != int(type_id)
    # A newly imported offer can be validation-successful while Ozon is still
    # assigning its public SKU (is_created=False). Deleting that transitional
    # offer makes a safe retry destructive and can create a loop. Only an
    # explicit terminal validation failure or decline is eligible for reset.
    # Moderation declines on an already-created card must be repaired in
    # place. Ozon rejects deletion of such cards with ITEM_IS_CREATED.
    # Only terminal schema validation failures are safe reset candidates.
    failed = validation in ("fail", "failed", "not_passed")
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

    product_id = info.get("id")
    if not archived:
        if isinstance(product_id, bool) or not isinstance(product_id, int) or product_id <= 0:
            return {
                "action": "delete_failed",
                "detail": "; ".join([*reason, "official product id is unavailable"]),
                "product_id": product_id,
            }
        archive_response = archive_offer(ozon_post, product_id)
        if archive_response.get("result") is not True:
            return {
                "action": "delete_failed",
                "detail": "; ".join([*reason, "archive rejected"]),
                "archive_response": archive_response,
                "product_id": product_id,
            }
        archived_info = fetch_offer_info(ozon_post, offer_id)
        if archived_info is None:
            return {
                "action": "deleted",
                "detail": "; ".join([*reason, "archive removed offer from lookup"]),
                "archive_response": archive_response,
                "product_id": product_id,
            }
        if not bool(archived_info.get("is_archived")):
            return {
                "action": "delete_failed",
                "detail": "; ".join([*reason, "archive not yet visible"]),
                "archive_response": archive_response,
                "product_id": product_id,
            }

    resp = delete_offer(ozon_post, offer_id)
    status = (resp.get("status") or [{}])[0]
    deleted = bool(status.get("is_deleted"))
    return {
        "action": "deleted" if deleted else "delete_failed",
        "detail": "; ".join(reason),
        "delete_response": resp,
        "product_id": info.get("id"),
    }
