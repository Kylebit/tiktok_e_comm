"""批量上架 0800–0830：公共采集箱 → 认领 → MX 西班牙语上架。"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.catalog.sku_key import tk_match_key
from modules.miaoshou.mx_migrate import MxSkuVariantWrite, claim_common_to_tiktok, fetch_tiktok_product
from modules.miaoshou.mx_publish import publish_mx_listing, publish_mx_multi_listing
from scripts.mx_pop_pricing import KNOWN_BY_MATCH_KEY, fetch_cny_mxn, quote_match_key

STOCK = 200
SKIP_MK = {"0808"}  # 已上架
SKIP_GROUP_DE = {"0818", "0819", "0820", "0821", "0822"}

# 公共采集箱（贴纸1 组，sourceItemId 已核对）
COMMON_JOBS: list[dict] = [
    {
        "keys": ["0800", "0801"],
        "common_id": 3729935844,
        "pid": "1733510891803150267",
        "region": "PH",
    },
    {
        "keys": ["0802"],
        "common_id": 3729935846,
        "pid": "1733510761614903227",
        "region": "PH",
    },
    {
        "keys": ["0803", "0804"],
        "common_id": 3729935852,
        "pid": "1734083720865220539",
        "region": "PH",
    },
    {
        "keys": ["0805", "0806", "0807"],
        "common_id": 3729935797,
        "pid": "1733291356586084283",
        "region": "PH",
    },
    {
        "keys": ["0827"],
        "common_id": 3729935853,
        "pid": "1733828087686137787",
        "region": "PH",
    },
]


def _package_cm(mk: str) -> tuple[int, int, int] | None:
    known = KNOWN_BY_MATCH_KEY.get(mk, {})
    if known.get("l"):
        return int(known["l"]), int(known["w"]), int(known["h"])
    return None


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


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="批量上架 0800–0830（公共采集箱）")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="仅认领+POP 预览，不 save/publish（Orbit Hive 默认）",
    )
    args = ap.parse_args()

    rate = fetch_cny_mxn()
    results: list[dict] = []

    common_ids = [j["common_id"] for j in COMMON_JOBS]
    print(">>> 公共采集箱 → TikTok 平台采集箱")
    tk_map = claim_common_to_tiktok(common_ids)
    for job in COMMON_JOBS:
        job["tk"] = tk_map[job["common_id"]]
        print(f"  {job['keys']}: common {job['common_id']} → TK {job['tk']}")

    for job in COMMON_JOBS:
        keys = [k for k in job["keys"] if k not in SKIP_MK and k not in SKIP_GROUP_DE]
        if not keys:
            continue
        tk = int(job["tk"])
        pid = job["pid"]
        region = job["region"]

        try:
            product = fetch_tiktok_product(pid, region=region)
        except Exception as exc:
            results.append({"keys": keys, "status": "fail", "reason": f"母版 API: {exc}"})
            continue

        if len(keys) == 1:
            mk = keys[0]
            try:
                q = quote_match_key(mk, cny_mxn=rate)
            except Exception as exc:
                results.append({"mk": mk, "status": "fail", "reason": str(exc)})
                continue
            row = catalog_row(mk)
            if not row:
                results.append({"mk": mk, "status": "fail", "reason": "无目录"})
                continue
            print(f"\n>>> SINGLE {mk} tk={tk} list={q.list_price_ceil_mxn}")
            if args.dry_run:
                results.append(
                    {
                        "mk": mk,
                        "tk": tk,
                        "list": q.list_price_ceil_mxn,
                        "status": "dry_run",
                    }
                )
                continue
            rc = publish_mx_listing(
                collect_box_detail_id=tk,
                seller_sku=row["seller_sku"],
                ph_product_id=pid,
                master_region=region,
                publish=True,
                mxn_sale=q.sale_price_mxn,
                mxn_list=q.list_price_ceil_mxn,
                stock=STOCK,
                weight_kg=q.weight_kg,
                package_cm=_package_cm(mk),
                pop_quote=q,
                volumetric_confirmed=True,
                skip_user_confirm=True,
                spanish_copy=True,
            )
            results.append(
                {
                    "mk": mk,
                    "tk": tk,
                    "list": q.list_price_ceil_mxn,
                    "status": "ok" if rc == 0 else f"exit {rc}",
                }
            )
            continue

        sku_by_mk: dict[str, dict] = {}
        for sk in product.get("skus") or []:
            mk = tk_match_key(sk.get("seller_sku") or "")
            if mk in keys:
                sku_by_mk[mk] = sk
        writes: list[MxSkuVariantWrite] = []
        for mk in keys:
            if mk not in sku_by_mk:
                results.append({"mk": mk, "status": "fail", "reason": "PH 母版无此规格"})
                continue
            sk = sku_by_mk[mk]
            q = quote_match_key(mk, cny_mxn=rate)
            label = ""
            attrs = sk.get("sales_attributes") or []
            if attrs:
                label = str(attrs[0].get("value_name") or "")
            writes.append(
                MxSkuVariantWrite(
                    match_key=mk,
                    seller_sku=(sk.get("seller_sku") or "").strip(),
                    mxn_list_price=q.list_price_ceil_mxn,
                    weight_kg=q.weight_kg,
                    variant_label=label,
                )
            )
        if len(writes) != len(keys):
            results.append({"keys": keys, "status": "fail", "reason": "规格未齐"})
            continue
        pkg = _package_cm(keys[0])
        print(f"\n>>> GROUP {keys} tk={tk}")
        if args.dry_run:
            preview = {w.match_key: w.mxn_list_price for w in writes}
            results.append({"keys": keys, "tk": tk, "list": preview, "status": "dry_run"})
            continue
        rc = publish_mx_multi_listing(
            collect_box_detail_id=tk,
            ph_product_id=pid,
            variant_writes=writes,
            publish=True,
            stock=STOCK,
            master_region=region,
            package_cm=pkg,
            skip_user_confirm=True,
            spanish_copy=True,
        )
        results.append(
            {
                "keys": keys,
                "tk": tk,
                "status": "ok" if rc == 0 else f"exit {rc}",
            }
        )

    out = ROOT / "data" / "mx_confirm" / "batch_08xx_publish.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for r in results if r.get("status") == "ok")
    print(f"\n=== DONE ok={ok}/{len(results)} → {out} ===")
    for r in results:
        print(json.dumps(r, ensure_ascii=False))
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
