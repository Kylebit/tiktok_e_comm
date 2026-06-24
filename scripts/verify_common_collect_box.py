"""Verify common collect box: total count, group 贴纸1, SKU ID fields."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.db import connect, init_db
from modules.miaoshou.client import post_open

LIST_PATH = "/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_list"
DETAIL_PATH = "/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_detail"
GROUP_NAME = "贴纸1"


def fetch_all_list(**filter_extra: object) -> tuple[list[dict], int | None]:
    items: list[dict] = []
    total: int | None = None
    base_filter = {"tabPaneName": "all", "sourceItemIdKeyword": ""}
    base_filter.update(filter_extra)
    for page in range(1, 50):
        r = post_open(
            LIST_PATH,
            {"pageNo": page, "pageSize": 100, "filter": base_filter},
        )
        if r.get("result") != "success" and r.get("code") not in ("200", "success"):
            print("LIST ERROR:", json.dumps(r, ensure_ascii=False)[:800])
            break
        data = r.get("data") or {}
        if total is None:
            total = data.get("total")
        batch = data.get("detailList") or []
        if not batch:
            break
        items.extend(batch)
        if len(batch) < 100:
            break
    return items, total


def group_names(items: list[dict]) -> Counter:
    c: Counter = Counter()
    for it in items:
        g = it.get("commonCollectBoxGroupName") or it.get("collectBoxGroupName") or "(无分组)"
        c[g] += 1
    return c


def detail_sku_fields(detail_id: int) -> dict:
    r = post_open(DETAIL_PATH, {"commonCollectBoxDetailId": detail_id})
    data = r.get("data") or {}
    detail = data.get("editCommonCollectBoxDetail") or data
    # dump top-level keys for discovery
    keys = sorted(detail.keys()) if isinstance(detail, dict) else []
    sku_map = detail.get("skuMap") or {}
    sku_samples = []
    for k, v in list(sku_map.items())[:3]:
        sku_samples.append({"key": k, "fields": v})
    source_list = detail.get("sourceList") or []
    source_attrs = detail.get("sourceAttrs") or []
    # look for sku id like fields anywhere shallow
    id_like = {}
    for name in (
        "sourceItemId",
        "itemNum",
        "platformItemId",
        "platformSkuId",
        "tiktokProductId",
        "tiktokSkuId",
        "productId",
        "skuId",
    ):
        if detail.get(name):
            id_like[f"detail.{name}"] = detail.get(name)
    for s in source_list[:3]:
        for k, v in s.items():
            if "id" in k.lower() or "sku" in k.lower():
                id_like[f"sourceList.{k}"] = v
    for a in source_attrs[:20]:
        n = (a.get("name") or a.get("attributeName") or "").strip()
        v = a.get("value") or a.get("attributeValue") or ""
        if any(x in n.upper() for x in ("SKU", "ID", "商品", "产品")):
            id_like[f"sourceAttr[{n}]"] = v
    return {
        "detail_id": detail_id,
        "title": (detail.get("title") or "")[:80],
        "itemNum": detail.get("itemNum"),
        "top_keys_sample": keys[:40],
        "sku_count": len(sku_map),
        "sku_samples": sku_samples,
        "source_list_sample": source_list[:2],
        "id_like_fields": id_like,
        "raw_detail_excerpt": json.dumps(detail, ensure_ascii=False)[:3500],
    }


def ph_catalog_stats() -> dict:
    init_db()
    conn = connect()
    rows = conn.execute(
        """
        SELECT p.sku_id, p.product_id, p.seller_sku, p.product_name
        FROM products p
        JOIN shops s ON p.shop_cipher = s.cipher
        WHERE UPPER(s.region) = 'PH'
        """
    ).fetchall()
    products = {r["product_id"] for r in rows if r["product_id"]}
    return {
        "ph_sku_rows": len(rows),
        "ph_product_ids": len(products),
        "sample": [
            {
                "sku_id": r["sku_id"],
                "product_id": r["product_id"],
                "seller_sku": r["seller_sku"],
                "title": (r["product_name"] or "")[:50],
            }
            for r in rows[:3]
        ],
    }


def main() -> int:
    print("=== PH catalog (shop.db) ===")
    ph = ph_catalog_stats()
    print(json.dumps(ph, ensure_ascii=False, indent=2))

    print("\n=== Common collect box total ===")
    all_items, total = fetch_all_list()
    print(f"api total={total} fetched={len(all_items)}")
    groups = group_names(all_items)
    print("groups:", dict(groups.most_common(20)))

    sticker_items = [
        it
        for it in all_items
        if (it.get("commonCollectBoxGroupName") or it.get("collectBoxGroupName") or "")
        == GROUP_NAME
    ]
    print(f"\n=== group '{GROUP_NAME}' from full scan: {len(sticker_items)} ===")
    if sticker_items:
        for it in sticker_items[:5]:
            print(
                f"  id={it.get('commonCollectBoxDetailId')} "
                f"itemNum={it.get('itemNum')} title={(it.get('title') or '')[:50]}"
            )
        did = int(sticker_items[0]["commonCollectBoxDetailId"])
        print(f"\n=== detail sample id={did} ===")
        print(json.dumps(detail_sku_fields(did), ensure_ascii=False, indent=2))

    # try filter variants for group
    for key in (
        "commonCollectBoxGroupName",
        "collectBoxGroupName",
        "groupName",
        "commonCollectBoxGroupId",
    ):
        filtered, ft = fetch_all_list(**{key: GROUP_NAME})
        if filtered:
            print(f"\nfilter {key}={GROUP_NAME!r} -> {len(filtered)} (total={ft})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
