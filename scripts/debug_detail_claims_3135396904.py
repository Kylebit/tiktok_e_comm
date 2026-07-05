from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm")
sys.path.insert(0, str(ROOT))

from modules.miaoshou.client import post_open  # type: ignore


DETAIL_ID = 3135396904
SHOPS = {
    "lh_ph": 7676267,
    "lh_my": 13295169,
    "lh_th": 13295228,
    "lh_vn": 13295291,
    "hb_ph": 15173238,
    "hb_my": 16770639,
    "hb_th": 16770557,
    "hb_vn": 16783702,
    "mx": 16265910,
    "gb": 10204699,
}


def main() -> None:
    out = {}
    for key, shop_id in SHOPS.items():
        try:
            resp = post_open(
                "/open/v1/product/collect_box/tiktok/collect_box/get_shop_collect_item_info",
                {"detailId": DETAIL_ID, "shopId": str(shop_id)},
            )
            data = resp.get("data") or {}
            info = data.get("shopCollectItemInfo") or {}
            out[key] = {
                "ok": resp.get("result"),
                "message": resp.get("message"),
                "claimToShopIds": data.get("claimToShopIds"),
                "title": info.get("title"),
                "cid": info.get("cid"),
                "img_count": len(info.get("imgUrls") or []),
                "sku_count": len(info.get("skuMap") or {}),
                "weight": info.get("weight"),
            }
        except Exception as exc:
            out[key] = {"error": str(exc)}
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
