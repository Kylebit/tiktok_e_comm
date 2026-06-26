"""上架 0809–0829（Temu/TikTok 公共采集箱 → MX 西班牙语）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.catalog.sku_key import tk_match_key
from modules.miaoshou.feishu_manual_overrides import load_overrides
from modules.miaoshou.mx_migrate import MxSkuVariantWrite, claim_common_to_tiktok, fetch_tiktok_product
from modules.miaoshou.mx_publish import publish_mx_listing, publish_mx_multi_listing
from scripts.migrate_mx_batch_08xx import catalog_row
from scripts.mx_pop_pricing import KNOWN_BY_MATCH_KEY, fetch_cny_mxn, quote_match_key

STOCK = 200

# Temu/TikTok 公共采集箱（847 noClaimed，标题已核对）
JOBS: list[dict] = [
    {"keys": ["0809"], "common_id": 3579516303, "pid": "1733828085385693115", "region": "MY"},
    {"keys": ["0810"], "common_id": 3735437658, "pid": "1731475798019049403", "region": "PH"},
    {"keys": ["0811", "0812", "0813"], "common_id": 3735437690, "pid": "1732799684701226939", "region": "PH"},
    {"keys": ["0814", "0815"], "common_id": 3735437729, "pid": "1732510670006749115", "region": "PH"},
    {"keys": ["0823", "0824"], "common_id": 3735437644, "pid": "1731571872335628219", "region": "PH"},
    {"keys": ["0829"], "common_id": 3735437656, "pid": "1731484883560073147", "region": "PH"},
]


def _package_cm(mk: str) -> tuple[int, int, int] | None:
    known = {**KNOWN_BY_MATCH_KEY.get(mk, {}), **load_overrides().get(mk, {})}
    if known.get("l"):
        return int(known["l"]), int(known["w"]), int(known["h"])
    return None


def main() -> int:
    rate = fetch_cny_mxn()
    results: list[dict] = []

    print(">>> claim common → TikTok")
    tk_map = claim_common_to_tiktok([j["common_id"] for j in JOBS])
    for job in JOBS:
        job["tk"] = tk_map[job["common_id"]]
        print(f"  {job['keys']}: common {job['common_id']} → TK {job['tk']}")

    for job in JOBS:
        keys = job["keys"]
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
                results.append({"mk": mk, "status": "fail", "reason": "母版无此规格"})
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
        print(f"\n>>> GROUP {keys} tk={tk} lists={[w.mxn_list_price for w in writes]}")
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
            {"keys": keys, "tk": tk, "status": "ok" if rc == 0 else f"exit {rc}"}
        )

    out = ROOT / "data" / "mx_confirm" / "batch_0809_0829_publish.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for r in results if r.get("status") == "ok")
    print(f"\n=== DONE ok={ok}/{len(results)} → {out} ===")
    for r in results:
        print(json.dumps(r, ensure_ascii=False))
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
