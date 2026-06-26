"""Orbit Hive：发送 MX 富文本审批卡（主图 + 价格表 + 审批按钮）。"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.miaoshou.mx_collect_match import find_common_id as find_collect_common_id
from modules.miaoshou.mx_confirm import create_confirm_card
from modules.miaoshou.mx_feishu_approval import (
    build_batch_mx_approval_card,
    build_single_mx_approval_card,
    default_chat_id,
    send_mx_approval_card,
)
from modules.miaoshou.mx_migrate import collect_master_images_and_product
from scripts.mx_pop_pricing import fetch_cny_mxn, quote_match_key

DEFAULT_TASK = "TASK-20260625-132251-972825"


def catalog_row(mk: str) -> dict | None:
    conn = sqlite3.connect(ROOT / "data" / "shop.db")
    conn.row_factory = sqlite3.Row
    for reg in ("PH", "MY", "TH", "VN"):
        row = conn.execute(
            """
            SELECT p.seller_sku, p.product_id, s.region
            FROM products p JOIN shops s ON p.shop_cipher = s.cipher
            LEFT JOIN sku_costs sc ON sc.sku_id = p.sku_id
            WHERE p.seller_sku LIKE ? AND UPPER(s.region) = ?
              AND sc.cost_cny IS NOT NULL AND sc.cost_cny > 0
            LIMIT 1
            """,
            (f"%{mk}", reg),
        ).fetchone()
        if row:
            return dict(row)
    return None


def find_common_id(product_id: str, *, mk: str = "", seller_sku: str = "") -> int | None:
    return find_collect_common_id(mk=mk, seller_sku=seller_sku, product_id=str(product_id))


def build_card_for_mk(mk: str, *, rate: float) -> tuple[object, int | None]:
    row = catalog_row(mk)
    if not row:
        raise RuntimeError(f"{mk} 无目录/成本")
    q = quote_match_key(mk, cny_mxn=rate)
    urls, product = collect_master_images_and_product(str(row["product_id"]), region=row["region"] or "PH")
    common_id = find_common_id(str(row["product_id"]), mk=mk, seller_sku=str(row["seller_sku"]))
    tk_placeholder = 0
    card = create_confirm_card(
        pop_quote=q,
        collect_box_detail_id=tk_placeholder,
        seller_sku=row["seller_sku"],
        master_product_id=str(row["product_id"]),
        master_region=row["region"] or "PH",
        product_name=str(product.get("title") or row["seller_sku"]),
        main_image_url=urls[0] if urls else "",
    )
    return card, common_id


def main() -> int:
    ap = argparse.ArgumentParser(description="发送 MX 富文本飞书审批卡")
    ap.add_argument("keys", nargs="+", help="对齐码，如 0810 0811")
    ap.add_argument("--task-id", default=DEFAULT_TASK)
    ap.add_argument("--title", default="MX 批量发布审批")
    ap.add_argument(
        "--risk",
        default="真实 publish + 西班牙语上架 + 写价；批准前不会执行。",
    )
    ap.add_argument("--chat-id", default="", help="默认 FEISHU_DEFAULT_CHAT_ID 或 Orbit 战情室")
    ap.add_argument(
        "--batch",
        action="store_true",
        help="多个 SKU 合并为一张审批卡（默认每个 SKU 单独一张）",
    )
    ap.add_argument("--dry-run", action="store_true", help="仅打印卡片 JSON，不发送")
    ap.add_argument(
        "--feishu",
        action="store_true",
        help="发送到飞书群审批卡（默认仅入 Web 收件箱，不在群内审批）",
    )
    args = ap.parse_args()

    rate = fetch_cny_mxn()
    keys = [k.zfill(4)[-4:] for k in args.keys]
    cards = []
    from modules.miaoshou.mx_confirm import _write  # noqa: PLC2701
    from modules.miaoshou.mx_web_approval import mx_approval_url

    for mk in keys:
        card, common_id = build_card_for_mk(mk, rate=rate)
        if common_id:
            card.collect_box_detail_id = common_id  # type: ignore[misc]
            _write(card)
        cards.append(card)
        print(f"  web queued {mk} list={card.list_price_ceil_mxn} → {mx_approval_url(card.token)}")

    if not args.feishu:
        print(f">>> Web 审批: {mx_approval_url()}")
        return 0

    chat = args.chat_id.strip() or default_chat_id()
    if args.batch:
        payloads = [
            build_batch_mx_approval_card(cards, task_id=args.task_id, title=args.title, risk_note=args.risk)
        ]
    else:
        payloads = [
            build_single_mx_approval_card(
                c,
                task_id=args.task_id,
                title=f"MX 上架审批 · {c.match_key}",
                risk_note=args.risk,
            )
            for c in cards
        ]

    out_dir = ROOT / "data" / "mx_confirm"
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, payload in enumerate(payloads):
        mk = cards[i].match_key if not args.batch and i < len(cards) else str(i)
        path = out_dir / f"orbit_approval_{mk}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f">>> card json: {path}")

    if args.dry_run:
        print(">>> dry-run: 未发送飞书")
        return 0

    for payload in payloads:
        result = send_mx_approval_card(payload, chat_id=chat)
        mid = (result.get("data") or {}).get("message_id")
        mk = payload.get("header", {}).get("title", {}).get("content", "")
        print(f">>> sent {mk} message_id={mid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
