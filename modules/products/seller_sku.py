"""为缺失商家 SKU 的商品自动分配编码，并可导出/推送至 TikTok。"""

from __future__ import annotations

import csv
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from core import auth
from core.api_client import get as api_get, request
from core.config import ROOT, get
from core.db import connect, init_db

DETAIL_PATH = "/product/202309/products/{product_id}"
GLOBAL_DETAIL_PATH = "/product/202309/global_products/{global_product_id}"
GLOBAL_EDIT_PATH = "/product/202309/global_products/{global_product_id}"

REGION_PREFIX = {
    "MY": "660",
    "VN": "880",
    "TH": "990",
    "PH": "770",
}

EXPORT_CSV = ROOT / "导出#SKU_2026_05_31_15_57_10.csv"
BATCHEDIT_GLOB = "Tiktoksellercenter_batchedit_*_all_information_template_*.csv"


@dataclass
class FillStats:
    empty: int = 0
    resolved_ref: int = 0
    resolved_global: int = 0
    generated: int = 0
    updated_db: int = 0
    pushed: int = 0
    push_failed: int = 0
    skipped: int = 0
    assignments: list[dict] = field(default_factory=list)


def _digits(raw: str) -> str:
    return re.sub(r"\D", "", str(raw or ""))


def _load_reference_by_sku_id() -> dict[str, str]:
    out: dict[str, str] = {}

    if EXPORT_CSV.is_file():
        with EXPORT_CSV.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                sid = _digits(row.get("SKU ID", ""))
                ssku = (row.get("平台SKU") or "").strip().strip("\t\"")
                if sid and ssku:
                    out[sid] = ssku

    for path in (ROOT / "CURSOR" / "product_cost").glob(BATCHEDIT_GLOB):
        with path.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                sid = _digits(row.get("sku_id", ""))
                ssku = (row.get("seller_sku") or "").strip()
                if sid and ssku:
                    out[sid] = ssku
    return out


def _region_prefix(region: str, conn) -> str:
    configured = (get("products", {}) or {}).get("seller_sku_prefix", {})
    if region in configured:
        return str(configured[region])
    rows = conn.execute(
        """SELECT p.seller_sku FROM products p
           JOIN shops s ON s.cipher = p.shop_cipher
           WHERE s.region = ? AND p.seller_sku GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'""",
        (region,),
    ).fetchall()
    if not rows:
        return REGION_PREFIX.get(region, "660")
    prefixes = Counter(r["seller_sku"][:3] for r in rows)
    return prefixes.most_common(1)[0][0]


def _next_codes(region: str, conn, count: int) -> list[str]:
    prefix = _region_prefix(region, conn)
    rows = conn.execute(
        """SELECT p.seller_sku FROM products p
           JOIN shops s ON s.cipher = p.shop_cipher
           WHERE s.region = ? AND p.seller_sku GLOB ?""",
        (region, f"{prefix}[0-9][0-9][0-9]"),
    ).fetchall()
    used = {int(r["seller_sku"]) for r in rows if r["seller_sku"].isdigit()}
    start = max(used, default=int(prefix + "000")) + 1
    return [str(n) for n in range(start, start + count)]


def _global_maps_from_detail(detail: dict) -> tuple[str, dict[str, str]]:
    gpa = detail.get("global_product_association") or {}
    gpid = str(gpa.get("global_product_id") or "")
    local_to_global = {
        str(m.get("local_sku_id")): str(m.get("global_sku_id"))
        for m in gpa.get("sku_mappings") or []
        if m.get("local_sku_id") and m.get("global_sku_id")
    }
    return gpid, local_to_global


def _fetch_detail(token: str, cipher: str, product_id: str) -> dict:
    path = DETAIL_PATH.format(product_id=product_id)
    result = api_get(path, token, {"shop_cipher": cipher})
    if result.get("code") != 0:
        raise RuntimeError(result.get("message", f"商品详情失败 {product_id}"))
    return result.get("data") or {}


def _save_mapping(conn, sku_id: str, shop_cipher: str, gpid: str, gsid: str) -> None:
    conn.execute(
        """UPDATE products SET global_product_id = ?, global_sku_id = ?
           WHERE sku_id = ? AND shop_cipher = ?""",
        (gpid, gsid, sku_id, shop_cipher),
    )


def _save_seller_sku(conn, sku_id: str, shop_cipher: str, seller_sku: str) -> None:
    conn.execute(
        """UPDATE products SET seller_sku = ?, updated_at = ?
           WHERE sku_id = ? AND shop_cipher = ?""",
        (seller_sku, int(time.time()), sku_id, shop_cipher),
    )


def _build_global_seller_map(conn) -> dict[str, str]:
    rows = conn.execute(
        """SELECT global_sku_id, seller_sku FROM products
           WHERE global_sku_id != '' AND seller_sku != ''
           GROUP BY global_sku_id"""
    ).fetchall()
    return {r["global_sku_id"]: r["seller_sku"] for r in rows if r["global_sku_id"]}


def _assign_for_empty(
    token: str | None = None,
    dry_run: bool = False,
) -> FillStats:
    init_db()
    conn = connect()
    stats = FillStats()
    ref_map = _load_reference_by_sku_id()
    global_seller = _build_global_seller_map(conn)

    rows = conn.execute(
        """SELECT p.sku_id, p.shop_cipher, p.product_id, p.seller_sku,
                  p.global_sku_id, p.global_product_id, p.product_name, p.sku_name,
                  s.region, s.name AS shop_name
           FROM products p
           JOIN shops s ON s.cipher = p.shop_cipher
           WHERE p.seller_sku IS NULL OR p.seller_sku = ''
           ORDER BY s.region, p.product_id, p.sku_id"""
    ).fetchall()
    stats.empty = len(rows)
    if not rows:
        conn.close()
        return stats

    by_region_needed: dict[str, set[str]] = defaultdict(set)
    pending: list[dict] = []

    product_cache: dict[tuple[str, str], dict] = {}

    for row in rows:
        sku_id = row["sku_id"]
        cipher = row["shop_cipher"]
        product_id = row["product_id"]
        region = row["region"]
        gsid = row["global_sku_id"] or ""
        gpid = row["global_product_id"] or ""

        if sku_id in ref_map:
            seller_sku = ref_map[sku_id]
            source = "reference"
            stats.resolved_ref += 1
        else:
            cache_key = (cipher, product_id)
            if cache_key not in product_cache:
                product_cache[cache_key] = _fetch_detail(token or auth.access_token(), cipher, product_id)
                time.sleep(0.1)
            detail = product_cache[cache_key]
            gpid, local_to_global = _global_maps_from_detail(detail)
            gsid = local_to_global.get(sku_id, gsid)
            if not dry_run:
                _save_mapping(conn, sku_id, cipher, gpid, gsid)

            if gsid and gsid in global_seller:
                seller_sku = global_seller[gsid]
                source = "global_sku"
                stats.resolved_global += 1
            else:
                pending.append({
                    "row": row,
                    "global_sku_id": gsid,
                    "global_product_id": gpid,
                    "region": region,
                })
                key = gsid or f"local:{sku_id}"
                by_region_needed[region].add(key)
                continue

        stats.assignments.append({
            "sku_id": sku_id,
            "shop_cipher": cipher,
            "product_id": product_id,
            "region": region,
            "seller_sku": seller_sku,
            "source": source,
            "global_sku_id": gsid,
            "global_product_id": gpid,
        })
        if gsid:
            global_seller[gsid] = seller_sku

    generated_by_region: dict[str, list[str]] = {}
    for region, keys in by_region_needed.items():
        generated_by_region[region] = _next_codes(region, conn, len(keys))

    gen_idx: Counter[str] = Counter()
    assigned_global: dict[str, str] = {}

    for item in pending:
        row = item["row"]
        gsid = item["global_sku_id"]
        region = item["region"]
        if gsid and gsid in assigned_global:
            seller_sku = assigned_global[gsid]
            source = "global_batch"
        elif gsid and gsid in global_seller:
            seller_sku = global_seller[gsid]
            source = "global_sku"
            stats.resolved_global += 1
        else:
            i = gen_idx[region]
            seller_sku = generated_by_region[region][i]
            gen_idx[region] += 1
            source = "generated"
            stats.generated += 1
            if gsid:
                assigned_global[gsid] = seller_sku
                global_seller[gsid] = seller_sku

        stats.assignments.append({
            "sku_id": row["sku_id"],
            "shop_cipher": row["shop_cipher"],
            "product_id": row["product_id"],
            "region": region,
            "seller_sku": seller_sku,
            "source": source,
            "global_sku_id": gsid,
            "global_product_id": item["global_product_id"],
            "product_name": row["product_name"],
            "sku_name": row["sku_name"],
            "shop_name": row["shop_name"],
        })

    if not dry_run:
        for a in stats.assignments:
            _save_seller_sku(conn, a["sku_id"], a["shop_cipher"], a["seller_sku"])
            if a.get("global_sku_id"):
                conn.execute(
                    """UPDATE products SET global_sku_id = ?, global_product_id = ?
                       WHERE sku_id = ? AND shop_cipher = ?""",
                    (
                        a.get("global_sku_id") or "",
                        a.get("global_product_id") or "",
                        a["sku_id"],
                        a["shop_cipher"],
                    ),
                )
        conn.commit()
        stats.updated_db = len(stats.assignments)

    conn.close()
    return stats


def _global_edit_body(global_detail: dict, updates: dict[str, str]) -> dict:
    cat = global_detail.get("category") or {}
    skus = []
    for s in global_detail.get("skus") or []:
        sid = str(s["id"])
        item = {
            "id": sid,
            "seller_sku": updates.get(sid, s.get("seller_sku") or ""),
        }
        attrs = []
        for a in s.get("sales_attributes") or []:
            attrs.append({
                "id": a["id"],
                "value_id": a.get("value_id"),
                "value_name": a.get("value_name"),
                "name": a.get("name"),
            })
        if attrs:
            item["sales_attributes"] = attrs
        price = s.get("price") or {}
        if price.get("amount") and price.get("currency"):
            item["price"] = {"amount": price["amount"], "currency": price["currency"]}
        inv = s.get("inventory") or []
        if inv:
            item["inventory"] = [
                {
                    "global_warehouse_id": i.get("global_warehouse_id"),
                    "quantity": i.get("quantity"),
                }
                for i in inv
            ]
        skus.append(item)

    body = {
        "title": global_detail["title"],
        "description": global_detail.get("description") or "<p></p>",
        "category_id": str(cat.get("id") or ""),
        "category_version": "v1",
        "main_images": [{"uri": img["uri"]} for img in global_detail.get("main_images") or [] if img.get("uri")],
        "skus": skus,
    }
    if global_detail.get("package_weight"):
        body["package_weight"] = global_detail["package_weight"]
    if global_detail.get("package_dimensions"):
        body["package_dimensions"] = global_detail["package_dimensions"]
    if global_detail.get("product_attributes"):
        body["product_attributes"] = [
            {"id": a["id"], "values": a.get("values") or []}
            for a in global_detail["product_attributes"]
        ]
    if global_detail.get("manufacturer_ids"):
        body["manufacturer_ids"] = global_detail["manufacturer_ids"]
    if global_detail.get("responsible_person_ids"):
        body["responsible_person_ids"] = global_detail["responsible_person_ids"]
    return body


def _push_global_product(token: str, gpid: str, updates: dict[str, str]) -> tuple[bool, str]:
    if not gpid or not updates:
        return False, "missing global product"
    result = api_get(GLOBAL_DETAIL_PATH.format(global_product_id=gpid), token, {})
    if result.get("code") != 0:
        return False, result.get("message", "global detail failed")
    body = _global_edit_body(result.get("data") or {}, updates)
    resp = request("PUT", GLOBAL_EDIT_PATH.format(global_product_id=gpid), token, {}, body)
    if resp.get("code") == 0:
        return True, ""
    return False, resp.get("message", str(resp))


def push_assignments(assignments: list[dict], token: str | None = None) -> tuple[int, int, list[str]]:
    token = token or auth.access_token()
    by_global: dict[str, dict[str, str]] = defaultdict(dict)
    for a in assignments:
        gpid = a.get("global_product_id") or ""
        gsid = a.get("global_sku_id") or ""
        if gpid and gsid:
            by_global[gpid][gsid] = a["seller_sku"]

    ok = fail = 0
    errors: list[str] = []
    for gpid, updates in by_global.items():
        success, msg = _push_global_product(token, gpid, updates)
        if success:
            ok += 1
            time.sleep(0.2)
        else:
            fail += 1
            errors.append(f"{gpid}: {msg[:120]}")
    return ok, fail, errors


def export_csv(assignments: list[dict], path: Path | None = None) -> Path:
    exports = ROOT / get("exports_dir", "exports")
    exports.mkdir(parents=True, exist_ok=True)
    out = path or exports / f"seller_sku_fill_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["站点", "店铺", "商品 ID", "SKU ID", "规格", "商家 SKU", "来源", "全球 SKU ID"])
        for a in assignments:
            w.writerow([
                a.get("region", ""),
                a.get("shop_name", ""),
                f'="{a.get("product_id", "")}"',
                f'="{a.get("sku_id", "")}"',
                a.get("sku_name", ""),
                a.get("seller_sku", ""),
                a.get("source", ""),
                f'="{a.get("global_sku_id", "")}"',
            ])
    return out


def fill_missing(
    dry_run: bool = False,
    push: bool = False,
    export: bool = True,
) -> FillStats:
    stats = _assign_for_empty(dry_run=dry_run)
    if stats.empty == 0:
        print("  ✅ 所有 SKU 已有商家 SKU")
        return stats

    print(f"  待补全 {stats.empty} 个 SKU")
    if dry_run:
        print(f"  [预览] 引用匹配 {stats.resolved_ref}，全球 SKU 复用 {stats.resolved_global}，新生成 {stats.generated}")
        for a in stats.assignments[:10]:
            print(f"    {a['region']} {a['sku_id'][:8]}… → {a['seller_sku']} ({a['source']})")
        if len(stats.assignments) > 10:
            print(f"    … 共 {len(stats.assignments)} 条")
        return stats

    print(f"  已写入本地库 {stats.updated_db} 条（引用 {stats.resolved_ref}，全球复用 {stats.resolved_global}，新生成 {stats.generated}）")

    if export and stats.assignments:
        path = export_csv(stats.assignments)
        print(f"  📄 导出: {path}")
        print("     可在 Seller Center → 商品 → 批量编辑 中导入「商家 SKU」列")

    if push and stats.assignments:
        ok, fail, errors = push_assignments(stats.assignments)
        stats.pushed = ok
        stats.push_failed = fail
        if ok:
            print(f"  ✅ TikTok 全球商品已更新 {ok} 个")
        if fail:
            print(f"  ⚠️  API 推送失败 {fail} 个（可改用导出 CSV 手动上传）")
            for e in errors[:5]:
                print(f"     {e}")
    elif push:
        print("  ⚠️  无待推送项")

    return stats
