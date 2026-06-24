"""从商品目录 SQLite 加载 SKU 成本与商品信息（供结算利润计算）。"""

from __future__ import annotations

import re

from core.db import connect, init_db

AD_RATE = 0.20


def load_sku_cost_maps() -> tuple[dict[str, float], dict[str, float]]:
    """sku_id → cost_cny；前 6 位前缀 → cost_cny（兼容收入表科学计数法）。"""
    init_db()
    conn = connect()
    by_sku: dict[str, float] = {}
    by_prefix: dict[str, float] = {}
    for r in conn.execute("SELECT sku_id, cost_cny FROM sku_costs WHERE cost_cny > 0"):
        sku = str(r["sku_id"] or "").strip()
        if not sku:
            continue
        cost = float(r["cost_cny"])
        by_sku[sku] = cost
        if len(sku) >= 6 and sku[:6] not in by_prefix:
            by_prefix[sku[:6]] = cost
    conn.close()
    return by_sku, by_prefix


def load_product_maps() -> tuple[dict[str, dict], dict[str, dict]]:
    """sku_id → {product_name, sku_name, image_url}。"""
    init_db()
    conn = connect()
    by_sku: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT sku_id, product_name, image_url, sku_name FROM products WHERE sku_id != ''"
    ):
        sku = str(r["sku_id"] or "").strip()
        if not sku or not re.match(r"^\d+$", sku):
            continue
        info = {
            "product_name": (r["product_name"] or "")[:200],
            "sku_name": (r["sku_name"] or "").strip(),
            "image_url": (r["image_url"] or "").strip(),
        }
        if sku not in by_sku:
            by_sku[sku] = info
        else:
            cur = by_sku[sku]
            for k in info:
                if not cur.get(k) and info.get(k):
                    cur[k] = info[k]
    conn.close()
    by_prefix: dict[str, dict] = {}
    for sid, info in by_sku.items():
        if len(sid) >= 6 and sid[:6] not in by_prefix:
            by_prefix[sid[:6]] = info
    return by_sku, by_prefix


def cost_stats() -> dict:
    init_db()
    conn = connect()
    n = conn.execute("SELECT COUNT(*) AS c FROM sku_costs WHERE cost_cny > 0").fetchone()
    conn.close()
    return {"sku_with_cost": int(n["c"] or 0) if n else 0}
