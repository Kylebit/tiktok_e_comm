"""Deep scan common collect box for MX pending SKUs (multiple match strategies)."""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.miaoshou.client import post_open

LIST = "/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_list"
DETAIL = "/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_detail"
CORE = ["0810", "0811", "0814", "0823", "0829", "0809"]


def fetch_all(tab: str = "all") -> tuple[list[dict], int | None]:
    items: list[dict] = []
    total: int | None = None
    for page in range(1, 100):
        r = post_open(
            LIST,
            {
                "pageNo": page,
                "pageSize": 100,
                "filter": {"tabPaneName": tab, "sourceItemIdKeyword": ""},
            },
        )
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


def search_keyword(kw: str, tab: str = "all") -> list[dict]:
    r = post_open(
        LIST,
        {
            "pageNo": 1,
            "pageSize": 20,
            "filter": {"tabPaneName": tab, "sourceItemIdKeyword": kw},
        },
    )
    return (r.get("data") or {}).get("detailList") or []


def all_catalog_pids(mk: str) -> list[dict]:
    conn = sqlite3.connect(ROOT / "data" / "shop.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT p.seller_sku, p.product_id, s.region
        FROM products p JOIN shops s ON p.shop_cipher = s.cipher
        LEFT JOIN sku_costs sc ON sc.sku_id = p.sku_id
        WHERE p.seller_sku LIKE ? AND sc.cost_cny IS NOT NULL AND sc.cost_cny > 0
        ORDER BY CASE UPPER(s.region) WHEN 'PH' THEN 0 WHEN 'MY' THEN 1 WHEN 'TH' THEN 2 ELSE 3 END
        """,
        (f"%{mk.zfill(4)[-4:]}",),
    ).fetchall()
    return [dict(r) for r in rows]


def index_items(items: list[dict]) -> dict[str, dict]:
    by_pid: dict[str, dict] = {}
    for it in items:
        cid = it.get("commonCollectBoxDetailId")
        for s in it.get("sourceList") or []:
            sid = str(s.get("sourceItemId") or "").strip()
            if sid:
                by_pid[sid] = it
            url = str(s.get("sourceItemUrl") or "")
            m = re.search(r"/(\d{15,20})", url)
            if m:
                by_pid.setdefault(m.group(1), it)
        item_num = str(it.get("itemNum") or "")
        if item_num:
            by_pid.setdefault(f"itemNum:{item_num}", it)
        title = str(it.get("title") or "")
        for mk in CORE:
            if mk in item_num or mk in title:
                by_pid.setdefault(f"hint:{mk}:{cid}", it)
    return by_pid


def main() -> int:
    print("=== collect box totals ===")
    for tab in ("all", "noClaimed", "claimed", "collectFail", "collectSucess"):
        _, t = fetch_all(tab)
        print(f"  tabPaneName={tab!r} total={t}")

    items, total = fetch_all("all")
    print(f"\nfull fetch: total={total} len={len(items)}")
    by_pid = index_items(items)

    print("\n=== per-SKU deep check ===")
    for mk in CORE:
        print(f"\n--- {mk} ---")
        catalog = all_catalog_pids(mk)
        found_any = False
        for row in catalog:
            pid = str(row["product_id"])
            sku = row["seller_sku"]
            reg = row["region"]
            # A: full list index
            hit = by_pid.get(pid)
            method = "full_list"
            # B: API keyword exact
            if not hit:
                kw_hits = search_keyword(pid)
                if kw_hits:
                    hit = kw_hits[0]
                    method = "api_keyword_exact"
            # C: API keyword suffix last 10 digits
            if not hit and len(pid) > 10:
                kw_hits = search_keyword(pid[-10:])
                if kw_hits:
                    for h in kw_hits:
                        for s in h.get("sourceList") or []:
                            if pid in str(s.get("sourceItemId") or "") or pid in str(
                                s.get("sourceItemUrl") or ""
                            ):
                                hit = h
                                method = "api_keyword_suffix"
                                break
            # D: search seller_sku suffix
            if not hit:
                kw_hits = search_keyword(mk)
                for h in kw_hits:
                    for s in h.get("sourceList") or []:
                        sid = str(s.get("sourceItemId") or "")
                        if sid == pid:
                            hit = h
                            method = "api_keyword_mk"
                            break
            if hit:
                found_any = True
                cid = hit.get("commonCollectBoxDetailId")
                src = (hit.get("sourceList") or [{}])[0]
                print(
                    f"  FOUND [{method}] {reg} sku={sku} pid={pid} "
                    f"common={cid} source={src.get('source')} srcId={src.get('sourceItemId')}"
                )
                print(f"    title={(hit.get('title') or '')[:60]}")
            else:
                print(f"  MISS {reg} sku={sku} pid={pid}")

        if not found_any:
            # try itemNum patterns like 770810, 0010
            for pat in (f"770{mk}", f"660{mk}", f"880{mk}", f"990{mk}", mk):
                hits = [it for it in items if pat in str(it.get("itemNum") or "")]
                if hits:
                    it = hits[0]
                    print(
                        f"  HINT itemNum~{pat} common={it.get('commonCollectBoxDetailId')} "
                        f"itemNum={it.get('itemNum')} (verify pid manually)"
                    )

    # dump any list item whose sourceItemId matches our core pids set
    print("\n=== reverse: core pids in 847 list? ===")
    all_pids = set()
    for mk in CORE:
        for row in all_catalog_pids(mk):
            all_pids.add(str(row["product_id"]))
    matched = []
    for it in items:
        for s in it.get("sourceList") or []:
            sid = str(s.get("sourceItemId") or "")
            if sid in all_pids:
                matched.append((sid, it.get("commonCollectBoxDetailId"), (it.get("title") or "")[:50]))
    print(f"matched_in_full_list: {len(matched)}")
    for m in matched:
        print(f"  pid={m[0]} common={m[1]} {m[2]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
