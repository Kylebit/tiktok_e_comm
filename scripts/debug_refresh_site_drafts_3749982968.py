from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm")
sys.path.insert(0, str(ROOT))

from modules.sourcing.new_product_workbench import prepare_miaoshou_site_drafts  # type: ignore


def main() -> None:
    result = prepare_miaoshou_site_drafts("3749982968")
    simplified = {
        "ok": result.get("ok"),
        "offer_id": result.get("offer_id"),
        "tiktok_detail_id": result.get("tiktok_detail_id"),
        "blocked_sites": result.get("blocked_sites"),
        "ready": result.get("ready"),
        "sites": {},
    }
    for region, row in (result.get("sites") or {}).items():
        simplified["sites"][region] = {
            "mode": row.get("mode"),
            "shop_ids": row.get("shop_ids"),
            "site_collect_shop_ids": row.get("site_collect_shop_ids"),
            "verified_claim_shop_ids": row.get("verified_claim_shop_ids"),
            "ready": row.get("ready"),
            "checks": row.get("checks"),
            "shop_results": [
                {
                    "target_id": shop_row.get("target_id"),
                    "shop_name": shop_row.get("shop_name"),
                    "shop_ids": shop_row.get("shop_ids"),
                    "verified_claim_shop_ids": shop_row.get("verified_claim_shop_ids"),
                    "ready": shop_row.get("ready"),
                }
                for shop_row in (row.get("shop_results") or [])
            ],
        }
    print(json.dumps(simplified, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
