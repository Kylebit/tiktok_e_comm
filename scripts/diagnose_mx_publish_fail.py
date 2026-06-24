"""Diagnose why MX publish did not appear in shop backend."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.miaoshou.client import post_open

TK = 2059296237
MX = 16265910
MY = 14820702
CID = "600338"

ONLINE_PATHS = [
    "/open/v1/product/shop/shop/get_online_product_list",
    "/open/v1/product/shop/shop/get_manage_product_list",
    "/open/v1/product/shop/shop/get_shop_product_list",
    "/open/v1/product/shop/shop/get_product_list",
    "/open/v1/product/shop/shop/get_shop_data_list",
]

TASK_PATHS = [
    "/open/v1/product/collect_box/tiktok/collect_box/get_move_collect_task_list",
    "/open/v1/product/collect_box/tiktok/collect_box/search_move_collect_task_list",
    "/open/v1/product/collect_box/tiktok/collect_box/get_publish_task_list",
    "/open/v1/product/collect_box/tiktok/collect_box/search_publish_task_list",
    "/open/v1/product/collect_box/tiktok/collect_box/get_move_collect_task",
]


def probe_paths(paths: list[str], body: dict) -> None:
    for path in paths:
        r = post_open(path, body)
        ok = r.get("result") == "success" or r.get("code") in ("200", "success", "0")
        msg = r.get("message") or r.get("reason") or ""
        preview = json.dumps(r, ensure_ascii=False)[:600]
        print(f"{path}\n  ok={ok} code={r.get('code')} msg={msg}\n  {preview}\n")


def readiness_issues(info: dict) -> list[str]:
    issues = []
    for f in ("title", "notes", "cid"):
        if not info.get(f):
            issues.append(f"missing_{f}")
    if not info.get("imgUrls"):
        issues.append("missing_images")
    for dim in ("packageLength", "packageWidth", "packageHeight"):
        if not info.get(dim):
            issues.append(f"missing_{dim}")
    if not info.get("weight"):
        issues.append("missing_weight")
    attrs = info.get("productAttributes") or []
    if attrs == []:
        issues.append("empty_productAttributes")
    sku_map = info.get("skuMap") or {}
    if not sku_map:
        issues.append("empty_skuMap")
    for k, sku in sku_map.items():
        if float(sku.get("weight") or 0) <= 0:
            issues.append(f"sku_{k}_weight")
        if not sku.get("price"):
            issues.append(f"sku_{k}_price")
        if not sku.get("shopIdToWarehouseIdAndStockMap"):
            issues.append(f"sku_{k}_warehouse")
    return issues


def main() -> int:
    print("=== MX vs MY readiness ===")
    for sid, label in [(MX, "MX"), (MY, "MY")]:
        rd = post_open(
            "/open/v1/product/collect_box/tiktok/collect_box/get_shop_collect_item_info",
            {"detailId": TK, "shopId": sid},
        )
        info = (rd.get("data") or {}).get("shopCollectItemInfo") or {}
        issues = readiness_issues(info)
        print(f"\n{label} shopId={sid}")
        print(f"  issues: {issues}")
        print(
            f"  pkg={info.get('packageLength')}x{info.get('packageWidth')}x{info.get('packageHeight')} "
            f"attrs={len(info.get('productAttributes') or [])} imgs={len(info.get('imgUrls') or [])}"
        )

    print("\n=== MX category metadata ===")
    meta = post_open(
        "/open/v1/product/collect_box/tiktok/collect_box/get_category_metadata",
        {"site": "MX", "cid": int(CID), "shopIds": [MX]},
    )
    print(json.dumps(meta, ensure_ascii=False)[:4000])

    print("\n=== online/manage product list probes (MX) ===")
    body = {"platform": "tiktok", "site": "MX", "shopId": MX, "pageNo": 1, "pageSize": 20}
    probe_paths(ONLINE_PATHS, body)

    print("=== publish task probes ===")
    task_bodies = [
        {"pageNo": 1, "pageSize": 20, "shopIds": [MX]},
        {"pageNo": 1, "pageSize": 20, "detailIds": [TK], "shopIds": [MX]},
        {"detailId": TK, "shopId": MX},
    ]
    for path in TASK_PATHS:
        for body in task_bodies:
            r = post_open(path, body)
            if r.get("result") == "success" or "task" in json.dumps(r).lower():
                print(path, "body", body)
                print(json.dumps(r, ensure_ascii=False)[:1200])
                print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
