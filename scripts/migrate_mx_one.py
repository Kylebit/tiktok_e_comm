"""单个对齐码 → MX 店：认领、POP、对话框确认、首次上架。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.catalog.logistics_weights import sync_match_key_weights
from modules.miaoshou.mx_confirm import (
    MX_EXIT_NEEDS_USER_CONFIRM,
    approve_confirm,
    get_confirm,
    prepare_mx_publish_confirm,
    wait_for_terminal_confirm,
)
from modules.miaoshou.mx_migrate import claim_common_to_tiktok, collect_master_images_and_product
from modules.miaoshou.mx_publish import publish_mx_listing
from scripts.mx_pop_pricing import KNOWN_BY_MATCH_KEY, fetch_cny_mxn, load_catalog_row, quote_match_key

STOCK = 200
TOKENS_PATH = ROOT / "data" / "mx_confirm" / "batch_queue_tokens.json"


def _extract_confirm_token(argv: list[str]) -> tuple[list[str], str | None]:
    out: list[str] = []
    token: str | None = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--confirm-token":
            if i + 1 < len(argv):
                token = argv[i + 1]
                i += 2
                continue
        elif arg.startswith("--confirm-token="):
            token = arg.split("=", 1)[1]
            i += 1
            continue
        out.append(arg)
        i += 1
    return out, token


def _token_for_match_key(match_key: str) -> str | None:
    if not TOKENS_PATH.is_file():
        return None
    tokens = json.loads(TOKENS_PATH.read_text(encoding="utf-8"))
    return tokens.get(match_key)


def _known_package_cm(match_key: str) -> tuple[int, int, int] | None:
    known = KNOWN_BY_MATCH_KEY.get(match_key, {})
    if known.get("l"):
        return int(known["l"]), int(known["w"]), int(known["h"])
    return None


def main() -> int:
    argv, confirm_token = _extract_confirm_token(sys.argv[1:])
    ap = argparse.ArgumentParser(description="MX 单 SKU 搬运")
    ap.add_argument("--match-key", help="对齐码，如 0014")
    ap.add_argument("--common-id", type=int, help="公共采集箱 detailId（与 --tk-detail-id 二选一）")
    ap.add_argument("--tk-detail-id", type=int, help="已有 TikTok 平台采集箱 detailId，跳过公共箱认领")
    ap.add_argument("--master-sku", help="母版 seller_sku，如 770014")
    ap.add_argument("--master-product-id", help="母版 TikTok product_id")
    ap.add_argument("--master-region", default="PH", help="母版站点 PH/MY/TH/VN")
    ap.add_argument("--skip-confirm", action="store_true", help="跳过确认（仅调试）")
    ap.add_argument("--dry-run", action="store_true", help="仅 POP 测算")
    ap.add_argument("--interactive", action="store_true", help="终端内输入 确认/取消")
    ap.add_argument("--user-approved", action="store_true", help="标记用户已在对话框确认（配合 --confirm-token）")
    ap.add_argument("--from-batch-token", metavar="MATCH_KEY", help="从 batch_queue_tokens.json 取 token")
    args = ap.parse_args(argv)

    if confirm_token is None and args.from_batch_token:
        confirm_token = _token_for_match_key(args.from_batch_token.zfill(4)[-4:])

    if confirm_token:
        card = get_confirm(confirm_token)
        if not card:
            print(f"确认单不存在: {confirm_token}", file=sys.stderr)
            return 1
        if args.user_approved and card.status == "pending":
            approve_confirm(confirm_token)
            card = get_confirm(confirm_token)
        if not card or card.status != "approved":
            print(f"确认单未通过（status={card.status if card else '?'}）", file=sys.stderr)
            return MX_EXIT_NEEDS_USER_CONFIRM
        return publish_mx_listing(
            collect_box_detail_id=card.collect_box_detail_id,
            seller_sku=card.seller_sku,
            ph_product_id=card.master_product_id,
            master_region=card.master_region,
            publish=True,
            mxn_sale=card.sale_price_mxn,
            mxn_list=card.list_price_ceil_mxn,
            stock=card.stock,
            weight_kg=card.weight_kg,
            package_cm=_known_package_cm(card.match_key),
            confirm_token=card.token,
            skip_user_confirm=False,
        )

    if not args.match_key or not args.master_sku or not args.master_product_id:
        ap.error("首次运行需 --match-key --master-sku --master-product-id（或 --confirm-token 继续上架）")
    if not args.common_id and not args.tk_detail_id:
        ap.error("请提供 --common-id 或 --tk-detail-id")

    mk = args.match_key.zfill(4)[-4:]
    print(f">>> 同步物流重量: {mk}")
    sync_match_key_weights([mk], on_progress=print, force_refresh=False)

    rate = fetch_cny_mxn()
    q = quote_match_key(mk, cny_mxn=rate)
    print(
        f"\n>>> {mk} ({args.master_sku}): POP sale {q.sale_price_mxn:.2f} MXN | "
        f"list ceil {q.list_price_ceil_mxn} | weight {q.weight_kg}kg | vol {q.volumetric_kg}kg | {q.package_cm}"
    )
    if q.sfp_adjustment:
        print(f"    {q.sfp_adjustment}")

    if args.dry_run:
        return 0

    if args.tk_detail_id:
        tk_detail_id = int(args.tk_detail_id)
        print(f"\n>>> 使用已有 TikTok 采集箱 detailId={tk_detail_id}")
    else:
        print("\n>>> 公共采集箱 → TikTok 平台采集箱")
        tk_map = claim_common_to_tiktok([int(args.common_id)])
        tk_detail_id = tk_map[int(args.common_id)]
        print(f"  common {args.common_id} → TK {tk_detail_id}")

    if args.skip_confirm:
        return publish_mx_listing(
            collect_box_detail_id=tk_detail_id,
            seller_sku=args.master_sku,
            ph_product_id=args.master_product_id,
            master_region=args.master_region,
            publish=True,
            mxn_sale=q.sale_price_mxn,
            mxn_list=q.list_price_ceil_mxn,
            stock=STOCK,
            weight_kg=q.weight_kg,
            package_cm=_known_package_cm(mk),
            pop_quote=q,
            skip_user_confirm=True,
        )

    try:
        row = load_catalog_row(args.master_sku)
        pname = str(row.get("product_name") or args.master_sku)
    except RuntimeError:
        pname = args.master_sku
    urls, _ = collect_master_images_and_product(args.master_product_id, region=args.master_region)

    card, _ = prepare_mx_publish_confirm(
        pop_quote=q,
        collect_box_detail_id=tk_detail_id,
        seller_sku=args.master_sku,
        master_product_id=args.master_product_id,
        master_region=args.master_region,
        stock=STOCK,
        product_name=pname,
        main_image_url=urls[0] if urls else "",
    )

    if args.interactive and wait_for_terminal_confirm(card.token):
        return publish_mx_listing(
            collect_box_detail_id=tk_detail_id,
            seller_sku=args.master_sku,
            ph_product_id=args.master_product_id,
            master_region=args.master_region,
            publish=True,
            mxn_sale=q.sale_price_mxn,
            mxn_list=q.list_price_ceil_mxn,
            stock=STOCK,
            weight_kg=q.weight_kg,
            package_cm=_known_package_cm(mk),
            confirm_token=card.token,
            skip_user_confirm=False,
        )

    print("\n等待对话框确认后再继续上架。")
    return MX_EXIT_NEEDS_USER_CONFIRM


if __name__ == "__main__":
    raise SystemExit(main())
