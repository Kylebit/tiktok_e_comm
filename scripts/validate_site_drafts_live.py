import json
import sys
import time

sys.path.insert(0, r"C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm")

from modules.miaoshou.client import post_open  # type: ignore


CHECKS = [
    ("PH_LH", 3137891751, "7676267"),
    ("PH_HB", 3137891751, "15173238"),
    ("MY_LH", 3137891751, "13295169"),
    ("MY_HB", 3137891751, "16770639"),
    ("TH_LH", 3137891751, "13295228"),
    ("TH_HB", 3137891751, "16770557"),
    ("VN_LH", 3134301636, "13295291"),
    ("VN_HB", 3137891751, "16783702"),
    ("MX", 3137891751, "16265910"),
    ("GB", 3137871374, "10204699"),
]


def main() -> None:
    out = []
    for label, detail_id, shop_id in CHECKS:
        started = time.time()
        try:
            resp = post_open(
                "/open/v1/product/collect_box/tiktok/collect_box/get_shop_collect_item_info",
                {"detailId": detail_id, "shopId": shop_id},
            )
            data = resp.get("data") or {}
            info = data.get("shopCollectItemInfo") or {}
            out.append(
                {
                    "label": label,
                    "seconds": round(time.time() - started, 2),
                    "result": resp.get("result"),
                    "code": resp.get("code"),
                    "message": resp.get("message"),
                    "detail_id": detail_id,
                    "shop_id": shop_id,
                    "has_info": bool(info),
                    "claim_to_shop_ids": data.get("claimToShopIds") or [],
                    "title": info.get("title"),
                    "cid": info.get("cid"),
                    "img_count": len(info.get("imgUrls") or []),
                    "video": bool(info.get("mainImgVideoUrl")),
                    "sku_count": len((info.get("skuMap") or {}).keys()),
                    "cod": info.get("isCodOpen"),
                    "item_num": next(iter((info.get("skuMap") or {}).values()), {}).get("itemNum")
                    if info.get("skuMap")
                    else None,
                }
            )
        except Exception as exc:  # pragma: no cover - live-only helper
            out.append(
                {
                    "label": label,
                    "seconds": round(time.time() - started, 2),
                    "detail_id": detail_id,
                    "shop_id": shop_id,
                    "error": str(exc),
                }
            )
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
