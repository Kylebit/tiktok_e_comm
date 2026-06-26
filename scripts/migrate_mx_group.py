"""同链接多规格 → MX 店：POP、对话框确认、整组上架。"""
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
    approve_group_confirm,
    get_confirm,
    get_group_confirm,
    prepare_mx_group_publish_confirm,
    wait_for_terminal_confirm,
)
from modules.miaoshou.mx_migrate import (
    MxSkuVariantWrite,
    collect_master_images_and_product,
    fetch_tiktok_product,
)
from modules.miaoshou.mx_publish import publish_mx_multi_listing
from modules.catalog.sku_key import tk_match_key
from scripts.mx_pop_pricing import KNOWN_BY_MATCH_KEY, fetch_cny_mxn, load_catalog_row, quote_match_key

STOCK = 200
TOKENS_PATH = ROOT / "data" / "mx_confirm" / "batch_queue_tokens.json"


def _extract_confirm_token(argv: list[str]) -> tuple[list[str], str | None]:
    """argparse 无法解析以 '-' 开头的 token，先手动剥离 --confirm-token。"""
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


def _known_package_cm(match_key: str) -> tuple[int, int, int] | None:
    known = KNOWN_BY_MATCH_KEY.get(match_key, {})
    if known.get("l"):
        return int(known["l"]), int(known["w"]), int(known["h"])
    return None


def _token_for_match_key(match_key: str) -> str | None:
    if not TOKENS_PATH.is_file():
        return None
    tokens = json.loads(TOKENS_PATH.read_text(encoding="utf-8"))
    return tokens.get(match_key)


def _publish_from_confirm_token(token: str, *, user_approved: bool) -> int:
    group = get_group_confirm(token)
    if group:
        if user_approved and group.status == "pending":
            approve_group_confirm(token)
            group = get_group_confirm(token)
        if not group or group.status != "approved":
            print(f"多规格确认单未通过（status={group.status if group else '?'}）", file=sys.stderr)
            return MX_EXIT_NEEDS_USER_CONFIRM
        writes = [
            MxSkuVariantWrite(
                match_key=v.match_key,
                seller_sku=v.seller_sku,
                mxn_list_price=v.list_price_ceil_mxn,
                weight_kg=v.weight_kg,
                variant_label=v.variant_label,
            )
            for v in group.variants
        ]
        pkg = _known_package_cm(group.match_keys[0])
        return publish_mx_multi_listing(
            collect_box_detail_id=group.collect_box_detail_id,
            ph_product_id=group.master_product_id,
            variant_writes=writes,
            publish=True,
            stock=group.stock,
            master_region=group.master_region,
            package_cm=pkg,
            confirm_token=group.token,
            skip_user_confirm=False,
        )

    card = get_confirm(token)
    if not card:
        print(f"确认单不存在: {token}", file=sys.stderr)
        return 1
    if user_approved and card.status == "pending":
        approve_confirm(token)
        card = get_confirm(token)
    if not card or card.status != "approved":
        print(f"确认单未通过（status={card.status if card else '?'}）", file=sys.stderr)
        return MX_EXIT_NEEDS_USER_CONFIRM

    # 单规格确认 token：若 match_key 属于多规格组，按整组 POP 上架
    group_keys = ["0010", "0011", "0012", "0013"]
    if card.match_key in group_keys:
        return _publish_group_keys(
            match_keys=group_keys,
            tk_detail_id=card.collect_box_detail_id,
            master_product_id=card.master_product_id,
            master_region=card.master_region,
            confirm_token=token,
            skip_user_confirm=False,
        )

    print("单规格确认单请用 migrate_mx_one.py", file=sys.stderr)
    return 1


def _variant_label(sku: dict) -> str:
    attrs = sku.get("sales_attributes") or []
    if attrs:
        return str(attrs[0].get("value_name") or "").strip()
    return ""


def _load_ph_variants(match_keys: list[str], master_product_id: str, master_region: str) -> dict[str, dict]:
    product = fetch_tiktok_product(master_product_id, region=master_region)
    out: dict[str, dict] = {}
    for sku in product.get("skus") or []:
        mk = tk_match_key(sku.get("seller_sku") or "")
        if mk in match_keys:
            out[mk] = {
                "match_key": mk,
                "seller_sku": (sku.get("seller_sku") or "").strip(),
                "model_name": _variant_label(sku),
            }
    missing = [k for k in match_keys if k not in out]
    if missing:
        raise RuntimeError(f"母版缺少对齐码: {', '.join(missing)}")
    return out


def _publish_group_keys(
    *,
    match_keys: list[str],
    tk_detail_id: int,
    master_product_id: str,
    master_region: str,
    confirm_token: str | None,
    skip_user_confirm: bool,
) -> int:
    rate = fetch_cny_mxn()
    variant_by_mk = _load_ph_variants(match_keys, master_product_id, master_region)
    writes: list[MxSkuVariantWrite] = []
    for mk in match_keys:
        v = variant_by_mk[mk]
        q = quote_match_key(mk, cny_mxn=rate)
        writes.append(
            MxSkuVariantWrite(
                match_key=mk,
                seller_sku=v["seller_sku"],
                mxn_list_price=q.list_price_ceil_mxn,
                weight_kg=q.weight_kg,
                variant_label=v.get("model_name") or "",
            )
        )
    pkg = _known_package_cm(match_keys[0])
    return publish_mx_multi_listing(
        collect_box_detail_id=tk_detail_id,
        ph_product_id=master_product_id,
        variant_writes=writes,
        publish=True,
        stock=STOCK,
        master_region=master_region,
        package_cm=pkg,
        confirm_token=confirm_token,
        skip_user_confirm=skip_user_confirm,
    )


def main() -> int:
    argv, confirm_token = _extract_confirm_token(sys.argv[1:])
    ap = argparse.ArgumentParser(description="MX 同链接多规格搬运")
    ap.add_argument("--match-keys", nargs="+", help="对齐码列表，如 0010 0011 0012 0013")
    ap.add_argument("--tk-detail-id", type=int, help="已有 TikTok 平台采集箱 detailId")
    ap.add_argument("--master-product-id", help="母版 TikTok product_id")
    ap.add_argument("--master-region", default="PH")
    ap.add_argument("--skip-confirm", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--interactive", action="store_true")
    ap.add_argument("--user-approved", action="store_true")
    ap.add_argument("--from-batch-token", metavar="MATCH_KEY", help="从 batch_queue_tokens.json 取 token")
    args = ap.parse_args(argv)

    if confirm_token is None and args.from_batch_token:
        confirm_token = _token_for_match_key(args.from_batch_token.zfill(4)[-4:])

    if confirm_token:
        return _publish_from_confirm_token(confirm_token, user_approved=args.user_approved)

    if not args.match_keys or not args.tk_detail_id or not args.master_product_id:
        ap.error("首次运行需 --match-keys --tk-detail-id --master-product-id（或 --confirm-token / --from-batch-token）")

    keys = [k.zfill(4)[-4:] for k in args.match_keys]
    print(f">>> 同步物流重量: {', '.join(keys)}")
    sync_match_key_weights(keys, on_progress=print, force_refresh=False)

    rate = fetch_cny_mxn()
    variant_by_mk = _load_ph_variants(keys, args.master_product_id, args.master_region)
    variant_quotes: list[tuple[object, str, str]] = []
    print("\n>>> POP 测算")
    for mk in keys:
        v = variant_by_mk[mk]
        q = quote_match_key(mk, cny_mxn=rate)
        label = v.get("model_name") or mk
        print(
            f"  {mk} {v['seller_sku']} [{label}]: list {q.list_price_ceil_mxn} | "
            f"sale {q.sale_price_mxn:.0f} | {q.package_cm}"
        )
        variant_quotes.append((q, v["seller_sku"], label))

    if args.dry_run:
        return 0

    try:
        row = load_catalog_row(variant_by_mk[keys[0]]["seller_sku"])
        pname = str(row.get("product_name") or keys[0])
    except RuntimeError:
        pname = keys[0]
    urls, _ = collect_master_images_and_product(args.master_product_id, region=args.master_region)
    package_cm = str(quote_match_key(keys[0], cny_mxn=rate).package_cm)

    if args.skip_confirm:
        return _publish_group_keys(
            match_keys=keys,
            tk_detail_id=int(args.tk_detail_id),
            master_product_id=args.master_product_id,
            master_region=args.master_region,
            confirm_token=None,
            skip_user_confirm=True,
        )

    card, _ = prepare_mx_group_publish_confirm(
        match_keys=keys,
        collect_box_detail_id=int(args.tk_detail_id),
        master_product_id=args.master_product_id,
        master_region=args.master_region,
        stock=STOCK,
        product_name=pname,
        main_image_url=urls[0] if urls else "",
        package_cm=package_cm,
        variant_quotes=variant_quotes,
    )

    if args.interactive and wait_for_terminal_confirm(card.token):
        approve_group_confirm(card.token)
        return _publish_group_keys(
            match_keys=keys,
            tk_detail_id=int(args.tk_detail_id),
            master_product_id=args.master_product_id,
            master_region=args.master_region,
            confirm_token=card.token,
            skip_user_confirm=False,
        )

    print("\n等待对话框确认后再继续上架。")
    return MX_EXIT_NEEDS_USER_CONFIRM


if __name__ == "__main__":
    raise SystemExit(main())
