"""MX 批量队列：按对齐码生成确认卡片（对话框）。"""
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
    format_confirm_card_dialog,
    prepare_mx_publish_confirm,
)
from modules.miaoshou.mx_migrate import collect_master_images_and_product
from scripts.mx_pop_pricing import KNOWN_BY_MATCH_KEY, fetch_cny_mxn, load_catalog_row, quote_match_key

STOCK = 200

# tk_detail_id 来自妙手 TikTok 平台采集箱全量扫描（2026-06-25）
# 0010–0013 见 MX_GROUP（同链接多规格）
MX_QUEUE: list[dict] = [
    {"match_key": "0016", "tk_detail_id": 1842250865, "master_sku": "770016", "master_product_id": "1731886576733095867", "master_region": "PH"},
    {"match_key": "0017", "tk_detail_id": 2439741653, "master_sku": "770017", "master_product_id": "1731814164151109563", "master_region": "PH"},
    {"match_key": "0018", "tk_detail_id": 1666910516, "master_sku": "770018", "master_product_id": "1731502168399316923", "master_region": "PH"},
    {"match_key": "0021", "tk_detail_id": 2227606548, "master_sku": "770021", "master_product_id": "1732993365279541179", "master_region": "PH"},
    {"match_key": "0022", "tk_detail_id": 1903332860, "master_sku": "770022", "master_product_id": "1731994556145371067", "master_region": "PH"},
    {"match_key": "0023", "tk_detail_id": 2166177827, "master_sku": "770023", "master_product_id": "1732753170443765691", "master_region": "PH"},
    {"match_key": "0025", "tk_detail_id": 1783989545, "master_sku": "770025", "master_product_id": "1731762445353322427", "master_region": "PH"},
    {"match_key": "0026", "tk_detail_id": 2742689723, "master_sku": "660026", "master_product_id": "1734659190752577467", "master_region": "MY"},
]

# 0010–0013 同 PH 链接多规格，用 migrate_mx_group.py 整组上架
MX_GROUP: list[dict] = [
    {
        "match_keys": ["0010", "0011", "0012", "0013"],
        "tk_detail_id": 1693450013,
        "master_product_id": "1731565249412499387",
        "master_region": "PH",
    },
]

BLOCKED: list[dict] = [
    {"match_key": "0027", "reason": "shop.db 无目录/成本，请先录入"},
]


def _known_package_cm(match_key: str) -> tuple[int, int, int] | None:
    known = KNOWN_BY_MATCH_KEY.get(match_key, {})
    if known.get("l"):
        return int(known["l"]), int(known["w"]), int(known["h"])
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="MX 批量确认卡片")
    ap.add_argument("--keys", nargs="*", help="仅处理指定对齐码")
    ap.add_argument("--summary-only", action="store_true")
    args = ap.parse_args()

    want = set(args.keys) if args.keys else None
    rows = [r for r in MX_QUEUE if not want or r["match_key"] in want]
    keys = [r["match_key"] for r in rows]

    if keys:
        print(">>> 同步物流重量:", ", ".join(keys))
        sync_match_key_weights(keys, on_progress=print, force_refresh=False)

    rate = fetch_cny_mxn()
    tokens_path = ROOT / "data" / "mx_confirm" / "batch_queue_tokens.json"
    tokens: dict[str, str] = {}
    if tokens_path.is_file():
        tokens = json.loads(tokens_path.read_text(encoding="utf-8"))

    print("\n=== 汇总 ===")
    print("| 对齐码 | 上传原价 | POP折后 | 尺寸 | 状态 |")
    print("|--------|----------|---------|------|------|")
    for b in BLOCKED:
        if want and b["match_key"] not in want:
            continue
        print(f"| {b['match_key']} | — | — | — | ⛔ {b['reason'][:30]}… |")

    for row in rows:
        mk = row["match_key"]
        q = quote_match_key(mk, cny_mxn=rate)
        vol = "⚠计泡" if q.volumetric_kg > q.weight_kg + 1e-6 else "OK"
        print(f"| {mk} | {q.list_price_ceil_mxn} | {q.sale_price_mxn:.0f} | {q.package_cm} {vol} | 待确认 |")

    if args.summary_only:
        return 0

    for row in rows:
        mk = row["match_key"]
        q = quote_match_key(mk, cny_mxn=rate)
        try:
            cat = load_catalog_row(row["master_sku"])
            pname = str(cat.get("product_name") or row["master_sku"])
        except RuntimeError:
            pname = row["master_sku"]
        urls, _ = collect_master_images_and_product(
            row["master_product_id"], region=row["master_region"]
        )
        card, text = prepare_mx_publish_confirm(
            pop_quote=q,
            collect_box_detail_id=row["tk_detail_id"],
            seller_sku=row["master_sku"],
            master_product_id=row["master_product_id"],
            master_region=row["master_region"],
            stock=STOCK,
            product_name=pname,
            main_image_url=urls[0] if urls else "",
        )
        tokens[mk] = card.token
        print(f"\n--- {mk} token={card.token} ---")

    tokens_path.parent.mkdir(parents=True, exist_ok=True)
    tokens_path.write_text(json.dumps(tokens, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已保存 tokens → {tokens_path}")
    return MX_EXIT_NEEDS_USER_CONFIRM


if __name__ == "__main__":
    raise SystemExit(main())
