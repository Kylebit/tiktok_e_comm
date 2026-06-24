"""Probe Miaoshou TikTok MX shop and collect box for publish pilot."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.miaoshou.client import get_shop_list, post_open

LIST_PATH = "/open/v1/product/collect_box/tiktok/collect_box/search_collect_box_detail_list"


def main() -> int:
    print("=== TikTok shops by site ===")
    for site in ["MX", "US", "GB", "TH", "PH", "MY", "VN", "SG", "ID", "JP"]:
        r = get_shop_list("tiktok", site, page_size=20)
        shops = (r.get("data") or {}).get("shopList") or []
        print(f"{site}: code={r.get('code')} shops={len(shops)}")
        for s in shops:
            print(
                f"  id={s.get('shopId')} nick={s.get('shopNick')} "
                f"parent={s.get('parentShopId')} status={s.get('status')}"
            )

    for status in ("notPublished", "published"):
        body = {
            "pageNo": 1,
            "pageSize": 5,
            "filter": {"status": status, "sourceItemIdKeyword": ""},
        }
        r = post_open(LIST_PATH, body)
        data = r.get("data") or {}
        items = data.get("detailList") or []
        print(f"\n=== collect box status={status} total={data.get('total')} ===")
        if r.get("code") not in ("200", "success") and r.get("result") != "success":
            print(json.dumps(r, ensure_ascii=False)[:1500])
            continue
        for it in items[:5]:
            shops = it.get("collectBoxDetailShopList") or []
            shop_bits = [f"{s.get('site')}:{s.get('shopId')}" for s in shops[:6]]
            print(
                f"  detailId={it.get('detailId')} "
                f"title={str(it.get('title', ''))[:60]} shops={shop_bits}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
