from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm")
sys.path.insert(0, str(ROOT))

from modules.miaoshou.client import post_open  # type: ignore


DETAIL_SHOPS = {
    3135395098: {"gb": 10204699},
    3135392955: {"gb": 10204699},
    3134301636: {"lh_vn": 13295291},
    3134298907: {"lh_vn": 13295291},
    3134295891: {"lh_vn": 13295291},
}


def main() -> None:
    out = {}
    for detail_id, shops in DETAIL_SHOPS.items():
        out[str(detail_id)] = {}
        for key, shop_id in shops.items():
            try:
                resp = post_open(
                    "/open/v1/product/collect_box/tiktok/collect_box/get_shop_collect_item_info",
                    {"detailId": detail_id, "shopId": str(shop_id)},
                )
                data = resp.get("data") or {}
                info = data.get("shopCollectItemInfo") or {}
                out[str(detail_id)][key] = {
                    "ok": resp.get("result"),
                    "message": resp.get("message"),
                    "claimToShopIds": data.get("claimToShopIds"),
                    "title": info.get("title"),
                    "cid": info.get("cid"),
                    "img_count": len(info.get("imgUrls") or []),
                    "sku_count": len(info.get("skuMap") or {}),
                }
            except Exception as exc:
                out[str(detail_id)][key] = {"error": str(exc)}
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
