"""MX 批量搬运：公共采集箱 → POP 定价 → 确认卡片 → 首次上架。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.catalog.logistics_weights import sync_match_key_weights
from modules.miaoshou.mx_confirm import (
    MX_EXIT_NEEDS_USER_CONFIRM,
    prepare_mx_publish_confirm,
)
from modules.miaoshou.mx_migrate import claim_common_to_tiktok, collect_master_images_and_product
from modules.miaoshou.mx_publish import publish_mx_listing
from scripts.mx_pop_pricing import KNOWN_BY_MATCH_KEY, fetch_cny_mxn, load_catalog_row, quote_match_key

STOCK = 200

# 贴纸1 公共采集箱 → 母版（0001 无 PH，用 MY）
MX_BATCH = [
    {
        "match_key": "0001",
        "common_collect_box_detail_id": 3579516381,
        "master_sku": "660001",
        "master_product_id": "1731455264245712827",
        "master_region": "MY",
    },
    {
        "match_key": "0005",
        "common_collect_box_detail_id": 3579516409,
        "master_sku": "770005",
        "master_product_id": "1731708191695734715",
        "master_region": "PH",
    },
    {
        "match_key": "0006",
        "common_collect_box_detail_id": 3579516334,
        "master_sku": "770006",
        "master_product_id": "1733047965872588731",
        "master_region": "PH",
    },
    {
        "match_key": "0007",
        "common_collect_box_detail_id": 3579516410,
        "master_sku": "770007",
        "master_product_id": "1731502295464839099",
        "master_region": "PH",
    },
    {
        "match_key": "0008",
        "common_collect_box_detail_id": 3579516378,
        "master_sku": "770008",
        "master_product_id": "1731516982024767419",
        "master_region": "PH",
    },
]


def _known_package_cm(match_key: str) -> tuple[int, int, int] | None:
    known = KNOWN_BY_MATCH_KEY.get(match_key, {})
    if known.get("l"):
        return int(known["l"]), int(known["w"]), int(known["h"])
    return None


def _product_name(seller_sku: str, master_product_id: str, master_region: str) -> str:
    try:
        row = load_catalog_row(seller_sku)
        if row.get("product_name"):
            return str(row["product_name"])
    except RuntimeError:
        pass
    urls, product = collect_master_images_and_product(master_product_id, region=master_region)
    return str(product.get("title") or seller_sku)


def _main_image(seller_sku: str, master_product_id: str, master_region: str) -> str:
    urls, _ = collect_master_images_and_product(master_product_id, region=master_region)
    return urls[0] if urls else ""


def main() -> int:
    ap = argparse.ArgumentParser(description="MX 批量搬运：公共采集箱 → POP → 确认卡片 → 首次上架")
    ap.add_argument(
        "--skip-confirm",
        action="store_true",
        help="跳过确认卡片（仅调试，勿用于正式上架）",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="仅 POP 测算，不认领/确认/上架",
    )
    ap.add_argument(
        "--interactive",
        action="store_true",
        help="终端内逐个输入 确认/取消",
    )
    ap.add_argument(
        "--confirm-token",
        help="继续已确认的 token（配合 --user-approved）",
    )
    ap.add_argument("--user-approved", action="store_true")
    args = ap.parse_args()

    keys = [r["match_key"] for r in MX_BATCH]
    print(">>> 同步物流重量（四国合并）:", ", ".join(keys))
    sync_match_key_weights(keys, on_progress=print, force_refresh=False)

    tk_map: dict[int, int] = {}
    if not args.dry_run:
        print("\n>>> 公共采集箱 → TikTok 平台采集箱")
        common_ids = [r["common_collect_box_detail_id"] for r in MX_BATCH]
        tk_map = claim_common_to_tiktok(common_ids)
        for row in MX_BATCH:
            row["collect_box_detail_id"] = tk_map[row["common_collect_box_detail_id"]]
            print(
                f"  {row['match_key']}: common {row['common_collect_box_detail_id']} "
                f"→ TK {row['collect_box_detail_id']}"
            )

    rate = fetch_cny_mxn()
    rc = 0
    for row in MX_BATCH:
        q = quote_match_key(row["match_key"], cny_mxn=rate)
        publish_sku = row["master_sku"]
        print(
            f"\n>>> {row['match_key']} ({publish_sku}): POP sale {q.sale_price_mxn:.2f} MXN | "
            f"list ceil {q.list_price_ceil_mxn} | weight {q.weight_kg}kg | vol {q.volumetric_kg}kg"
        )
        if q.sfp_adjustment:
            print(f"    {q.sfp_adjustment}")

        if args.dry_run:
            continue

        confirm_token: str | None = None
        if not args.skip_confirm:
            pname = _product_name(publish_sku, row["master_product_id"], row["master_region"])
            img = _main_image(publish_sku, row["master_product_id"], row["master_region"])
            card, _ = prepare_mx_publish_confirm(
                pop_quote=q,
                collect_box_detail_id=row["collect_box_detail_id"],
                seller_sku=publish_sku,
                master_product_id=row["master_product_id"],
                master_region=row["master_region"],
                stock=STOCK,
                product_name=pname,
                main_image_url=img,
            )
            confirm_token = card.token
            if args.interactive:
                from modules.miaoshou.mx_confirm import wait_for_terminal_confirm

                if not wait_for_terminal_confirm(confirm_token):
                    print(f"    ✗ {row['match_key']} 未确认，跳过上架")
                    rc = MX_EXIT_NEEDS_USER_CONFIRM
                    continue
            else:
                print(f"    等待对话框确认 {row['match_key']}（token={confirm_token}）")
                rc = MX_EXIT_NEEDS_USER_CONFIRM
                break
            print(f"    ✓ {row['match_key']} 已确认，开始 save/publish")

        package_cm = _known_package_cm(row["match_key"])
        code = publish_mx_listing(
            collect_box_detail_id=row["collect_box_detail_id"],
            seller_sku=publish_sku,
            ph_product_id=row["master_product_id"],
            master_region=row["master_region"],
            publish=True,
            mxn_sale=q.sale_price_mxn,
            mxn_list=q.list_price_ceil_mxn,
            stock=STOCK,
            weight_kg=q.weight_kg,
            package_cm=package_cm,
            pop_quote=q,
            confirm_token=confirm_token,
            skip_user_confirm=args.skip_confirm,
        )
        if code:
            rc = code

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
