from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm")
sys.path.insert(0, str(ROOT))

from modules.sourcing.new_product_workbench import (  # type: ignore
    claim_miaoshou_to_tiktok,
    prepare_miaoshou_site_drafts,
)


def simplify_site_drafts(result: dict) -> dict:
    out = {
        "ok": result.get("ok"),
        "offer_id": result.get("offer_id"),
        "tiktok_detail_id": result.get("tiktok_detail_id"),
        "ready": result.get("ready"),
        "blocked_sites": result.get("blocked_sites"),
        "sites": {},
    }
    for region, row in (result.get("sites") or {}).items():
        out["sites"][region] = {
            "mode": row.get("mode"),
            "shop_ids": row.get("shop_ids"),
            "site_collect_shop_ids": row.get("site_collect_shop_ids"),
            "verified_claim_shop_ids": row.get("verified_claim_shop_ids"),
            "ready": row.get("ready"),
        }
        if row.get("shop_results"):
            out["sites"][region]["shop_results"] = [
                {
                    "target_id": shop_row.get("target_id"),
                    "shop_name": shop_row.get("shop_name"),
                    "shop_ids": shop_row.get("shop_ids"),
                    "verified_claim_shop_ids": shop_row.get("verified_claim_shop_ids"),
                    "ready": shop_row.get("ready"),
                }
                for shop_row in row.get("shop_results") or []
            ]
    return out


def main() -> None:
    offer_id = "3749982968"
    claim = claim_miaoshou_to_tiktok(offer_id)
    site = prepare_miaoshou_site_drafts(offer_id)
    print(json.dumps({
        "claim": {
            "ok": claim.get("ok"),
            "offer_id": claim.get("offer_id"),
            "tiktok_detail_id": claim.get("tiktok_detail_id"),
            "claimed": claim.get("claimed"),
            "blocked_sites": claim.get("blocked_sites"),
            "shop_keys": sorted((claim.get("shops") or {}).keys()),
        },
        "site_drafts": simplify_site_drafts(site),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
