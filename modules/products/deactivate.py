"""零销商品批量下架：扫描 → 确认 → deactivate API。"""

from __future__ import annotations

import time

from core import auth
from core.api_client import post as api_post
from core.config import get
from core.db import connect, init_db
from modules.products import analytics as analytics_mod
from modules.products import sales
from modules.products.promotions import get_activity, search_activities

DEACTIVATE_PATH = "/product/202309/products/deactivate"


def _deactivate_cfg() -> dict:
    cfg = get("deactivate") or {}
    return {
        "sales_days": int(cfg.get("sales_days", 90)),
        "min_stock": int(cfg.get("min_stock", 5)),
        "require_low_ctr": bool(cfg.get("require_low_ctr", True)),
        "push_delay_sec": float(cfg.get("push_delay_sec", get("promotion.push_delay_sec", 1.2))),
        "batch_size": int(cfg.get("batch_size", 20)),
    }


def _migrate(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS deactivate_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT NOT NULL,
            shop_cipher TEXT NOT NULL,
            region TEXT,
            product_name TEXT,
            image_url TEXT,
            seller_sku TEXT,
            stock INTEGER DEFAULT 0,
            orders_90d INTEGER DEFAULT 0,
            orders_28d INTEGER DEFAULT 0,
            click_through_rate REAL DEFAULT 0,
            ctr_median REAL DEFAULT 0,
            status TEXT DEFAULT 'pending',
            error TEXT,
            created_at INTEGER,
            pushed_at INTEGER,
            UNIQUE(product_id, shop_cipher)
        )"""
    )


def _promo_product_ids(token: str, cipher: str) -> set[str]:
    pids: set[str] = set()
    try:
        acts = search_activities(token, cipher, "ONGOING")
    except RuntimeError:
        return pids
    for act in acts:
        act_id = str(act.get("id") or act.get("activity_id") or "")
        if not act_id:
            continue
        try:
            detail = get_activity(token, cipher, act_id)
        except RuntimeError:
            continue
        for p in detail.get("products") or []:
            if p.get("id"):
                pids.add(str(p["id"]))
    return pids


def scan_candidates(
    region: str | None = None,
    limit: int = 50,
    quiet: bool = False,
) -> int:
    """90 天 0 单 + CTR 低于店铺中位 + 有库存 + 不在促销中。"""
    init_db()
    cfg = _deactivate_cfg()

    def _log(msg: str) -> None:
        if not quiet:
            print(msg)

    _log("\n[1/4] 同步 28 天 Analytics...")
    analytics_mod.sync_all(region=region, quiet=quiet)

    _log(f"\n[2/4] 统计近 {cfg['sales_days']} 天动销...")
    sold = sales.aggregate_product_sales(days=cfg["sales_days"], region=region)

    conn = connect()
    _migrate(conn)
    token = auth.access_token()
    now = int(time.time())
    candidates: list[dict] = []

    _log("\n[3/4] 筛选下架候选...")
    sql = """
        SELECT a.product_id, a.shop_cipher, a.region, a.orders AS orders_28d,
               a.click_through_rate, a.ctr_median,
               MAX(p.product_name) AS product_name,
               MAX(p.image_url) AS image_url,
               MAX(p.seller_sku) AS seller_sku,
               SUM(p.stock) AS stock_total
        FROM product_analytics a
        JOIN products p ON p.product_id = a.product_id AND p.shop_cipher = a.shop_cipher
        WHERE p.status = 'ACTIVATE' AND p.product_id != ''
    """
    params: list = []
    if region:
        sql += " AND a.region = ?"
        params.append(region.upper())
    sql += " GROUP BY a.product_id, a.shop_cipher"

    promo_cache: dict[str, set[str]] = {}
    for row in conn.execute(sql, params).fetchall():
        pid = row["product_id"]
        cipher = row["shop_cipher"]
        key = (pid, cipher)
        orders_90d = sold.get(key, {}).get("orders", 0)
        if orders_90d > 0:
            continue
        orders_28d = int(row["orders_28d"] or 0)
        if orders_28d > 0:
            continue
        stock = int(row["stock_total"] or 0)
        if stock < cfg["min_stock"]:
            continue
        ctr = float(row["click_through_rate"] or 0)
        median = float(row["ctr_median"] or 0)
        if cfg["require_low_ctr"] and median > 0 and ctr >= median:
            continue

        if cipher not in promo_cache:
            promo_cache[cipher] = _promo_product_ids(token, cipher)
        if pid in promo_cache[cipher]:
            continue

        candidates.append({
            "product_id": pid,
            "shop_cipher": cipher,
            "region": row["region"],
            "product_name": row["product_name"] or "",
            "image_url": row["image_url"] or "",
            "seller_sku": row["seller_sku"] or "",
            "stock": stock,
            "orders_90d": 0,
            "orders_28d": orders_28d,
            "click_through_rate": ctr,
            "ctr_median": median,
        })

    candidates.sort(key=lambda x: (-x["stock"], x["click_through_rate"]))
    candidates = candidates[:limit]

    _log(f"\n[4/4] 写入队列 ({len(candidates)} 个)...")
    n = 0
    for item in candidates:
        conn.execute(
            """INSERT INTO deactivate_queue (
                product_id, shop_cipher, region, product_name, image_url, seller_sku,
                stock, orders_90d, orders_28d, click_through_rate, ctr_median,
                status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            ON CONFLICT(product_id, shop_cipher) DO UPDATE SET
                region=excluded.region,
                product_name=excluded.product_name,
                image_url=excluded.image_url,
                seller_sku=excluded.seller_sku,
                stock=excluded.stock,
                orders_90d=excluded.orders_90d,
                orders_28d=excluded.orders_28d,
                click_through_rate=excluded.click_through_rate,
                ctr_median=excluded.ctr_median,
                status='pending',
                error=NULL,
                created_at=excluded.created_at""",
            (
                item["product_id"],
                item["shop_cipher"],
                item["region"],
                item["product_name"],
                item["image_url"],
                item["seller_sku"],
                item["stock"],
                item["orders_90d"],
                item["orders_28d"],
                item["click_through_rate"],
                item["ctr_median"],
                now,
            ),
        )
        n += 1

    conn.commit()
    conn.close()
    if not quiet:
        print(f"  ✅ 已写入 {n} 条下架候选")
    return n


def load_queue(status: str | None = "pending") -> list[dict]:
    init_db()
    conn = connect()
    _migrate(conn)
    sql = "SELECT * FROM deactivate_queue"
    params: list = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY stock DESC, click_through_rate ASC"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def push_deactivate(token: str, cipher: str, product_ids: list[str]) -> tuple[bool, str]:
    if not product_ids:
        return False, "product_ids 为空"
    r = api_post(
        DEACTIVATE_PATH,
        token,
        {"shop_cipher": cipher},
        {"product_ids": product_ids},
    )
    if r.get("code") == 0:
        return True, ""
    return False, r.get("message", str(r))[:200]


def push_approved(ids: list[int] | None = None) -> dict:
    init_db()
    conn = connect()
    _migrate(conn)
    if ids:
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT * FROM deactivate_queue WHERE id IN ({placeholders}) AND status = 'pending'",
            ids,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM deactivate_queue WHERE status = 'pending'"
        ).fetchall()
    conn.close()

    cfg = _deactivate_cfg()
    token = auth.access_token()
    ok = fail = skip = 0
    errors: list[str] = []
    now = int(time.time())

    by_shop: dict[str, list] = {}
    for row in rows:
        by_shop.setdefault(row["shop_cipher"], []).append(row)

    for cipher, group in by_shop.items():
        batch_size = cfg["batch_size"]
        for i in range(0, len(group), batch_size):
            batch = group[i : i + batch_size]
            pids = [r["product_id"] for r in batch]
            success, err = push_deactivate(token, cipher, pids)
            conn = connect()
            try:
                for row in batch:
                    if success:
                        conn.execute(
                            """UPDATE deactivate_queue SET status = 'pushed', pushed_at = ?, error = NULL
                               WHERE id = ?""",
                            (now, row["id"]),
                        )
                        conn.execute(
                            """UPDATE products SET status = 'DEACTIVATED', updated_at = ?
                               WHERE product_id = ? AND shop_cipher = ?""",
                            (now, row["product_id"], cipher),
                        )
                        ok += 1
                    else:
                        conn.execute(
                            "UPDATE deactivate_queue SET status = 'failed', error = ? WHERE id = ?",
                            (err, row["id"]),
                        )
                        fail += 1
                        errors.append(f"{row['product_id']}: {err}")
                conn.commit()
            finally:
                conn.close()
            time.sleep(cfg["push_delay_sec"])

    return {"ok": ok, "fail": fail, "skip": skip, "errors": errors}
