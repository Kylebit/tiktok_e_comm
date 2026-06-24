"""验证妙手开放平台：获取店铺数据列表。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.miaoshou.client import get_shop_list, load_config

SITES = ("TH", "PH", "MY", "VN")


def main() -> int:
    cfg = load_config()
    print(f"Base: {cfg.get('base_url')}")
    print(f"AppKey: {cfg['app_id'][:10]}...")

    for site in SITES:
        print(f"\n=== site={site} platform=tiktok ===")
        resp = get_shop_list("tiktok", site, page_no=1, page_size=20)
        print(json.dumps(resp, ensure_ascii=False, indent=2)[:2000])

        if resp.get("result") == "success":
            shops = (resp.get("data") or {}).get("shopList") or []
            print(f"shops: {len(shops)}")
            for s in shops[:5]:
                print(
                    f"  - {s.get('shopNick')} id={s.get('shopId')} "
                    f"status={s.get('status')} site={s.get('site')}"
                )
        else:
            print(f"FAIL code={resp.get('code')} message={resp.get('message') or resp.get('reason')}")
            if resp.get("code") in ("signInvalid", "signExpired", "signMissing", "appNotFound"):
                return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
