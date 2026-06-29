"""泰国店"孤儿商品"(无seller_sku，目录里没有真实尺寸数据)的尺寸人工核实。

这12个商品的包裹尺寸目前是 50x50x50cm 的占位默认值，导致开不了 Express Delivery 渠道。
人工核实真实尺寸后写回 Shopee，并重新尝试打开该渠道。
"""

from __future__ import annotations

from modules.shopee.auth import ensure_shop_token
from modules.shopee.client import merchant_post, resolve_global_item_id, shop_get, shop_post
from modules.shopee.publish import _merchant_token, _shop_meta

TH_SHOP_ID = 1561124013

ITEM_IDS = [
    49353310334, 46954047172, 45803294559, 49604936728, 46904051374,
    28694071141, 52162133950, 40627111232, 50203285744, 49154046989,
    50052820681, 45303299358,
]

EXPRESS_CHANNEL_ID = 7002
FULL_CHANNELS = [7000, 7001, 7002, 78004, 78014]


def list_items() -> list[dict]:
    token = ensure_shop_token(TH_SHOP_ID)
    res = shop_get(
        "/api/v2/product/get_item_base_info",
        TH_SHOP_ID,
        token,
        {"item_id_list": ",".join(str(i) for i in ITEM_IDS)},
    )
    items = res.get("response", {}).get("item_list", [])
    out = []
    for it in items:
        img = it.get("image") or {}
        urls = img.get("image_url_list") or []
        dim = it.get("dimension") or {}
        logistic = it.get("logistic_info") or []
        express_enabled = any(
            l.get("logistic_id") == EXPRESS_CHANNEL_ID and l.get("enabled") for l in logistic
        )
        out.append(
            {
                "item_id": it["item_id"],
                "name": it.get("item_name", ""),
                "image": urls[0] if urls else "",
                "weight_kg": it.get("weight"),
                "length_cm": dim.get("package_length"),
                "width_cm": dim.get("package_width"),
                "height_cm": dim.get("package_height"),
                "express_enabled": express_enabled,
            }
        )
    return out


def save_dimension(item_id: int, length_cm: float, width_cm: float, height_cm: float) -> dict:
    token = ensure_shop_token(TH_SHOP_ID)

    # CBSC(跨境)店铺：尺寸字段挂在 Global Item 上，Shop SKU 层不允许直接改
    meta = _shop_meta(TH_SHOP_ID, token)
    merchant_id = int(meta.get("merchant_id") or 0)
    if not merchant_id:
        return {"ok": False, "step": "dimension", "error": "无 merchant_id，无法定位 Global Item"}
    mtoken = _merchant_token(TH_SHOP_ID, token)
    gid = resolve_global_item_id(TH_SHOP_ID, merchant_id, mtoken, item_id)
    if not gid:
        return {"ok": False, "step": "dimension", "error": "未找到该商品的 global_item_id"}

    body = {
        "global_item_id": int(gid),
        "dimension": {
            "package_length": round(length_cm),
            "package_width": round(width_cm),
            "package_height": round(height_cm),
        },
    }
    res = merchant_post("/api/v2/global_product/update_global_item", merchant_id, mtoken, body)
    if res.get("error"):
        return {"ok": False, "step": "dimension", "error": res.get("message") or res.get("error")}

    # 尺寸改完后重新尝试打开全部渠道(包括之前因尺寸超限被拒的Express)
    logistic_info = [
        {"logistic_id": c, "enabled": True, "shipping_fee": 0, "size_id": 0, "is_free": False}
        for c in FULL_CHANNELS
    ]
    res2 = shop_post(
        "/api/v2/product/update_item", TH_SHOP_ID, token, {"item_id": item_id, "logistic_info": logistic_info}
    )
    if res2.get("error"):
        return {
            "ok": True,
            "dimension_saved": True,
            "express_enabled": False,
            "express_error": res2.get("message") or res2.get("error"),
        }
    return {"ok": True, "dimension_saved": True, "express_enabled": True}
